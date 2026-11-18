"""Website research mode (feature spec v1): fetch guards, crawl, sourced facts, documents.

No network: every fetch goes through an injected fake fetcher or a fake opener/resolver.
"""
from __future__ import annotations

from email.message import Message

import pytest

from client_discovery.core import parse_questionnaire_markdown
from client_discovery.inputs import INPUTS
from client_discovery.research import (
    FetchResult,
    ResearchError,
    _GuardedRedirectHandler,
    build_research_response,
    fetch_url,
    research_website,
)

BASE = "https://www.acmeplumbing.example"

HOME_HTML = """<!doctype html>
<html><head>
<title>Acme Plumbing | Toronto Plumbers</title>
<meta name="description" content="Acme Plumbing provides emergency plumbing across the GTA.">
</head><body>
<nav>
  <a href="/services">Services</a>
  <a href="/about">About us</a>
  <a href="/pricing">Pricing</a>
  <a href="/contact">Contact</a>
</nav>
<header>
  <h1>Acme Plumbing</h1>
  <p>Over 25 years of experience, 4.9 star rated by 1,200 customers.</p>
  <p>We offer 24/7 emergency plumbing. Serving Toronto and the GTA.</p>
  <a href="/contact">Get a free quote</a>
  <a href="tel:+14165550123">(416) 555-0123</a>
</header>
<section>
  <h2>Drain Cleaning</h2>
  <h2>Water Heater Installation</h2>
</section>
<script src="https://www.googletagmanager.com/gtm.js"></script>
</body></html>"""

SERVICES_HTML = """<!doctype html>
<html><head><title>Services | Acme Plumbing</title></head><body>
<h1>Our Services</h1>
<h2>Drain Cleaning</h2><p>Stubborn drains cleared the same day.</p>
<h2>Water Heater Installation</h2><p>Tank and tankless installs.</p>
<h2>Bathroom Renovations</h2><p>Full bathroom remodels, licensed and insured.</p>
</body></html>"""

ABOUT_HTML = """<!doctype html>
<html><head><title>About | Acme Plumbing</title></head><body>
<h1>About Acme</h1>
<p>We are a family plumbing company located in Toronto, ON.</p>
</body></html>"""

PRICING_HTML = """<!doctype html>
<html><head><title>Pricing | Acme Plumbing</title></head><body>
<h1>Pricing</h1>
<p>Drain cleaning from $120. Every job is guaranteed for two years.</p>
</body></html>"""

CONTACT_HTML = """<!doctype html>
<html><head><title>Contact | Acme Plumbing</title></head><body>
<h1>Contact us</h1>
<form action="/send" method="post"><input name="phone"></form>
<p>Email <a href="mailto:hello@acmeplumbing.example">hello@acmeplumbing.example</a> any time.</p>
</body></html>"""

SITE = {
    f"{BASE}/": HOME_HTML,
    f"{BASE}/services": SERVICES_HTML,
    f"{BASE}/about": ABOUT_HTML,
    f"{BASE}/pricing": PRICING_HTML,
    f"{BASE}/contact": CONTACT_HTML,
}


def fake_fetcher(pages: dict[str, str]):
    def fetch(url: str) -> FetchResult:
        if url in pages:
            return FetchResult(url=url, status=200, content_type="text/html", body=pages[url].encode())
        raise ResearchError("The page answered with an error (HTTP 404).")
    return fetch


# --- fetch guards (SSRF) -------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, url: str, body: bytes) -> None:
        self._url = url
        self._body = body
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return 200

    def read(self, _limit: int = -1) -> bytes:
        return self._body


class _FakeOpener:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def open(self, request, timeout: float = 0) -> _FakeResponse:
        self.opened.append(request.full_url)
        return _FakeResponse(request.full_url, b"<html><title>Ok</title></html>")


def test_http_urls_are_rejected():
    with pytest.raises(ResearchError, match="https"):
        fetch_url("http://example.com/")


def test_private_ip_resolution_is_rejected_before_any_fetch():
    opener = _FakeOpener()
    with pytest.raises(ResearchError, match="non-public"):
        fetch_url("https://example.com/", opener=opener, resolver=lambda host: ["10.0.0.8"])
    assert opener.opened == []


def test_link_local_literal_is_rejected():
    with pytest.raises(ResearchError, match="non-public"):
        fetch_url("https://169.254.169.254/latest/meta-data", resolver=lambda host: ["93.184.216.34"])


def test_redirect_to_a_private_address_is_rejected():
    handler = _GuardedRedirectHandler(resolver=lambda host: ["127.0.0.1"])
    with pytest.raises(ResearchError, match="non-public"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://internal.example/")


def test_public_fetch_goes_through():
    result = fetch_url("https://example.com/", opener=_FakeOpener(), resolver=lambda host: ["93.184.216.34"])
    assert result.status == 200
    assert b"Ok" in result.body


# --- crawl + facts -------------------------------------------------------------------------------

def test_page_cap_is_eight():
    links = "".join(f'<a href="/services-{i}">Services {i}</a>' for i in range(12))
    home = f"<html><head><title>Many Pages Co</title></head><body><h1>Many Pages Co</h1>{links}</body></html>"
    pages = {f"{BASE}/": home}
    for i in range(12):
        pages[f"{BASE}/services-{i}"] = ("<html><head><title>Page</title></head><body>"
                                         "<h1>Service page</h1><p>Work done well.</p></body></html>")
    result = research_website(f"{BASE}/", fetcher=fake_fetcher(pages))
    assert len(result.pages_read) == 8


def test_facts_are_extracted_with_sources():
    result = research_website(f"{BASE}/", fetcher=fake_fetcher(SITE))
    facts = result.facts
    assert facts.company_name is not None and facts.company_name.value == "Acme Plumbing"
    assert facts.industry is not None and facts.industry.value == "Plumbing"
    service_names = [f.value for f in facts.services]
    assert "Drain Cleaning" in service_names
    assert "Water Heater Installation" in service_names
    assert "Bathroom Renovations" in service_names
    assert all(f.source.startswith(BASE) for f in facts.services)
    assert any("416" in f.value for f in facts.phones)
    assert any(f.value == "hello@acmeplumbing.example" for f in facts.emails)
    assert facts.has_contact_form
    assert facts.proof_points, "the 25-years / 4.9-star sentence is a proof point"
    assert all(f.source for f in facts.proof_points)


def test_draft_questionnaire_parses_into_the_existing_pipeline():
    result = research_website(f"{BASE}/", fetcher=fake_fetcher(SITE))
    intake = parse_questionnaire_markdown(result.questionnaire)
    assert intake.company_name == "Acme Plumbing"
    assert intake.website == f"{BASE}/"
    assert "Drain Cleaning" in intake.workflow_tasks
    assert intake.hourly_cost == "" and intake.success_metric == ""


def test_brief_cites_sources_and_names_openings():
    result = research_website(f"{BASE}/", fetcher=fake_fetcher(SITE))
    brief = result.documents["research-brief"]
    assert "Drain Cleaning" in brief
    assert "(source:" in brief
    assert "no page for it" in brief  # Drain Cleaning is named but has no page of its own


def test_questions_are_gap_driven_and_use_the_inputs_wording():
    result = research_website(f"{BASE}/", fetcher=fake_fetcher(SITE))
    questions = result.documents["discovery-questions"]
    for key in ("hours", "hourly_cost", "success_metric", "people", "systems"):
        assert INPUTS[key][1] in questions, key
    assert "## 1. Confirm" in questions and "## 2. Learn" in questions and "## 3. Open" in questions


def test_unread_pages_are_recorded_not_guessed():
    pages = dict(SITE)
    del pages[f"{BASE}/contact"]
    result = research_website(f"{BASE}/", fetcher=fake_fetcher(pages))
    assert any(item["url"] == f"{BASE}/contact" for item in result.pages_unread)
    assert "could not read" in result.documents["research-brief"]


def test_response_shape_matches_process_conventions():
    response = build_research_response(f"{BASE}/", fetcher=fake_fetcher(SITE))
    assert response["intake"]["company_name"] == "Acme Plumbing"
    assert set(response["documents"]) == {"research-brief", "discovery-questions"}
    assert "price_range" not in response["score"]
    assert response["pages"]["read"][0] == f"{BASE}/"


# --- thin sites and non-sites --------------------------------------------------------------------

THIN_HTML = """<!doctype html>
<html><head><title>Bob's Bakery</title></head><body>
<h1>Bob's Bakery</h1>
<p>Fresh bread daily.</p>
</body></html>"""


def test_thin_one_page_site_carries_the_full_intake_as_questions():
    result = research_website("https://bobs.example/", fetcher=fake_fetcher({"https://bobs.example/": THIN_HTML}))
    assert result.pages_read == ["https://bobs.example/"]
    questions = result.documents["discovery-questions"]
    # inputs.py asks "people" only once tasks are listed; a thin site has none yet, so the
    # other four key inputs lead, followed by the missing basics.
    for key in ("hours", "hourly_cost", "success_metric", "systems"):
        assert INPUTS[key][1] in questions, key
    assert "budget range" in questions.lower()
    assert "final decision" in questions.lower()
    assert "single page" in result.documents["research-brief"]


def test_non_company_page_declines_cleanly():
    blank = "<html><head><title></title></head><body><p>hello world</p></body></html>"
    with pytest.raises(ResearchError, match="does not look like a company website"):
        research_website("https://blank.example/", fetcher=fake_fetcher({"https://blank.example/": blank}))


def test_missing_url_and_http_start_decline_cleanly():
    with pytest.raises(ResearchError, match="required"):
        research_website("", fetcher=fake_fetcher(SITE))
