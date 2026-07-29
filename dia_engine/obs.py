from __future__ import annotations

import logging
import os

from client_discovery.config import load_config

logger = logging.getLogger(__name__)


def _resolve_obs_credentials() -> tuple[str, int, str | None] | None:
    config = load_config()
    
    host = os.environ.get("OBS_HOST")
    if host is None:
        host = config.get("SERVER_IP", "127.0.0.1")
        
    port_str = os.environ.get("OBS_PORT")
    if port_str is None:
        port = config.get("SERVER_PORT", 4455)
    else:
        try:
            port = int(port_str)
        except ValueError:
            port = config.get("SERVER_PORT", 4455)
            if not isinstance(port, int):
                try:
                    port = int(port)
                except ValueError:
                    port = 4455

    password = os.environ.get("OBS_PASSWORD")
    if password is None:
        password = config.get("SERVER_PASSWORD")

    if host == "" or os.environ.get("OBS_HOST") == "":
        return None

    if not (1 <= port <= 65535):
        return None
        
    return host, port, password


def trigger_obs_screenshot(file_path: str = "C:\\AI\\error_capture.png") -> str:
    creds = _resolve_obs_credentials()
    if not creds:
        logger.info("OBS credentials explicitly missing or empty. Skipping screenshot.")
        return "OBS Screenshot skipped: Missing credentials."
    
    host, port, password = creds

    try:
        import obsws_python
        logger.info(f"Connecting to OBS via obsws_python on {host}:{port}")
        client = obsws_python.ReqClient(host=host, port=port, password=password)
        try:
            client.save_source_screenshot(
                os.environ.get("OBS_SOURCE_NAME", "Display Capture"),
                "png",
                file_path,
                int(os.environ.get("OBS_SCREENSHOT_WIDTH", "1920")),
                int(os.environ.get("OBS_SCREENSHOT_HEIGHT", "1080")),
                -1,
            )
            logger.info(f"OBS screenshot saved to {file_path}")
            return f"OBS Screenshot saved to {file_path}"
        finally:
            pass
    except ImportError:
        try:
            import obswebsocket
            from obswebsocket import obsws, requests
            logger.info(f"Connecting to OBS via obswebsocket on {host}:{port}")
            client = obsws(host, port, password)
            client.connect()
            try:
                client.call(requests.SaveSourceScreenshot(sourceName="Display Capture", imageFormat="png", imageFilePath=file_path))
                logger.info(f"OBS screenshot saved to {file_path} via obswebsocket")
                return f"OBS Screenshot saved to {file_path}"
            finally:
                client.disconnect()
        except ImportError:
            logger.error("No compatible OBS WebSocket library found (neither obsws_python nor obswebsocket).")
            return "OBS Screenshot failed: No compatible OBS WebSocket library found."
        except Exception as e:
            logger.error(f"OBS Screenshot failed under obswebsocket: {e}")
            return f"OBS Screenshot failed: {e}"
    except Exception as e:
        logger.error(f"OBS Screenshot failed under obsws_python: {e}")
        return f"OBS Screenshot failed: {e}"


def save_obs_replay_buffer() -> str:
    creds = _resolve_obs_credentials()
    if not creds:
        logger.info("OBS credentials explicitly missing or empty. Skipping replay buffer.")
        return "OBS Replay Buffer skipped: Missing credentials."
    
    host, port, password = creds

    try:
        import obsws_python
        logger.info(f"Connecting to OBS via obsws_python on {host}:{port}")
        client = obsws_python.ReqClient(host=host, port=port, password=password)
        try:
            client.save_replay_buffer()
            logger.info("OBS replay buffer saved.")
            return "OBS Replay Buffer saved."
        finally:
            pass
    except ImportError:
        try:
            import obswebsocket
            from obswebsocket import obsws, requests
            logger.info(f"Connecting to OBS via obswebsocket on {host}:{port}")
            client = obsws(host, port, password)
            client.connect()
            try:
                client.call(requests.SaveReplayBuffer())
                logger.info("OBS replay buffer saved via obswebsocket.")
                return "OBS Replay Buffer saved."
            finally:
                client.disconnect()
        except ImportError:
            logger.error("No compatible OBS WebSocket library found (neither obsws_python nor obswebsocket).")
            return "OBS Replay Buffer failed: No compatible OBS WebSocket library found."
        except Exception as e:
            logger.error(f"OBS Replay Buffer failed under obswebsocket: {e}")
            return f"OBS Replay Buffer failed: {e}"
    except Exception as e:
        logger.error(f"OBS Replay Buffer failed under obsws_python: {e}")
        return f"OBS Replay Buffer failed: {e}"
