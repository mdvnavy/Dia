from __future__ import annotations

from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from agent_auth import RateLimiter

from client_discovery.core import (
    generate_documents,
    parse_questionnaire_markdown,
    score_opportunity,
    validate_intake,
)

logger = logging.getLogger(__name__)


def _obs_capture_enabled() -> bool:
    """OBS capture is a local recording aid; enable it only via OBS_CAPTURE."""
    return os.environ.get("OBS_CAPTURE", "").strip().lower() in {"1", "true", "yes", "on"}


BASE_DIR = Path(__file__).resolve().parent
# Reject request bodies above this size before reading them, so a huge (or
# bogus) Content-Length cannot hang the worker thread or exhaust memory.
MAX_REQUEST_BYTES = 10 * 1024 * 1024
PUBLIC_FILES = {
    "/": BASE_DIR / "templates" / "index.html",
    "/static/app.js": BASE_DIR / "static" / "app.js",
    "/static/google-signin.js": BASE_DIR / "static" / "google-signin.js",
    "/static/style.css": BASE_DIR / "static" / "style.css",
    "/static/cabinet-fan.css": BASE_DIR / "static" / "cabinet-fan.css",
    "/static/pixel-hover.css": BASE_DIR / "static" / "pixel-hover.css",
    "/static/fanout-pixel.css": BASE_DIR / "static" / "fanout-pixel.css",
    "/static/composer-icons.css": BASE_DIR / "static" / "composer-icons.css",
    "/static/composer.css": BASE_DIR / "static" / "composer.css",
    "/static/vendor/jszip.min.js": BASE_DIR / "static" / "vendor" / "jszip.min.js",
}
# Runtime sample lives outside tests/ so it ships in the Cloud Run image even
# when test files are excluded by .dockerignore.
SAMPLE_PATHS = (
    BASE_DIR / "samples" / "sample_questionnaire.md",
    BASE_DIR / "tests" / "fixtures" / "complete_questionnaire.md",
)


ICON_DIR = BASE_DIR / "static" / "icons"
ICON_PATH = re.compile(r"^/static/icons/((?:[a-z0-9-]{1,32}/)?[a-z0-9][a-z0-9@-]{0,63}\.(?:svg|png|webp|gif))$")


# Ikenna's PayPal payment link (a plain GET opens the payment page). DIA_DONATE_URL overrides it; setting DIA_DONATE_URL to an empty value hides Donate.
DEFAULT_DONATE_URL = "https://www.paypal.com/ncp/payment/5UFVJBMWWKRBY"


def donate_url() -> str | None:
    """Single source for the Donate link: DIA_DONATE_URL (https only), defaulting to DEFAULT_DONATE_URL.
    Empty or invalid = the item is hidden."""
    raw = os.environ.get("DIA_DONATE_URL", DEFAULT_DONATE_URL).strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc or any(ch in raw for ch in "\"'<> \t\r\n"):
        logger.warning("DIA_DONATE_URL ignored: it must be an absolute https:// URL")
        return None
    return raw


def public_config() -> dict[str, object]:
    """Client-visible settings (no secrets)."""
    return {"donateUrl": donate_url()}


def icon_file(path: str) -> Path | None:
    """Swappable UI icon sets: /static/icons/<set>/<name>.svg|png (no traversal)."""
    match = ICON_PATH.match(path)
    if not match:
        return None
    candidate = ICON_DIR / match.group(1)
    return candidate if candidate.is_file() else None


def read_sample_questionnaire() -> str:
    for candidate in SAMPLE_PATHS:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError("no sample questionnaire is bundled with the app")


def build_intake_response(questionnaire: str) -> dict[str, object]:
    if not questionnaire.strip():
        raise ValueError("questionnaire is required")

    intake = parse_questionnaire_markdown(questionnaire)
    issues = validate_intake(intake)
    score = score_opportunity(intake)
    documents = generate_documents(intake, score)

    return {
        "intake": asdict(intake),
        "issues": [asdict(issue) for issue in issues],
        "score": asdict(score),
        "documents": documents,
    }


# Separate burst guard for /api/agent/status so page loads don't eat the
# agent's burst allowance; when exceeded, status just omits the quota.
status_rate_limiter = RateLimiter()
# Sign-in attempts get their own burst guard (each one may fetch Google certs).
auth_rate_limiter = RateLimiter()


class ClientDiscoveryHandler(BaseHTTPRequestHandler):
    server_version = "DIADiscoveryIntakeAgent/0.1"
    # Socket timeout so a client that claims a body but never sends it cannot
    # pin a worker thread forever.
    timeout = 10

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/healthz", "/health"):
            self.send_json({"status": "ok"})
            return
        if path == "/api/sample":
            self.send_json({"questionnaire": read_sample_questionnaire()})
            return
        if path == "/api/config":
            self.send_json(public_config())
            return
        if path == "/api/agent/status":
            self.handle_agent_status()
            return
        if path == "/api/auth/config":
            self.handle_auth_config()
            return

        file_path = PUBLIC_FILES.get(path)
        if file_path is None:
            file_path = icon_file(path)
        if file_path is None:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        self.send_file(file_path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/auth/session":
            self.handle_auth_session()
            return
        if path == "/api/auth/logout":
            self.handle_auth_logout()
            return
        if path == "/api/process":
            self.handle_process()
            return
        if path == "/api/agent":
            self.handle_agent()
            return
        if path == "/api/extract":
            self.handle_extract()
            return
        if path == "/api/pdf":
            self.handle_pdf()
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def handle_process(self) -> None:
        try:
            payload = self.read_json()
            response = build_intake_response(str(payload.get("questionnaire", "")))
        except json.JSONDecodeError:
            self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return

        self.send_json(response)

        # Record the completed run in client memory AFTER the response is on
        # the wire, so a slow or unreachable Sheet can never delay an intake.
        # No-op unless MEMORY_SHEET_ID is set; never raises.
        self._record_engagement(response)

        # OBS capture is a LOCAL-ONLY convenience for recording demo videos.
        # It is off by default (so the deployed Cloud Run service never pokes a
        # non-existent OBS) and only runs when OBS_CAPTURE is explicitly enabled
        # locally and the intake surfaced issues. It never affects the response.
        if response.get("issues") and _obs_capture_enabled():
            self._trigger_obs_capture()

    def handle_extract(self) -> None:
        """Raw file body in, questionnaire text out. Filename via X-Filename or ?name=."""
        from client_discovery.extract import MAX_UPLOAD_BYTES, ExtractionError, extract_text

        query = parse_qs(urlparse(self.path).query)
        filename = unquote(self.headers.get("X-Filename", "")) or (query.get("name") or [""])[0]
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = -1
        if content_length < 0:
            self.send_json({"error": "invalid content length"}, HTTPStatus.BAD_REQUEST)
            return
        if content_length > MAX_UPLOAD_BYTES:
            self.close_connection = True
            self.send_json(
                {"error": "That file is over 5 MB. Try a smaller export of the questionnaire."},
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return
        data = self.rfile.read(content_length)
        try:
            text = extract_text(filename, data)
        except ExtractionError as error:
            self.send_json({"error": str(error)}, HTTPStatus(error.status))
            return
        except Exception as error:  # noqa: BLE001 - never 500 with a stack trace
            logger.warning("extraction failed for %s: %s", filename, error)
            self.send_json({"error": "That file couldn't be read. Try saving it as .txt or .docx."}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"filename": filename, "questionnaire": text, "characters": len(text)})

    def handle_pdf(self) -> None:
        """Render the formatted intake report. Body: {questionnaire} or {result}, optional date."""
        from client_discovery.report import pdf_filename, render_pdf

        try:
            payload = self.read_json()
            questionnaire = str(payload.get("questionnaire") or "")
            if questionnaire.strip():
                result = build_intake_response(questionnaire)
            elif isinstance(payload.get("result"), dict):
                result = payload["result"]
            else:
                raise ValueError("questionnaire or result is required")
            report_date = payload.get("date")
            body = render_pdf(result, report_date=str(report_date) if report_date else None)
        except json.JSONDecodeError:
            self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as error:  # noqa: BLE001
            logger.warning("pdf render failed: %s", error)
            self.send_json({"error": "PDF rendering failed on the server."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'attachment; filename="{pdf_filename(result)}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record_engagement(self, response: dict[str, object]) -> None:
        try:
            from client_discovery import memory
            from client_discovery.models import ClientIntake, OpportunityScore

            if not memory.is_enabled():
                return
            memory.record_engagement(
                ClientIntake(**response["intake"]),
                OpportunityScore(**response["score"]),
                source="/api/process",
                documents=sorted(response.get("documents", {})),
            )
        except Exception as error:  # noqa: BLE001 - memory must never break intake
            logger.warning("client memory recording skipped: %s", error)

    def _trigger_obs_capture(self) -> None:
        try:
            from client_discovery.core import (
                save_obs_replay_buffer,
                trigger_obs_screenshot,
            )

            trigger_obs_screenshot()
            save_obs_replay_buffer()
        except Exception as error:  # noqa: BLE001 - capture must never break intake
            logger.warning("OBS capture skipped: %s", error)

    # --- live agent ---------------------------------------------------------

    def _secure_request(self) -> bool:
        return bool(os.environ.get("K_SERVICE")) or (
            self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        )

    def _queue_header(self, name: str, value: str) -> None:
        pending = getattr(self, "_pending_headers", None)
        if pending is None:
            pending = self._pending_headers = []
        pending.append((name, value))

    def _auth_state(self) -> dict[str, object]:
        from agent_auth import is_owner_email, session_email

        email = session_email(self.headers)
        return {"signed_in": bool(email), "email": email, "owner": is_owner_email(email)}

    def handle_auth_config(self) -> None:
        from agent_auth import google_client_id, sign_in_enabled

        enabled = sign_in_enabled()
        self.send_json(
            {
                "enabled": enabled,
                "google_client_id": google_client_id() if enabled else None,
                **self._auth_state(),
            }
        )

    def handle_auth_session(self) -> None:
        from agent_auth import (
            SignInError,
            client_ip,
            is_owner_email,
            make_session,
            session_cookie_header,
            sign_in_enabled,
            verify_google_credential,
        )

        if not sign_in_enabled():
            self.send_json({"error": "Google sign-in is not configured."}, HTTPStatus.NOT_FOUND)
            return
        # JSON only: a cross-site <form> can't send application/json without a
        # CORS preflight, so this also blocks login CSRF.
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self.send_json({"error": "json body required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        allowed, retry_after = auth_rate_limiter.check(
            client_ip(self.headers, self.client_address[0])
        )
        if not allowed:
            self.send_json(
                {"error": "Too many sign-in attempts. Give it a minute and try again."},
                HTTPStatus.TOO_MANY_REQUESTS,
                extra_headers={"Retry-After": str(retry_after)},
            )
            return
        try:
            payload = self.read_json()
            identity = verify_google_credential(str(payload.get("credential", "")))
        except SignInError as error:
            self.send_json({"error": str(error)}, HTTPStatus.UNAUTHORIZED)
            return
        except (json.JSONDecodeError, ValueError):
            self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return
        self._queue_header(
            "Set-Cookie", session_cookie_header(make_session(identity), self._secure_request())
        )
        self.send_json(
            {"signed_in": True, "email": identity.email, "owner": is_owner_email(identity.email)}
        )

    def handle_auth_logout(self) -> None:
        from agent_auth import clear_session_cookie_header

        self._queue_header("Set-Cookie", clear_session_cookie_header(self._secure_request()))
        self.send_json({"signed_in": False, "email": None, "owner": False})

    def _visitor_keys(self) -> list[str]:
        """Quota buckets for this request; mints the visitor cookie if needed."""
        from agent_auth import client_ip
        from agent_quota import (
            new_visitor_id,
            parse_cookie,
            valid_visitor_id,
            visitor_cookie_header,
            visitor_keys,
        )

        visitor_id = parse_cookie(self.headers.get("Cookie"))
        if not valid_visitor_id(visitor_id):
            visitor_id = new_visitor_id()
            self._queue_header(
                "Set-Cookie", visitor_cookie_header(visitor_id, self._secure_request())
            )
        ip = client_ip(self.headers, self.client_address[0])
        return visitor_keys(ip, visitor_id)

    def handle_agent_status(self) -> None:
        from agent_auth import ask_dia_enabled, client_ip, configured_mode
        from agent_quota import get_quota
        from agent_runtime import is_configured

        payload: dict[str, object] = {
            "enabled": ask_dia_enabled(),
            "configured": is_configured(),
            "auth": configured_mode(),
            **self._auth_state(),
        }
        if payload["owner"]:
            payload["quota"] = None
        else:
            keys = self._visitor_keys()
            allowed, _ = status_rate_limiter.check(
                client_ip(self.headers, self.client_address[0])
            )
            if allowed:
                quota = get_quota()
                payload["quota"] = quota.peek(keys).as_json()
                payload["quota_store"] = quota.store_name
        self.send_json(payload)

    def handle_agent(self) -> None:
        from agent_auth import (
            ASK_DIA_RETRY_AFTER_SECONDS,
            ASK_DIA_UNAVAILABLE_MESSAGE,
            agent_rate_limiter,
            ask_dia_enabled,
            client_ip,
            is_owner,
        )
        from agent_quota import get_quota, limit_message
        from agent_runtime import AgentNotConfigured, is_configured, run_agent

        if not ask_dia_enabled():
            # Kill switch: checked before anything else (body, quota, Gemini),
            # for owners too, so flipping ASK_DIA_ENABLED stops all spend.
            self.send_json(
                {"enabled": False, "error": ASK_DIA_UNAVAILABLE_MESSAGE},
                HTTPStatus.SERVICE_UNAVAILABLE,
                extra_headers={"Retry-After": str(ASK_DIA_RETRY_AFTER_SECONDS)},
            )
            return

        owner = is_owner(self.headers)
        if not owner:
            # Burst guard first, so scripted clients are throttled before
            # anything else (including the quota store). Owners skip it.
            allowed, retry_after = agent_rate_limiter.check(
                client_ip(self.headers, self.client_address[0])
            )
            if not allowed:
                self.send_json(
                    {"error": "Too many questions at once. Give it a minute and try again."},
                    HTTPStatus.TOO_MANY_REQUESTS,
                    extra_headers={"Retry-After": str(retry_after)},
                )
                return

        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return

        message = str(payload.get("message", "")).strip()
        if not message:
            self.send_json({"error": "message is required"}, HTTPStatus.BAD_REQUEST)
            return

        if not is_configured():
            # Nothing is spent, so nothing is charged against the quota.
            self.send_json(
                {
                    "configured": False,
                    "reply": (
                        "Live Gemini agent is not configured. Set GEMINI_API_KEY "
                        "(or GOOGLE_API_KEY) to enable it. The deterministic intake "
                        "pipeline above still works without an API key."
                    ),
                }
            )
            return

        quota_info: dict[str, object] | None = None
        if not owner:
            quota = get_quota()
            result = quota.consume(self._visitor_keys())
            if not result.allowed:
                self.send_json(
                    {
                        "error": limit_message(result.limit),
                        "quota": result.as_json(),
                    },
                    HTTPStatus.TOO_MANY_REQUESTS,
                    extra_headers={"Retry-After": str(result.retry_after(quota.now()))},
                )
                return
            quota_info = result.as_json()

        try:
            reply = run_agent(message)
        except AgentNotConfigured as error:
            self.send_json({"configured": False, "reply": str(error)})
            return
        except Exception as error:  # noqa: BLE001 - surface agent errors to the demo UI
            self.send_json(
                {"error": f"agent run failed: {error}"},
                HTTPStatus.BAD_GATEWAY,
            )
            return

        response: dict[str, object] = {"configured": True, "reply": reply}
        if owner:
            response["owner"] = True
        else:
            response["quota"] = quota_info
        self.send_json(response)

    def read_json(self) -> dict[str, object]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length < 0:
            raise ValueError("invalid content length")
        if content_length > MAX_REQUEST_BYTES:
            raise ValueError("request body too large")
        raw_body = self.rfile.read(content_length).decode("utf-8")
        if not raw_body:
            return {}
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("json object is required")
        return payload

    def send_json(
        self,
        payload: dict[str, object],
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        headers = [*(getattr(self, "_pending_headers", None) or []), *(extra_headers or {}).items()]
        self._pending_headers = []
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, file_path: Path) -> None:
        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "text/plain"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_server() -> None:
    # Cloud Run injects PORT and requires binding to 0.0.0.0; both defaults are
    # container-friendly and work for local development too.
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), ClientDiscoveryHandler)
    print(f"DIA demo running at http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("KeyboardInterrupt received, shutting down...")
    finally:
        httpd.server_close()
        print("Server socket closed")


if __name__ == "__main__":
    run_server()
