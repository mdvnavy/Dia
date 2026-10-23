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


# --- route table (lockdown v2: "No use without signing in") -------------------
#
# Every functional route is listed here with gated=True and is refused with
# 401 {"error": "sign_in_required"} before any work when there is no valid
# signed session. Only the sign-in endpoints and read-only pages are public.
# tests/test_sign_in_gate.py fails if a route is added outside this table or
# is added ungated without being on its public allow-list.
GET_ROUTES: dict[str, tuple[str, bool]] = {
    "/healthz": ("handle_health", False),
    "/health": ("handle_health", False),
    "/api/config": ("handle_config", False),
    "/api/auth/config": ("handle_auth_config", False),
    "/api/agent/status": ("handle_agent_status", False),
    "/api/sample": ("handle_sample", True),
    "/api/usage": ("handle_usage", True),
}
POST_ROUTES: dict[str, tuple[str, bool]] = {
    "/api/auth/session": ("handle_auth_session", False),
    "/api/auth/logout": ("handle_auth_logout", False),
    "/api/process": ("handle_process", True),
    "/api/agent": ("handle_agent", True),
    "/api/extract": ("handle_extract", True),
    "/api/pdf": ("handle_pdf", True),
}


class ClientDiscoveryHandler(BaseHTTPRequestHandler):
    server_version = "DIADiscoveryIntakeAgent/0.1"
    # Socket timeout so a client that claims a body but never sends it cannot
    # pin a worker thread forever.
    timeout = 10

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in GET_ROUTES:
            self._dispatch(GET_ROUTES[path])
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
        if path in POST_ROUTES:
            self._dispatch(POST_ROUTES[path])
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _dispatch(self, route: tuple[str, bool]) -> None:
        """Run a route's handler; gated routes need a valid signed session."""
        from agent_auth import (
            ASK_DIA_RETRY_AFTER_SECONDS,
            ASK_DIA_UNAVAILABLE_MESSAGE,
            SIGN_IN_MESSAGE,
            SIGN_IN_REQUIRED,
            ask_dia_enabled,
            request_identity,
        )

        handler_name, gated = route
        self.identity = None
        if handler_name == "handle_agent" and not ask_dia_enabled():
            # Kill switch first, before sign-in, the body, quota or Gemini,
            # for owners too, so flipping ASK_DIA_ENABLED stops all spend.
            self.close_connection = True
            self.send_json(
                {"enabled": False, "error": ASK_DIA_UNAVAILABLE_MESSAGE},
                HTTPStatus.SERVICE_UNAVAILABLE,
                extra_headers={"Retry-After": str(ASK_DIA_RETRY_AFTER_SECONDS)},
            )
            return
        if gated:
            self.identity = request_identity(self.headers)
            if self.identity is None:
                # Don't read the body: drop the connection after replying.
                self.close_connection = True
                self.send_json(
                    {"error": SIGN_IN_REQUIRED, "message": SIGN_IN_MESSAGE},
                    HTTPStatus.UNAUTHORIZED,
                    extra_headers={"Cache-Control": "no-store"},
                )
                return
        getattr(self, handler_name)()

    def handle_health(self) -> None:
        self.send_json({"status": "ok"})

    def handle_config(self) -> None:
        self.send_json(public_config())

    def handle_sample(self) -> None:
        self.send_json({"questionnaire": read_sample_questionnaire()})

    # --- quota helpers (gated routes only; self.identity is set) -------------

    def _usage(self) -> dict[str, object]:
        from agent_quota import get_quota

        identity = self.identity
        if identity.uncapped:
            return {"signed_in": True, "owner": True, "unlimited": True}
        return {"signed_in": True, "owner": False, "unlimited": False, **get_quota().usage(identity.sub)}

    def _burst_ok(self, limiter) -> bool:
        """Per-user burst guard; owners skip it. Sends the 429 when tripped."""
        if self.identity.uncapped:
            return True
        allowed, retry_after = limiter.check(self.identity.sub)
        if not allowed:
            self.send_json(
                {"error": "burst_limited", "message": "That's a lot at once. Give it a few seconds and try again."},
                HTTPStatus.TOO_MANY_REQUESTS,
                extra_headers={"Retry-After": str(retry_after)},
            )
        return allowed

    def _charge(self, bucket: str) -> bool:
        """Charge one unit before the work. Sends the 429/503 and returns False when refused."""
        from agent_quota import QuotaUnavailable, get_quota, limit_message

        if self.identity.uncapped:
            return True
        quota = get_quota()
        try:
            result = quota.consume(bucket, self.identity.sub)
        except QuotaUnavailable:
            self.send_json(
                {"error": "quota_unavailable", "message": "DIA couldn't check today's allowance. Try again in a moment."},
                HTTPStatus.SERVICE_UNAVAILABLE,
                extra_headers={"Retry-After": "30"},
            )
            return False
        if not result.allowed:
            self.send_json(
                {
                    "error": "daily_limit",
                    "bucket": bucket,
                    "message": limit_message(bucket, result.state.limit),
                    "usage": self._usage(),
                },
                HTTPStatus.TOO_MANY_REQUESTS,
                extra_headers={"Retry-After": str(result.retry_after(quota.now()))},
            )
            return False
        return True

    def _refund(self, bucket: str) -> None:
        from agent_quota import get_quota

        if not self.identity.uncapped:
            get_quota().refund(bucket, self.identity.sub)

    def handle_usage(self) -> None:
        self.send_json(self._usage(), extra_headers={"Cache-Control": "no-store"})

    def handle_process(self) -> None:
        from agent_auth import generation_burst
        from agent_quota import GENERATIONS

        if not self._burst_ok(generation_burst):
            return
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        questionnaire = str(payload.get("questionnaire", ""))
        if not questionnaire.strip():
            self.send_json({"error": "questionnaire is required"}, HTTPStatus.BAD_REQUEST)
            return
        if not self._charge(GENERATIONS):
            return
        try:
            response = build_intake_response(questionnaire)
        except ValueError as error:
            self._refund(GENERATIONS)
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception:
            self._refund(GENERATIONS)
            raise
        response["usage"] = self._usage()

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
        from agent_auth import utility_burst
        from client_discovery.extract import MAX_UPLOAD_BYTES, ExtractionError, extract_text

        if not self._burst_ok(utility_burst):
            return
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
        """Render the formatted intake report. Body: {result} or {questionnaire}, optional date.

        Re-rendering an existing run ({result}) is an export and is not
        charged. Building from a {questionnaire} re-runs the pipeline, so it
        costs one generation (otherwise it would be an uncapped /api/process).
        """
        from agent_auth import generation_burst, utility_burst
        from agent_quota import GENERATIONS
        from client_discovery.report import pdf_filename, render_pdf

        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        questionnaire = str(payload.get("questionnaire") or "")
        from_result = isinstance(payload.get("result"), dict)
        charged = False
        if from_result:
            if not self._burst_ok(utility_burst):
                return
        elif questionnaire.strip():
            if not self._burst_ok(generation_burst) or not self._charge(GENERATIONS):
                return
            charged = True
        else:
            self.send_json({"error": "questionnaire or result is required"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            result = payload["result"] if from_result else build_intake_response(questionnaire)
            report_date = payload.get("date")
            body = render_pdf(result, report_date=str(report_date) if report_date else None)
        except ValueError as error:
            if charged:
                self._refund(GENERATIONS)
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as error:  # noqa: BLE001
            if charged:
                self._refund(GENERATIONS)
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
        from agent_auth import request_identity

        identity = request_identity(self.headers)
        if identity is None:
            return {"signed_in": False, "email": None, "owner": False}
        return {
            "signed_in": True,
            "email": identity.email or None,
            "owner": identity.uncapped,
        }

    def handle_auth_config(self) -> None:
        from agent_auth import google_client_id, sign_in_enabled

        enabled = sign_in_enabled()
        self.send_json(
            {
                "enabled": enabled,
                "google_client_id": google_client_id() if enabled else None,
                **self._auth_state(),
            },
            extra_headers={"Cache-Control": "no-store"},
        )

    def handle_auth_session(self) -> None:
        from agent_auth import (
            SignInError,
            auth_burst,
            client_ip,
            hashed_ip,
            is_owner_identity,
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
        allowed, retry_after = auth_burst.check(
            hashed_ip(client_ip(self.headers, self.client_address[0]))
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
            # Tia sends the GIS credential as "Authorization: Bearer <id token>";
            # a JSON {"credential"} body is also accepted.
            credential = self.headers.get("Authorization", "") or str(payload.get("credential", ""))
            identity = verify_google_credential(credential)
        except SignInError as error:
            self.send_json(
                {"error": str(error), "status": "unauthorized", "authorized": False, "message": str(error)},
                HTTPStatus.UNAUTHORIZED,
            )
            return
        except (json.JSONDecodeError, ValueError):
            self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return
        self._queue_header(
            "Set-Cookie", session_cookie_header(make_session(identity), self._secure_request())
        )
        owner = is_owner_identity(identity)
        # Tia's /api/auth/session shape (status, authorized, message, user) plus
        # DIA's signed_in/email/owner. The ID token itself is never echoed.
        self.send_json(
            {
                "status": "authorized",
                "authorized": True,
                "message": "Signed in.",
                "user": {"sub": identity.sub, "email": identity.email, "name": identity.name},
                "signed_in": True,
                "email": identity.email,
                "owner": owner,
            },
            extra_headers={"Cache-Control": "no-store"},
        )

    def handle_auth_logout(self) -> None:
        from agent_auth import clear_session_cookie_header

        self._queue_header("Set-Cookie", clear_session_cookie_header(self._secure_request()))
        self.send_json({"signed_in": False, "email": None, "owner": False})

    def handle_agent_status(self) -> None:
        """Public: whether Ask DIA is on and configured, and who is signed in. No counts."""
        from agent_auth import ask_dia_enabled, configured_mode
        from agent_runtime import is_configured

        enabled = ask_dia_enabled()
        self.send_json(
            {
                "enabled": enabled,
                "configured": bool(enabled and is_configured()),
                "auth": configured_mode(),
                **self._auth_state(),
            },
            extra_headers={"Cache-Control": "no-store"},
        )

    def handle_agent(self) -> None:
        """Ask DIA. The kill switch and sign-in gate already ran in _dispatch."""
        from agent_auth import ask_burst
        from agent_quota import ASK
        from agent_runtime import AgentNotConfigured, is_configured, run_agent

        if not self._burst_ok(ask_burst):
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

        # Charge before the model call; refund if the call fails.
        if not self._charge(ASK):
            return
        try:
            reply = run_agent(message)
        except AgentNotConfigured as error:
            self._refund(ASK)
            self.send_json({"configured": False, "reply": str(error), "usage": self._usage()})
            return
        except Exception as error:  # noqa: BLE001 - surface agent errors to the demo UI
            self._refund(ASK)
            self.send_json(
                {"error": f"agent run failed: {error}", "usage": self._usage()},
                HTTPStatus.BAD_GATEWAY,
            )
            return

        self.send_json({"configured": True, "reply": reply, "usage": self._usage()})

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
