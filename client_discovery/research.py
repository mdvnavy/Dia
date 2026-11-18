"""Website research mode (feature spec v1, 2026-11-14): a company's website in, discovery homework out.

DIA normally starts from a filled questionnaire. This module is the research-first entry point for a
prospect the consultant has not met yet: it fetches the company's own website (homepage plus the
customer-facing pages: about, services, pricing, FAQ, contact), extracts sourced facts, pre-fills a
draft questionnaire in the exact format the existing parser accepts, and renders two new documents:

- research-brief.md: what the site says, each claim tied to its source page, plus visible openings.
- discovery-questions.md: Confirm (site facts, for a yes or a correction), Learn (the intake gaps,
  led by the five key inputs from inputs.py) and Open (questions built from the openings).

Rules, from the spec: every fact carries its source URL; anything not found stays blank, and a blank
becomes a question; extraction and mapping are deterministic (no LLM, no API-key path); a page that
cannot be read is recorded as unread, never guessed at. Fetching is stdlib-only and SSRF-guarded,
mirroring the safety posture of extract.py: https only, public addresses only, byte and page caps.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import parse_questionnaire_markdown, score_opportunity, validate_intake
from .inputs import INPUTS, input_gaps
from .models import ClientIntake, OpportunityScore, ValidationIssue

MAX_PAGES = 8                # spec §3: homepage + customer-facing pages, capped
MAX_PAGE_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10.0
USER_AGENT = "DIA-Research/1.0 (website research mode; contact: the site operator's consultant)"

# A start page with fewer words than this and no company signal at all is not a company site.
_MIN_COMPANY_WORDS = 80


class ResearchError(ValueError):
    """A friendly, user-facing reason the website could not be researched."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class FetchResult:
    url: str            # final URL after redirects
    status: int
    content_type: str
    body: bytes
    truncated: bool = False


# A fetcher takes a URL and returns the page, or raises ResearchError. Injectable for tests.
Fetcher = Callable[[str], FetchResult]


# --- fetching (SSRF-guarded, stdlib only) -------------------------------------------------------

def _resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def _assert_public_https(url: str, resolver: Callable[[str], list[str]]) -> None:
    """Reject anything that is not an https URL on a publicly reachable host."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ResearchError(f"DIA only researches https websites (got {parsed.scheme or 'no scheme'}).")
    host = parsed.hostname
    if not host:
        raise ResearchError("That URL has no host name.")
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        raise ResearchError("That host is not a public website.")
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            ips = [ipaddress.ip_address(ip) for ip in resolver(host)]
        except Exception as error:  # noqa: BLE001 - any resolver failure is one user-facing message
            raise ResearchError(f"DIA could not resolve {host}.") from error
    if not ips:
        raise ResearchError(f"DIA could not resolve {host}.")
    for ip in ips:
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified or not ip.is_global):
            raise ResearchError("That website resolves to a non-public address, so DIA will not fetch it.")


class _GuardedRedirectHandler(HTTPRedirectHandler):
    """Re-validate every redirect hop: a public page must not bounce DIA somewhere private."""

    def __init__(self, resolver: Callable[[str], list[str]]) -> None:
        super().__init__()
        self._resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib's own signature
        _assert_public_https(newurl, self._resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(url: str, *, opener=None, resolver: Callable[[str], list[str]] | None = None) -> FetchResult:
    """Fetch one page with the research guards. Raises ResearchError on any failure."""
    resolver = resolver or _resolve
    _assert_public_https(url, resolver)
    if opener is None:
        opener = build_opener(_GuardedRedirectHandler(resolver))
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html, application/xhtml+xml"})
    try:
        with opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            status = response.getcode()
            headers = response.headers
            content_type = headers.get_content_type() if headers is not None else ""
            body = response.read(MAX_PAGE_BYTES + 1)
    except HTTPError as error:
        raise ResearchError(f"The page answered with an error (HTTP {error.code}).") from error
    except (URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise ResearchError(f"DIA could not read that page ({reason}).") from error
    if status != 200:
        raise ResearchError(f"The page answered with an error (HTTP {status}).")
    if content_type not in ("text/html", "application/xhtml+xml", ""):
        raise ResearchError(f"That URL is not a web page (it is {content_type or 'an unknown type'}).")
    truncated = len(body) > MAX_PAGE_BYTES
    return FetchResult(url=final_url, status=status, content_type=content_type,
                       body=body[:MAX_PAGE_BYTES], truncated=truncated)


def _decode_html(body: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16") if body[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            pass
    return body.decode("cp1252", errors="replace")


# --- page extraction ----------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
_CHROME_TAGS = {"nav", "footer", "aside"}


@dataclass
class PageData:
    url: str
    title: str = ""
    description: str = ""
    headings1: list[str] = field(default_factory=list)
    headings2: list[str] = field(default_factory=list)
    headings3: list[str] = field(default_factory=list)
    list_items: list[str] = field(default_factory=list)
    text: str = ""                       # main visible text (nav/footer/aside excluded)
    links: list[tuple[str, str]] = field(default_factory=list)   # (absolute href, anchor text)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    has_form: bool = False
    html_source: str = ""                # lowercased markup, for technology-signal scanning

    @property
    def word_count(self) -> int:
        return len(self.text.split())


class _PageParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page = PageData(url=base_url)
        self._skip_depth = 0
        self._chrome_depth = 0
        self._in_title = False
        self._heading: str | None = None
        self._heading_parts: list[str] = []
        self._item_parts: list[str] | None = None
        self._anchor: tuple[str, list[str]] | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            if tag == "head":
                return
        if tag in _CHROME_TAGS:
            self._chrome_depth += 1
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (attrs_d.get("name") or attrs_d.get("property") or "").lower()
            if name in ("description", "og:description") and attrs_d.get("content"):
                self.page.description = attrs_d["content"].strip()
        elif tag in ("h1", "h2", "h3"):
            self._heading = tag
            self._heading_parts = []
        elif tag == "li":
            self._item_parts = []
        elif tag == "a":
            href = attrs_d.get("href", "").strip()
            if href:
                self._anchor = (href, [])
                lowered = href.lower()
                if lowered.startswith("mailto:"):
                    self.page.emails.append(href[7:].split("?")[0].strip())
                elif lowered.startswith("tel:"):
                    self.page.phones.append(href[4:].strip())
        elif tag == "form":
            self.page.has_form = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in _CHROME_TAGS and self._chrome_depth:
            self._chrome_depth -= 1
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3") and self._heading == tag:
            text = _clean(" ".join(self._heading_parts))
            target = {"h1": self.page.headings1, "h2": self.page.headings2, "h3": self.page.headings3}[tag]
            if text:
                target.append(text)
            self._heading = None
        elif tag == "li" and self._item_parts is not None:
            text = _clean(" ".join(self._item_parts))
            if text:
                self.page.list_items.append(text)
            self._item_parts = None
        elif tag == "a" and self._anchor is not None:
            href, parts = self._anchor
            absolute = urljoin(self.page.url, href)
            self.page.links.append((absolute, _clean(" ".join(parts))))
            self._anchor = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.page.title = _clean(self.page.title + " " + data)
            return
        if self._skip_depth:
            return
        if self._heading:
            self._heading_parts.append(data)
        if self._item_parts is not None:
            self._item_parts.append(data)
        if self._anchor is not None:
            self._anchor[1].append(data)
        if not self._chrome_depth:
            self._text_parts.append(data)

    def finish(self) -> PageData:
        text = _clean(" ".join(self._text_parts))
        self.page.text = text
        found_emails = set(self.page.emails)
        found_emails.update(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))
        self.page.emails = sorted(e for e in found_emails if not e.lower().endswith((".png", ".jpg", ".gif")))
        phones = list(self.page.phones)
        phones += re.findall(r"\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}", text)
        seen: list[str] = []
        for phone in phones:
            cleaned = phone.strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        self.page.phones = seen
        return self.page


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_page(url: str, html_text: str) -> PageData:
    parser = _PageParser(url)
    parser.feed(html_text)
    page = parser.finish()
    page.html_source = html_text.lower()
    return page


# --- crawl --------------------------------------------------------------------------------------

_PAGE_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("services", ("service", "services", "what-we-do", "what_we_do", "solutions", "offerings",
                  "treatments", "procedures", "capabilities")),
    ("about", ("about", "our-story", "our_story", "who-we-are", "company-profile")),
    ("pricing", ("pricing", "prices", "plans", "rates", "packages")),
    ("faq", ("faq", "faqs", "frequently-asked")),
    ("contact", ("contact", "get-in-touch", "reach-us")),
)


def _page_kind(url: str, anchor_text: str) -> str | None:
    haystacks = (urlparse(url).path.lower(), anchor_text.lower().replace(" ", "-"))
    for kind, needles in _PAGE_KINDS:
        if any(needle in hay for needle in needles for hay in haystacks):
            return kind
    return None


def _same_site(start_url: str, candidate: str) -> bool:
    start_host = (urlparse(start_url).hostname or "").lower()
    host = (urlparse(candidate).hostname or "").lower()
    if not start_host or not host:
        return False
    return host == start_host or host.endswith("." + start_host) or start_host.endswith("." + host)


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


# --- facts --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SourcedFact:
    value: str
    source: str     # the page the fact came from


@dataclass
class Opening:
    kind: str
    detail: str
    source: str
    question: str


@dataclass
class SiteFacts:
    start_url: str
    company_name: SourcedFact | None = None
    tagline: SourcedFact | None = None
    description: SourcedFact | None = None
    industry: SourcedFact | None = None
    services: list[SourcedFact] = field(default_factory=list)
    locations: list[SourcedFact] = field(default_factory=list)
    phones: list[SourcedFact] = field(default_factory=list)
    emails: list[SourcedFact] = field(default_factory=list)
    has_contact_form: bool = False
    form_source: str = ""
    booking: SourcedFact | None = None
    chat_widget: bool = False
    chat_source: str = ""
    proof_points: list[SourcedFact] = field(default_factory=list)
    ctas: list[SourcedFact] = field(default_factory=list)
    tech_signals: list[SourcedFact] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)


_JUNK_HEADINGS = {
    "welcome", "home", "contact", "contact us", "about", "about us", "menu", "services",
    "our services", "what we do", "testimonials", "reviews", "blog", "news", "gallery", "faq",
    "faqs", "get in touch", "get a quote", "book now", "call now", "learn more", "read more",
    "our team", "why choose us", "why us", "follow us", "subscribe", "newsletter", "sitemap",
    "privacy policy", "terms", "copyright",
}

_TECH_SIGNALS: tuple[tuple[str, str], ...] = (
    ("wp-content", "WordPress"), ("wp-includes", "WordPress"),
    ("squarespace", "Squarespace"), ("cdn.shopify.com", "Shopify"), ("myshopify", "Shopify"),
    ("webflow", "Webflow"), ("wixstatic", "Wix"), ("wix.com", "Wix"),
    ("calendly", "Calendly booking"), ("acuityscheduling", "Acuity Scheduling"),
    ("squareup", "Square"), ("booksy", "Booksy"),
    ("googletagmanager", "Google Analytics"), ("gtag(", "Google Analytics"),
    ("intercom", "live chat"), ("tawk.to", "live chat"), ("drift.com", "live chat"),
    ("crisp.chat", "live chat"),
)

_INDUSTRY_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Plumbing", ("plumber", "plumbing", "drain cleaning", "sewer")),
    ("HVAC", ("hvac", "heating and cooling", "furnace repair", "air conditioning")),
    ("Roofing", ("roofing", "roofer", "shingle")),
    ("Dental", ("dentist", "dental clinic", "orthodont")),
    ("Legal services", ("law firm", "lawyer", "attorney")),
    ("Landscaping", ("landscaping", "lawn care", "snow removal")),
    ("Cleaning services", ("cleaning service", "house cleaning", "maid service")),
    ("Auto repair", ("auto repair", "mechanic", "car repair")),
    ("Real estate", ("real estate", "realtor", "homes for sale")),
    ("Restaurant and food", ("restaurant", "catering", "bakery")),
    ("Accounting", ("accounting firm", "bookkeeping", "tax preparation")),
    ("Construction and renovation", ("renovation", "remodeling", "general contractor")),
    ("Electrical", ("electrician", "electrical contractor")),
    ("Towing and roadside", ("towing", "tow truck", "roadside assistance")),
)

_PROOF_CUES = re.compile(
    r"\b\d[\d,]*\+?\s*(?:years|customers|clients|jobs|projects|homes|reviews|members)\b"
    r"|\b\d(?:\.\d)?\s*(?:star|★)"
    r"|\bguarantee(?:d|s)?\b|\blicensed\b|\binsured\b|\bcertified\b|\baccredited\b|\baward[- ]winning\b",
    re.IGNORECASE,
)
_CLAIM_CUES = re.compile(r"\b(24/7|same[- ]day|emergency|free (?:quote|estimate|consultation))\b", re.IGNORECASE)


def _service_candidate(text: str) -> str | None:
    cleaned = _clean(text)
    lowered = cleaned.lower()
    if len(cleaned) < 3 or len(cleaned) > 70 or lowered in _JUNK_HEADINGS:
        return None
    if "@" in cleaned or "©" in cleaned or cleaned.count(" ") > 7:
        return None
    return cleaned


def _sentence_split(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _build_facts(pages: list[PageData], pages_by_kind: dict[str, PageData],
                 start_url: str, company_hint: str) -> SiteFacts:
    facts = SiteFacts(start_url=start_url)
    home = pages[0]

    if company_hint.strip():
        facts.company_name = SourcedFact(company_hint.strip(), start_url)
    elif home.title:
        first = re.split(r"\s*[|–—]\s*|\s+-\s+|:\s+", home.title)[0].strip()
        if 1 <= len(first) <= 60:
            facts.company_name = SourcedFact(first, home.url)
    if facts.company_name is None and home.headings1:
        facts.company_name = SourcedFact(home.headings1[0], home.url)
    if facts.company_name is None:
        host = (urlparse(start_url).hostname or "").lower().removeprefix("www.")
        label = host.split(".")[0].replace("-", " ").strip()
        if label:
            facts.company_name = SourcedFact(label.title(), start_url)

    if home.description:
        facts.description = SourcedFact(home.description, home.url)
    for heading in home.headings1 + home.headings2:
        if facts.company_name and heading.lower() == facts.company_name.value.lower():
            continue
        facts.tagline = SourcedFact(heading, home.url)
        break

    # Industry: first cue that appears, searched page by page so the source is honest.
    for page in pages:
        lowered = f"{page.title} {page.text[:4000]}".lower()
        for label, cues in _INDUSTRY_CUES:
            if any(cue in lowered for cue in cues):
                facts.industry = SourcedFact(label, page.url)
                break
        if facts.industry:
            break

    # Services: the services page's headings and list items first, then homepage headings,
    # then service-looking navigation links.
    services: list[SourcedFact] = []
    seen_services: set[str] = set()

    def _add_service(value: str | None, source: str) -> None:
        if value and value.lower() not in seen_services and len(services) < 8:
            seen_services.add(value.lower())
            services.append(SourcedFact(value, source))

    services_page = pages_by_kind.get("services")
    if services_page is not None:
        for item in services_page.headings2 + services_page.headings3 + services_page.list_items:
            _add_service(_service_candidate(item), services_page.url)
    if len(services) < 3:
        for item in home.headings2 + home.headings3:
            _add_service(_service_candidate(item), home.url)
    if len(services) < 3:
        for page in pages:
            for href, anchor_text in page.links:
                kind_path = urlparse(href).path.lower()
                if "service" in kind_path and _same_site(start_url, href):
                    candidate = _service_candidate(anchor_text)
                    if candidate is None:
                        slug = kind_path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").strip()
                        candidate = _service_candidate(slug.title())
                    _add_service(candidate, page.url)
    facts.services = services

    # Locations and service area.
    locations: list[SourcedFact] = []
    seen_locations: set[str] = set()
    location_patterns = (
        re.compile(r"[Ss]erving (?:the )?([A-Z][A-Za-z&' .-]{2,40}?)(?:\s+area|\s+region)?\s*[.,]"),
        re.compile(r"[Ll]ocated in ([A-Z][A-Za-z&' .-]{2,40}?)\s*[.,]"),
        re.compile(r"\b([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+){0,2}),\s*"
                   r"(ON|BC|AB|MB|SK|QC|NB|NS|PE|NL|YT|NT|NU|AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|"
                   r"LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|"
                   r"WV|WI|WY|DC)\b"),
    )
    for page in pages:
        for pattern in location_patterns:
            for match in pattern.finditer(page.text):
                if pattern is location_patterns[2]:
                    value = f"{_clean(match.group(1))}, {match.group(2)}"
                else:
                    value = _clean(match.group(1))
                if value and value.lower() not in seen_locations and len(locations) < 4:
                    seen_locations.add(value.lower())
                    locations.append(SourcedFact(value, page.url))
    facts.locations = locations

    # Contact channels.
    for page in pages:
        for phone in page.phones:
            if all(phone != f.value for f in facts.phones) and len(facts.phones) < 3:
                facts.phones.append(SourcedFact(phone, page.url))
        for email in page.emails:
            if all(email != f.value for f in facts.emails) and len(facts.emails) < 3:
                facts.emails.append(SourcedFact(email, page.url))
        if page.has_form and not facts.has_contact_form:
            facts.has_contact_form = True
            facts.form_source = page.url
        for href, anchor_text in page.links:
            lowered = f"{href} {anchor_text}".lower()
            if facts.booking is None and any(word in lowered for word in
                                            ("calendly", "acuity", "booksy", "book now", "book online",
                                             "schedule an appointment", "book-an-appointment")):
                facts.booking = SourcedFact(anchor_text or "Online booking", page.url)
            if not facts.chat_widget and any(word in page.html_source for word in
                                             ("intercom", "tawk.to", "drift.com", "crisp.chat")):
                facts.chat_widget = True
                facts.chat_source = page.url
            cleaned = _clean(anchor_text)
            if (cleaned and re.match(r"(?i)^(get|book|request|schedule|call|contact|start|claim)\b", cleaned)
                    and all(cleaned.lower() != f.value.lower() for f in facts.ctas) and len(facts.ctas) < 5):
                facts.ctas.append(SourcedFact(cleaned, page.url))

    # Proof points: sentences that carry a number with a cue, or a trust cue.
    for page in pages:
        for sentence in _sentence_split(page.text):
            if 15 <= len(sentence) <= 200 and _PROOF_CUES.search(sentence):
                if all(sentence.lower() != f.value.lower() for f in facts.proof_points) and len(facts.proof_points) < 6:
                    facts.proof_points.append(SourcedFact(sentence, page.url))

    # Technology signals: scope hints only, never conclusions (spec §3).
    signals_seen: set[str] = set()
    for page in pages:
        for needle, label in _TECH_SIGNALS:
            if needle in page.html_source and label not in signals_seen:
                signals_seen.add(label)
                facts.tech_signals.append(SourcedFact(label, page.url))

    facts.openings = _find_openings(facts, pages, pages_by_kind)
    return facts


def _find_openings(facts: SiteFacts, pages: list[PageData],
                   pages_by_kind: dict[str, PageData]) -> list[Opening]:
    openings: list[Opening] = []
    crawled_paths = [urlparse(page.url).path.lower() for page in pages]

    # A service the site names but gives no page of its own.
    for service in facts.services:
        words = [w for w in re.findall(r"[a-z]{4,}", service.value.lower())
                 if w not in ("services", "service", "repair", "repairs")]
        if words and not any(any(word in path for word in words) for path in crawled_paths):
            openings.append(Opening(
                kind="service_without_page",
                detail=f"The site names \"{service.value}\" as a service but has no page for it.",
                source=service.source,
                question=(f"Your site mentions {service.value} but there is no page for it - "
                          "is that a service you want more of, or one you are moving away from?"),
            ))
        if len([o for o in openings if o.kind == "service_without_page"]) >= 2:
            break

    # A promise with no path to act on it.
    claim_match = _CLAIM_CUES.search(pages[0].text)
    if claim_match and not facts.phones and not facts.has_contact_form and facts.booking is None:
        claim = claim_match.group(0)
        openings.append(Opening(
            kind="claim_without_action",
            detail=f"The homepage promises \"{claim}\" but the site offers no phone link, form or booking path.",
            source=pages[0].url,
            question=(f"Your site says {claim} - how does a customer reach you in that moment today, "
                      "when the site itself gives them no way to act on it?"),
        ))

    # Services sold with no proof stated anywhere.
    if facts.services and not facts.proof_points:
        openings.append(Opening(
            kind="no_proof_online",
            detail="The site lists services but states no reviews, ratings, years in business or guarantees.",
            source=facts.services[0].source,
            question=("Your site lists your services but shows no reviews, ratings, years in business "
                      "or guarantees - what proof do happy customers usually point to?"),
        ))

    # No pricing anywhere.
    all_text = " ".join(page.text for page in pages).lower()
    if "pricing" not in pages_by_kind and "pric" not in all_text and "rate" not in all_text:
        openings.append(Opening(
            kind="no_pricing_path",
            detail="No pricing, rates or starting-at figures appear anywhere on the site.",
            source=facts.start_url,
            question=("There is no pricing anywhere on your site - is that a deliberate choice, "
                      "or something you would want to change?"),
        ))
    return openings


# --- draft questionnaire ------------------------------------------------------------------------

def _cell(value: str) -> str:
    return _clean(value).replace("|", "-")


def _draft_questionnaire(facts: SiteFacts) -> str:
    name = facts.company_name.value if facts.company_name else ""
    location = "; ".join(f.value for f in facts.locations)
    services = "; ".join(f.value for f in facts.services)
    industry = facts.industry.value if facts.industry else ""
    notes_bits = []
    if facts.tech_signals:
        labels = ", ".join(f.value for f in facts.tech_signals)
        notes_bits.append(f"From the website: the site shows signs of {labels} (a scope hint, not a confirmed tool list).")
    if facts.booking:
        notes_bits.append(f"Online booking is offered (\"{facts.booking.value}\").")
    notes = " ".join(notes_bits)
    lines = [
        "# Discovery Intake Questionnaire",
        "",
        "## 1. Company Basics",
        "",
        "| Question | Answer |",
        "| --- | --- |",
        f"| Company name | {_cell(name)} |",
        f"| Website | {_cell(facts.start_url)} |",
        f"| Industry / niche | {_cell(industry)} |",
        "| Company size |  |",
        "| Years in business |  |",
        f"| Location | {_cell(location)} |",
        "",
        "## 2. Current Tools",
        "",
        "| Question | Answer |",
        "| --- | --- |",
        "| What tools/software do you use today? |  |",
        "",
        "## 3. Pain Points",
        "",
        "| Question | Answer |",
        "| --- | --- |",
        "",
        "## 4. Goals",
        "",
        "| Question | Answer |",
        "| --- | --- |",
        "",
        "## 5. Budget + Timeline",
        "",
        "| Question | Answer |",
        "| --- | --- |",
        "| Budget range |  |",
        "| Final decision-maker |  |",
        "| Preferred start date |  |",
        "| In-house tech person |  |",
        f"| Anything else we should know | {_cell(notes)} |",
        "",
        "## 6. Workflows and value",
        "",
        "| Question | Answer |",
        "| --- | --- |",
        f"| Top 1-3 manual tasks: who does each, hours per week, number of people | {_cell(services)} |",
        "| Loaded hourly cost of those people (or a salary band) |  |",
        "| The one number that would show this worked, and its value today |  |",
        "| Who owns the process today, and who signs off on the project? |  |",
        "| Systems involved, and can you grant login or API access? |  |",
        "",
        "## Sources (website research)",
        "",
        "Every pre-filled answer above came from the company's own website:",
    ]
    sourced: list[SourcedFact] = [f for f in (facts.company_name, facts.industry) if f]
    sourced += facts.services + facts.locations
    for fact in sourced:
        lines.append(f"- {fact.value} - {fact.source}")
    lines.append(f"- Website researched starting at {facts.start_url}")
    return "\n".join(lines) + "\n"


# --- documents ----------------------------------------------------------------------------------

def _source_note(fact: SourcedFact) -> str:
    return f" _(source: {fact.source})_"


def _brief_markdown(facts: SiteFacts, pages: list[PageData], unread: list[dict[str, str]]) -> str:
    name = facts.company_name.value if facts.company_name else "This company"
    lines = [f"# Research brief: {name}", ""]
    lines.append(f"Prepared from the company's own website, starting at {facts.start_url}. "
                 f"DIA read {len(pages)} page(s): " + ", ".join(page.url for page in pages) + ".")
    if unread:
        lines.append("Pages DIA could not read (recorded, not guessed at): "
                     + ", ".join(f"{item['url']} ({item['reason']})" for item in unread) + ".")
    lines.append("")
    lines.append("## Who they are")
    lines.append("")
    if facts.description:
        lines.append(f"{facts.description.value}{_source_note(facts.description)}")
    elif facts.tagline:
        lines.append(f"{facts.tagline.value}{_source_note(facts.tagline)}")
    else:
        lines.append("The site does not describe the business in its own words on the pages DIA read.")
    if facts.industry:
        lines.append(f"It reads as a {facts.industry.value} business.{_source_note(facts.industry)}")
    if facts.locations:
        lines.append("Location and service area as stated: "
                     + "; ".join(f.value for f in facts.locations) + ".")
    lines.append("")
    lines.append("## What they sell")
    lines.append("")
    if facts.services:
        lines += [f"- {fact.value}{_source_note(fact)}" for fact in facts.services]
    else:
        lines.append("The site does not name its services or products on the pages DIA read.")
    lines.append("")
    lines.append("## How a customer reaches them today")
    lines.append("")
    steps = []
    for fact in facts.phones:
        steps.append(f"Call {fact.value}{_source_note(fact)}")
    for fact in facts.emails:
        steps.append(f"Email {fact.value}{_source_note(fact)}")
    if facts.has_contact_form:
        steps.append(f"Send the contact form _(source: {facts.form_source})_")
    if facts.booking:
        steps.append(f"Book online: \"{facts.booking.value}\"{_source_note(facts.booking)}")
    if facts.chat_widget:
        steps.append(f"Use the live chat on the site _(source: {facts.chat_source})_")
    if steps:
        lines += [f"{i}. {step}" for i, step in enumerate(steps, 1)]
    else:
        lines.append("The pages DIA read show no phone number, email address, form or booking path.")
    if facts.ctas:
        lines.append("")
        lines.append("Calls to action the site already runs: "
                     + ", ".join(f"\"{fact.value}\"" for fact in facts.ctas) + ".")
    lines.append("")
    lines.append("## Proof points they state")
    lines.append("")
    if facts.proof_points:
        lines += [f"- {fact.value}{_source_note(fact)}" for fact in facts.proof_points]
    else:
        lines.append("The site states no reviews, ratings, years, counts or guarantees on the pages DIA read.")
    lines.append("")
    lines.append("## Openings")
    lines.append("")
    if facts.openings:
        lines += [f"- {opening.detail} _(source: {opening.source})_" for opening in facts.openings]
    else:
        lines.append("No obvious gaps between what the site claims and what it supports.")
    if len(pages) == 1:
        lines.append("- The whole site is a single page, so most of the intake is still unknown.")
    lines.append("")
    lines.append("## What the site does not say")
    lines.append("")
    lines.append("Hours, costs, success measures, people and systems are not on the public website. "
                 "They are the Learn questions in discovery-questions.md.")
    return "\n".join(lines) + "\n"


_LEARN_EXTRAS: tuple[tuple[str, str], ...] = (
    ("missing_pain_points", "What repetitive work is eating the most time right now?"),
    ("missing_goals", "What would success look like for you?"),
    ("missing_budget", "What budget range have you set aside for this kind of work?"),
    ("missing_decision_maker", "Who makes the final decision on a project like this?"),
    ("missing_start_date", "When would you want to start?"),
)


def _questions_markdown(facts: SiteFacts, intake: ClientIntake, score: OpportunityScore,
                        issues: list[ValidationIssue]) -> str:
    name = facts.company_name.value if facts.company_name else "this company"
    lines = [f"# Discovery questions: {name}", "",
             "Prepared from the company's own website. Confirm what the site says, learn what it "
             "cannot say, then open on the gaps.", ""]
    lines.append("## 1. Confirm")
    lines.append("")
    confirm: list[str] = []
    if facts.company_name:
        confirm.append(f"Your site presents the business as \"{facts.company_name.value}\" - "
                       "is that the name you trade under?")
    if facts.services:
        listed = ", ".join(f.value for f in facts.services[:4])
        confirm.append(f"Your site lists {listed} as services - is that where most of the work "
                       "actually comes from?")
    if facts.locations:
        confirm.append(f"Your site says you serve {facts.locations[0].value} - "
                       "is that still the full picture on location and service area?")
    if facts.industry:
        confirm.append(f"Your site reads as a {facts.industry.value} business - is that how "
                       "you would describe it?")
    channels = []
    if facts.phones:
        channels.append("phone")
    if facts.has_contact_form:
        channels.append("the website form")
    if facts.booking:
        channels.append("online booking")
    if facts.emails:
        channels.append("email")
    if channels:
        confirm.append(f"Your site routes customers through {' and '.join(channels)} - "
                       "is that how most work actually comes in?")
    if confirm:
        lines += [f"{i}. {question}" for i, question in enumerate(confirm, 1)]
    else:
        lines.append("Nothing on the site was solid enough to confirm; start with the Learn questions.")
    lines.append("")
    lines.append("## 2. Learn")
    lines.append("")
    learn: list[str] = []
    for key in input_gaps(intake, score):
        learn.append(INPUTS[key][1])  # the client-facing question wording from inputs.py
    issue_codes = {issue.code for issue in issues}
    for code, question in _LEARN_EXTRAS:
        if code in issue_codes and question not in learn:
            learn.append(question)
    if learn:
        lines += [f"{i}. {question}" for i, question in enumerate(learn, 1)]
    else:
        lines.append("The site answered everything the intake asks. (Rare - check the brief.)")
    lines.append("")
    lines.append("## 3. Open")
    lines.append("")
    open_questions = [opening.question for opening in facts.openings][:3]
    if len(open_questions) < 2:
        open_questions.append("What is the one job your website should be doing for the business "
                              "that it is not doing today?")
    lines += [f"{i}. {question}" for i, question in enumerate(open_questions[:3], 1)]
    return "\n".join(lines) + "\n"


# --- the run ------------------------------------------------------------------------------------

@dataclass
class ResearchResult:
    start_url: str
    facts: SiteFacts
    questionnaire: str
    documents: dict[str, str]
    intake: ClientIntake
    issues: list[ValidationIssue]
    score: OpportunityScore
    pages_read: list[str]
    pages_unread: list[dict[str, str]]


def research_website(url: str, company_name: str = "", *, fetcher: Fetcher | None = None) -> ResearchResult:
    """Crawl a company website and turn it into a draft intake plus the two research documents."""
    fetcher = fetcher or fetch_url
    start = (url or "").strip()
    if not start:
        raise ResearchError("A website URL is required.")
    if "://" not in start:
        start = f"https://{start}"

    first = fetcher(start)  # a start-page failure is the run's failure: it propagates
    pages: list[PageData] = []
    pages_by_kind: dict[str, PageData] = {}
    unread: list[dict[str, str]] = []
    visited = {_normalize_url(first.url), _normalize_url(start)}
    home = parse_page(first.url, _decode_html(first.body))
    pages.append(home)

    queue: list[tuple[str, str]] = []
    queued = set(visited)

    def _enqueue(page: PageData) -> None:
        for href, anchor_text in page.links:
            if _page_kind(href, anchor_text) is None or not _same_site(first.url, href):
                continue
            normalized = _normalize_url(href)
            if normalized not in queued:
                queued.add(normalized)
                queue.append((href, anchor_text))

    _enqueue(home)
    kind_order = {kind: index for index, (kind, _) in enumerate(_PAGE_KINDS)}
    while queue and len(pages) < MAX_PAGES:
        queue.sort(key=lambda item: kind_order.get(_page_kind(item[0], item[1]) or "", 99))
        href, anchor_text = queue.pop(0)
        kind = _page_kind(href, anchor_text)
        try:
            fetched = fetcher(href)
        except ResearchError as error:
            unread.append({"url": href, "reason": str(error)})
            continue
        page = parse_page(fetched.url, _decode_html(fetched.body))
        pages.append(page)
        visited.add(_normalize_url(fetched.url))
        if kind is not None and kind not in pages_by_kind:
            pages_by_kind[kind] = page  # the first page of a kind is the canonical one
        _enqueue(page)

    facts = _build_facts(pages, pages_by_kind, first.url, company_name)

    has_signal = bool(
        company_name.strip() or facts.services or facts.locations or facts.phones or facts.emails
        or facts.has_contact_form or home.title or home.headings1
    )
    if not has_signal and home.word_count < _MIN_COMPANY_WORDS:
        raise ResearchError(
            "That page does not look like a company website - DIA found no company name, services, "
            "location or contact details on it, so there is nothing to research yet."
        )

    questionnaire = _draft_questionnaire(facts)
    intake = parse_questionnaire_markdown(questionnaire)
    issues = validate_intake(intake)
    score = score_opportunity(intake)
    documents = {
        "research-brief": _brief_markdown(facts, pages, unread),
        "discovery-questions": _questions_markdown(facts, intake, score, issues),
    }
    return ResearchResult(
        start_url=first.url,
        facts=facts,
        questionnaire=questionnaire,
        documents=documents,
        intake=intake,
        issues=issues,
        score=score,
        pages_read=[page.url for page in pages],
        pages_unread=unread,
    )


def build_research_response(url: str, company_name: str = "", *,
                            fetcher: Fetcher | None = None) -> dict[str, object]:
    """The /api/research response shape, mirroring build_intake_response's keys where they overlap."""
    from .core import client_score_dict

    result = research_website(url, company_name, fetcher=fetcher)
    return {
        "url": result.start_url,
        "questionnaire": result.questionnaire,
        "intake": asdict(result.intake),
        "issues": [asdict(issue) for issue in result.issues],
        "score": client_score_dict(result.score),  # no tier price band (Ikenna: match the site)
        "documents": result.documents,
        "facts": asdict(result.facts),
        "pages": {"read": result.pages_read, "unread": result.pages_unread},
    }
