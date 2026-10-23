"""Tray / cabinet / icon-style wiring: icon route, merged multi-file parse, static hooks."""
from __future__ import annotations

import http.client
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from app import ClientDiscoveryHandler, build_intake_response

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests/fixtures/complete_questionnaire.md").read_text(encoding="utf-8")
APP_JS = (ROOT / "static/app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "templates/index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    server.server_close()


def _get(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, response.getheader("Content-Type") or "", body


@pytest.mark.parametrize("style_dir", ["line", "zelda", "pixel", "pixel-dark"])
@pytest.mark.parametrize("icon", ["tray", "tray-active", "cabinet", "cabinet-open"])
def test_every_icon_set_ships_every_icon(base, style_dir, icon):
    status, content_type, body = _get(base, f"/static/icons/{style_dir}/{icon}.svg")
    assert status == 200 and "svg" in content_type and body.lstrip().startswith(b"<svg")


@pytest.mark.parametrize("path", ["/static/icons/../app.py", "/static/icons/%2e%2e/app.py", "/static/icons/a/b/c.svg", "/static/icons/x.py"])
def test_icon_route_refuses_traversal_and_other_types(base, path):
    assert _get(base, path)[0] == 404


@pytest.mark.parametrize("path", ["/static/cabinet-fan.css", "/static/fanout-pixel.css", "/static/pixel-hover.css", "/static/vendor/jszip.min.js"])
def test_new_static_assets_are_served(base, path):
    assert _get(base, path)[0] == 200


def test_merged_multi_file_drop_still_parses_and_later_values_win():
    # Same separator the client uses for MULTI_FILE_MODE = "merge".
    second = FIXTURE.replace("Northstar Studio", "Second File Co")
    merged = f"<!-- file: a.md -->\n{FIXTURE}\n\n---\n\n<!-- file: b.md -->\n{second}\n"
    result = build_intake_response(merged)
    assert result["intake"]["company_name"] in {"Northstar Studio", "Second File Co"}
    assert result["score"]["total_score"] == build_intake_response(FIXTURE)["score"]["total_score"]


def test_product_switches_are_single_constants():
    assert re.search(r'^const CABINET_CAP_POLICY = "drop-oldest";', APP_JS, re.M)
    assert re.search(r'^const MULTI_FILE_MODE = "merge";', APP_JS, re.M)
    assert re.search(r"^const CABINET_MAX = 15;", APP_JS, re.M)
    assert 'sessionStorage.getItem(CABINET_KEY' in APP_JS  # intakes: session only
    assert 'localStorage.setItem(ICON_STYLE_KEY' in APP_JS  # icon style: per device


def test_icon_style_setting_markup():
    assert "Help &amp; settings" in INDEX
    group = re.search(r'role="radiogroup"[^>]*', INDEX).group(0)
    assert 'aria-labelledby="iconStyleTitle"' in group
    for value in ("line", "zelda", "dia-pixel"):
        assert f'role="radio" class="icon-style-option" data-icon-style="{value}"' in INDEX
    assert 'data-icon-style="line" aria-checked="true" tabindex="0"' in INDEX
    for label in ("Icon style: Line", "Icon style: Zelda", "Icon style: Dia pixel"):
        assert label in APP_JS


def test_new_controls_have_labels():
    for control in ('id="trayButton"', 'id="cabinetButton"', 'id="cabinetClose"'):
        tag = re.search(rf"<button[^>]*{control}[^>]*>", INDEX).group(0)
        assert "aria-label=" in tag
    assert 'id="intakeError" class="intake-error" role="alert"' in INDEX


def test_cabinet_sits_at_far_right_of_composer_row():
    """Ikenna: cabinet mirrors the tray (tray far left, cabinet far right of the composer), not in the top bar."""
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    header = html[html.index("<header"):html.index("</header>")]
    assert 'id="cabinetButton"' not in header
    dock = html[html.index('<div class="dock-row">'):html.index('id="dropHint"')]
    assert dock.index('id="trayButton"') < dock.index('id="cabinetButton"')
    assert 'class="dock-icon cabinet-btn px-btn"' in dock
    assert 'id="cabinetCount" class="cabinet-badge"' in dock
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ".dock-row .cabinet { margin-left: auto; }" in css
    assert '.cabinet[data-drop="up"] .fan' in css
