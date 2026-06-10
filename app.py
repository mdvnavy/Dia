from __future__ import annotations

from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

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
PUBLIC_FILES = {
    "/": BASE_DIR / "templates" / "index.html",
    "/static/app.js": BASE_DIR / "static" / "app.js",
    "/static/style.css": BASE_DIR / "static" / "style.css",
}
# Runtime sample lives outside tests/ so it ships in the Cloud Run image even
# when test files are excluded by .dockerignore.
SAMPLE_PATHS = (
    BASE_DIR / "samples" / "sample_questionnaire.md",
    BASE_DIR / "tests" / "fixtures" / "complete_questionnaire.md",
)


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


class ClientDiscoveryHandler(BaseHTTPRequestHandler):
    server_version = "DIADiscoveryIntakeAgent/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/healthz", "/health"):
            self.send_json({"status": "ok"})
            return
        if path == "/api/sample":
            self.send_json({"questionnaire": read_sample_questionnaire()})
            return
        if path == "/api/agent/status":
            from agent_runtime import is_configured

            self.send_json({"configured": is_configured()})
            return

        file_path = PUBLIC_FILES.get(path)
        if file_path is None:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        self.send_file(file_path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/process":
            self.handle_process()
            return
        if path == "/api/agent":
            self.handle_agent()
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

        # OBS capture is a LOCAL-ONLY convenience for recording demo videos.
        # It is off by default (so the deployed Cloud Run service never pokes a
        # non-existent OBS) and only runs when OBS_CAPTURE is explicitly enabled
        # locally and the intake surfaced issues. It never affects the response.
        if response.get("issues") and _obs_capture_enabled():
            self._trigger_obs_capture()

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

    def handle_agent(self) -> None:
        from agent_runtime import AgentNotConfigured, is_configured, run_agent

        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return

        message = str(payload.get("message", "")).strip()
        if not message:
            self.send_json({"error": "message is required"}, HTTPStatus.BAD_REQUEST)
            return

        if not is_configured():
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

        self.send_json({"configured": True, "reply": reply})

    def read_json(self) -> dict[str, object]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        if not raw_body:
            return {}
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("json object is required")
        return payload

    def send_json(
        self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, file_path: Path) -> None:
        body = file_path.read_bytes()
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
    httpd.serve_forever()


if __name__ == "__main__":
    run_server()
