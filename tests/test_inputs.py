"""dia-savant inputs addendum v1.1.1 (proto/dia-savant-inputs-addendum.md §10, flows.md v9.0.1 §2.9/§3.1/§5.2):
the pre-run inputs prompt, "Recommended next steps" with its two paths, the cabinet's move to localStorage, and
Re-run with updated answers. One test (or group) per §10 acceptance test is marked "§10.n"."""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app import build_intake_response, consulting_config
from client_discovery import inputs
from client_discovery.core import QUESTION_ALIASES, QUESTION_NUMBERS, SECTION6_ALIASES, parse_questionnaire_markdown
from tests.test_savant import LEGACY, SAMPLE, questionnaire

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static/app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "templates/index.html").read_text(encoding="utf-8")
CTA = "Bash Systems & Services offers business consulting"
URL = inputs.DEFAULT_CONSULTING_INTAKE_URL

# §5.4 truth table rows 2-7 (row 1 is the complete sample).
NO_HOURS = questionnaire(q1="Lead triage - sales coordinator, 2 people; Invoice chasing - founder, 1 person")
NO_COST = questionnaire(q2="")
PARTIAL = questionnaire(q1="Lead triage - coordinator, 6 hours a week, 1 person; Invoice chasing - founder, 1 person")
NO_METRIC = questionnaire(q3="Faster responses")
NO_COST_NO_METRIC = questionnaire(q2="", q3="Faster responses")
PARTIAL_NO_METRIC = questionnaire(q1="Lead triage - coordinator, 6 hours a week, 1 person; Invoice chasing - founder, 1 person",
                                  q3="Faster responses")
PEOPLE_ONLY = questionnaire(q1="Lead triage - coordinator, 6 hours a week; Invoice chasing - founder, 4 hours a week")
SYSTEMS_ONLY = questionnaire(q5="")
GAPPY = [NO_HOURS, NO_COST, PARTIAL, NO_METRIC, NO_COST_NO_METRIC, PARTIAL_NO_METRIC, PEOPLE_ONLY, SYSTEMS_ONLY]
GAPPY_IDS = ["no-hours", "no-cost", "partial", "no-metric", "no-cost-no-metric", "partial-no-metric", "people-only", "systems-only"]


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.delenv("DIA_CONSULTING_INTAKE_URL", raising=False)
    monkeypatch.delenv("DIA_PATH2_TRIGGER", raising=False)


def analysis(text: str) -> str:
    return build_intake_response(text)["documents"]["opportunity-analysis.md"]


def all_documents(text: str) -> str:
    return "\n".join(build_intake_response(text)["documents"].values())


def _need(tool: str) -> None:
    if not shutil.which(tool):
        pytest.skip(f"{tool} not installed")


def pdf_text(result: dict) -> str:
    from client_discovery.report import render_pdf

    _need("pdftotext")

    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(render_pdf(result))
        handle.flush()
        text = subprocess.run(["pdftotext", handle.name, "-"], capture_output=True, text=True, check=True).stdout
    return " ".join(text.split())


def pdf_links(result: dict) -> list[str]:
    from client_discovery.report import render_pdf

    _need("pdfinfo")

    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(render_pdf(result))
        handle.flush()
        out = subprocess.run(["pdfinfo", "-url", handle.name], capture_output=True, text=True, check=True).stdout
    return [line.split()[-1] for line in out.splitlines()[1:] if "Annotation" in line]


# --- §2 input_gaps ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,gaps", [
    (SAMPLE, []),
    (NO_HOURS, ["hours"]),
    (NO_COST, ["hourly_cost"]),
    (PARTIAL, ["hours"]),
    (NO_METRIC, ["success_metric"]),
    (NO_COST_NO_METRIC, ["hourly_cost", "success_metric"]),
    (PEOPLE_ONLY, ["people"]),
    (SYSTEMS_ONLY, ["systems"]),
    (LEGACY, ["hours", "hourly_cost", "success_metric"]),
])
def test_input_gaps_in_fixed_order(text, gaps):
    assert build_intake_response(text)["documents"] and _gaps(text) == gaps


def _gaps(text):
    from client_discovery.core import score_opportunity

    intake = parse_questionnaire_markdown(text)
    return inputs.input_gaps(intake, score_opportunity(intake))


# --- §10.1 No auto-fill; §10.2 the labelled default --------------------------------------------------------

def test_no_auto_fill_for_missing_hours_cost_and_metric():  # §10.1
    text = questionnaire(q1="Lead triage - sales coordinator", q2="", q3="")
    result = build_intake_response(text)
    assert result["intake"]["workflow_tasks"] == "Lead triage - sales coordinator"
    assert result["intake"]["hourly_cost"] == "" and result["intake"]["success_metric"] == ""
    assert result["value_at_stake"] is None and result["score"]["value_at_stake"] is None
    assert all(w["hours"] is None and w["people"] is None for w in result["score"]["workflows"])


def test_labelled_default_strategic_value():  # §10.2
    result = build_intake_response(NO_COST)
    assert "VALUE_AT_STAKE_MISSING" in result["flags"]
    assert result["score"]["strategic_value"] == 3 and result["score"]["strategic_value_default"]
    text = analysis(NO_COST)
    assert "until weekly hours and an hourly cost are provided" in text
    assert "3/5 default" in pdf_text(result).replace("3 /5", "3/5") or "3 / 5 default" in pdf_text(result)
    assert "Strategic value: 3" not in result["documents"]["client-profile.md"]


# --- §10.5 two paths in order; §10.6 CTA at most once; §10.7 gaps drive the CTA ----------------------------

@pytest.mark.parametrize("text", GAPPY, ids=GAPPY_IDS)
def test_path_1_intro_before_items_and_path_2_after(text):  # §10.5
    doc = analysis(text)
    block = doc[doc.index("**Recommended next steps**"):]
    intro = block.index("can work these out with its own team and tools, then return the updated answers for a new DIA run.")
    first = block.index("1. **")
    last = max(m.start() for m in re.finditer(r"^\d\. \*\*", block, re.M))
    assert intro < first <= last < block.index(CTA)
    assert doc.index("**Recommended next steps**") < doc.index("**Flags to clear first**") if "**Flags to clear first**" in doc else True


@pytest.mark.parametrize("text", [SAMPLE, *GAPPY], ids=["complete", *GAPPY_IDS])
def test_cta_at_most_once_per_document_and_json(text):  # §10.6 (Python surfaces; browser surfaces below)
    result = build_intake_response(text)
    for name, body in result["documents"].items():
        assert body.count(CTA) <= 1, name
    assert json.dumps(result).count(CTA) <= 1


def test_cta_once_in_the_pdf():  # §10.6
    pytest.importorskip("weasyprint")
    assert pdf_text(build_intake_response(NO_COST)).count(CTA) == 1


@pytest.mark.parametrize("text", GAPPY, ids=GAPPY_IDS)
def test_gaps_give_exactly_one_cta_broad_trigger(text):  # §10.7 rows 2-7
    assert all_documents(text).count(CTA) == 1


def test_complete_inputs_give_no_steps_and_no_cta():  # §10.7 row 1, §9
    docs = all_documents(SAMPLE)
    assert CTA not in docs and "Recommended next steps" not in docs


def test_narrow_trigger_switch_stays_in_config(monkeypatch):
    monkeypatch.setenv("DIA_PATH2_TRIGGER", "narrow")
    assert CTA not in all_documents(PEOPLE_ONLY) and "Recommended next steps" in all_documents(PEOPLE_ONLY)
    assert all_documents(NO_COST).count(CTA) == 1


# --- §10.7a contact sentence; §10.7b name -------------------------------------------------------------------

def test_cta_strings_are_pinned():  # §10.7a, §10.7b
    assert inputs.path2_sentences("Northstar Studio", ["hours"]) == [
        "If Northstar Studio would like help setting up this tracking or measurement, Bash Systems & Services offers "
        "business consulting.",
        f"Its services are listed at {URL}.",
    ]
    assert inputs.CONSULTING_NAME == "Bash Systems & Services"


def test_contact_sentence_unset_drops_the_second_sentence_and_the_link(monkeypatch):  # §10.7a
    monkeypatch.setenv("DIA_CONSULTING_INTAKE_URL", "")
    doc = analysis(NO_COST)
    assert CTA in doc and "Its services are listed at" not in doc
    assert consulting_config()["consultingUrl"] is None
    pytest.importorskip("weasyprint")
    result = build_intake_response(NO_COST)
    assert "Its services are listed at" not in pdf_text(result) and pdf_links(result) == []


def test_contact_sentence_set_follows_path_2_once_verbatim(monkeypatch):  # §10.7a
    test_url = "https://example.com/bash/services?view=card"
    monkeypatch.setenv("DIA_CONSULTING_INTAKE_URL", test_url)
    doc = analysis(NO_COST)
    assert doc.count(f"{CTA}. Its services are listed at {test_url}.") == 1
    assert consulting_config()["consultingUrl"] == test_url


def test_default_url_is_config_not_template():
    assert inputs.consulting_url() == URL
    for path in (ROOT / "templates").rglob("*.html"):
        assert "script.google.com" not in path.read_text(encoding="utf-8"), path
    assert "script.google.com" not in APP_JS


@pytest.mark.parametrize("bad", ["http://example.com/x", "javascript:alert(1)", "https://exa mple.com", "not a url"])
def test_invalid_url_counts_as_unset(monkeypatch, bad):
    monkeypatch.setenv("DIA_CONSULTING_INTAKE_URL", bad)
    assert inputs.consulting_url() is None


def test_pdf_services_link_is_a_clickable_annotation():  # §10.7a, decision 1
    pytest.importorskip("weasyprint")
    result = build_intake_response(NO_COST)
    assert set(pdf_links(result)) == {URL}
    text = pdf_text(result)
    # pdftotext drops the hyphen where the wrapped URL breaks at "-"; the annotation above carries it verbatim.
    assert text.count("Its services are listed at") == 1
    assert f"Its services are listed at {URL}.".replace("-", "") in text.replace("-", "")


def test_path_2_has_no_price_words():  # §10.7a
    for gaps in (["hours"], ["people"], ["systems"]):
        text = " ".join(inputs.path2_sentences("Northstar Studio", gaps)).replace(URL, "")
        assert not re.search(r"\$|\brate|\bprice", text, re.I)


# --- §10.8 no Inputs label in exports; §10.9 formal register -----------------------------------------------

@pytest.mark.parametrize("text", [SAMPLE, *GAPPY], ids=["complete", *GAPPY_IDS])
def test_no_inputs_label_in_exports(text):  # §10.8
    assert not re.search(r"Inputs: (complete|partial|thin)", all_documents(text))


@pytest.mark.parametrize("text", GAPPY, ids=GAPPY_IDS)
def test_steps_and_cta_are_formal(text):  # §10.9
    doc = analysis(text)
    block = doc[doc.index("**Recommended next steps**"):]
    block = block[:block.index("**Flags to clear first**")] if "**Flags to clear first**" in block else block
    block = block.replace(URL, "")
    assert "'" not in block and "’" not in block and "!" not in block
    assert not re.search(r"\bwe\b", block, re.I)


def test_flag_actions_are_formal_third_person():
    from client_discovery.report_model import FLAG_ACTIONS

    for flag, action in FLAG_ACTIONS.items():
        assert not re.match(r"(Ask|Confirm|Check|Plan|Start)\b", action), flag
        assert "'" not in action and not re.search(r"\bwe\b", action, re.I), flag


# --- §10.10 next step; §10.11 dedupe --------------------------------------------------------------------------

def test_next_step_from_the_first_gap():  # §10.10
    assert ("Next step: Northstar Studio works out the hours per task and returns the answers for a new DIA run "
            "(see What is missing).") in analysis(NO_HOURS)
    assert "Next step: Northstar Studio works out the people involved and returns" in analysis(PEOPLE_ONLY)


def test_next_step_without_gaps_uses_the_first_flag_action_or_kickoff():  # §10.10
    from client_discovery.report_model import flag_action

    doc = analysis(SAMPLE)
    assert f"Next step: {flag_action('TRUST_REPAIR', 'Northstar Studio')}" in doc
    clean = questionnaire(q9="None")
    assert "Next step: Confirm scope on a kickoff call." in analysis(clean)


def test_dedupe_drops_value_and_metric_flag_actions_when_steps_exist():  # §10.11
    from client_discovery.report_model import flag_action

    for text, flag in ((NO_METRIC, "NO_SUCCESS_METRIC"), (NO_COST, "VALUE_AT_STAKE_MISSING"), (PARTIAL, "VALUE_AT_STAKE_PARTIAL")):
        result = build_intake_response(text)
        assert flag in result["flags"]
        action = flag_action(flag, "Northstar Studio")
        assert action not in result["documents"]["opportunity-analysis.md"], flag
    # Other flag actions stay.
    assert flag_action("TRUST_REPAIR", "Northstar Studio") in analysis(NO_METRIC)


def test_partial_value_uses_the_no_hours_yet_wording():
    assert f"**Hours per task.** {inputs.HOURS_PARTIAL_QUESTION}" in analysis(PARTIAL)


# --- §10.15 old questionnaire --------------------------------------------------------------------------------

def test_old_questionnaire_only_gets_hours_cost_and_metric():  # §10.15
    doc = analysis(LEGACY)
    assert "**Hours per task.**" in doc and "**Hourly cost.**" in doc and "**Success metric.**" in doc
    assert "**People involved.**" not in doc and "**Systems.**" not in doc


# --- §10.14 alias parity, copy parity, markup ---------------------------------------------------------------

def _js_object(name: str) -> dict:
    body = re.search(rf"^const {name} = \{{\n(.*?)^\}};", APP_JS, re.M | re.S).group(1)
    body = body.replace("...SECTION6_ALIASES,", "")
    out = {}
    for key, value in re.findall(r"^\s*(\w+): (\[.*?\]),\s*$", body, re.M):
        out[key] = ast.literal_eval(value)
    return out


def test_js_alias_list_equals_section6_aliases():  # §10.14
    assert _js_object("SECTION6_ALIASES") == {k: list(v) for k, v in SECTION6_ALIASES.items()}
    rest = {k: list(v) for k, v in QUESTION_ALIASES.items() if k not in SECTION6_ALIASES}
    assert _js_object("QUESTION_ALIASES") == rest


def test_js_prompt_copy_equals_inputs_py():
    js = _js_object("INPUT_PROMPTS")
    assert list(js) == list(inputs.INPUTS)
    for key, (label, _question, prompt, why) in inputs.INPUTS.items():
        assert js[key] == [label, prompt, why], key


def test_prompt_markup_and_copy():
    section = INDEX[INDEX.index('<section id="inputsPrompt"'):INDEX.index("</section>", INDEX.index('<section id="inputsPrompt"'))]
    assert 'aria-labelledby="inputsPromptTitle"' in section
    assert INDEX.index('id="intakeError"') < INDEX.index('id="inputsPrompt"')
    assert INDEX.index('id="helpPopover"') < INDEX.index('id="loadSample"')   # Load sample lives in the help popover
    assert "Before you run: a few inputs would sharpen this" in section
    assert 'aria-label="Dismiss input tips"' in section
    assert "This questionnaire has no &quot;Workflows and value&quot; section." in section or \
        'This questionnaire has no "Workflows and value" section.' in section
    foot = section.index("Work these out your way, add them to section 6, then run again. Your text stays here.")
    assert foot < section.index("Prefer a hand? <span id=\"inputsPromptName\">Bash Systems &amp; Services</span> offers business consulting.")
    assert not re.search(r">[^<]*(fill|estimate|suggest|generate)[^<]*<", section, re.I)  # §10.3 (markup)


# --- Browser tests (Playwright) -------------------------------------------------------------------------------

from tests.test_persona_v12 import base_url, browser, context, open_page, run_intake  # noqa: E402,F401


def paste(page, text):
    page.evaluate("""t => { const q = document.querySelector('#questionnaire');
        if (document.querySelector('#intakeBody').hidden) setIntakeCollapsed(false);
        q.focus(); q.value = t; q.dispatchEvent(new Event('input'));
        const data = new DataTransfer(); data.setData('text/plain', t);
        q.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true })); }""", text)


def prompt_items(page):
    return page.eval_on_selector_all("#inputsPrompt li", "els => els.map(e => e.dataset.input)")


def test_prompt_after_paste_debounce_announce_and_esc(context, base_url):  # §10.12
    page = open_page(context, base_url)
    paste(page, NO_COST)
    page.wait_for_timeout(400)
    assert page.is_hidden("#inputsPrompt")  # still inside the 800ms debounce
    page.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
    assert prompt_items(page) == ["hourly_cost"]
    assert page.text_content("#statusText") == "1 key input is missing. Tips are below the composer."
    assert page.get_attribute("#questionnaire", "aria-describedby") == "intakeNote dropHint inputsPromptTitle"
    assert page.is_visible("#runAgent") and page.is_enabled("#runAgent")
    assert page.evaluate("document.activeElement.id") == "questionnaire"  # never takes focus
    page.focus("#inputsPromptDismiss")
    page.keyboard.press("Escape")
    assert page.is_hidden("#inputsPrompt")
    assert page.evaluate("document.activeElement.id") == "questionnaire"
    assert page.get_attribute("#questionnaire", "aria-describedby") == "intakeNote dropHint"
    paste(page, NO_COST_NO_METRIC)  # same intake: no second showing
    page.wait_for_timeout(1100)
    assert page.is_hidden("#inputsPrompt")
    assert page.errors == []


def test_prompt_not_shown_when_complete_or_not_a_questionnaire(context, base_url):  # §10.12, §9
    page = open_page(context, base_url)
    paste(page, SAMPLE)
    page.wait_for_timeout(1100)
    assert page.is_hidden("#inputsPrompt")
    page.evaluate("newIntake()")
    paste(page, "Hello there, this is just a note about hours and costs.")
    page.wait_for_timeout(1100)
    assert page.is_hidden("#inputsPrompt")


def test_prompt_items_drop_out_live_and_it_removes_itself(context, base_url):  # §3.2
    page = open_page(context, base_url)
    paste(page, NO_COST_NO_METRIC)
    page.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
    assert prompt_items(page) == ["hourly_cost", "success_metric"]
    assert page.text_content("#statusText") == "2 key inputs are missing. Tips are below the composer."
    page.evaluate("t => { const q = document.querySelector('#questionnaire'); q.value = t; q.dispatchEvent(new Event('input')); }",
                  NO_METRIC)
    assert prompt_items(page) == ["success_metric"]
    page.evaluate("t => { const q = document.querySelector('#questionnaire'); q.value = t; q.dispatchEvent(new Event('input')); }",
                  SAMPLE)
    assert page.is_hidden("#inputsPrompt")
    assert page.text_content("#statusText") == "2 key inputs are missing. Tips are below the composer."  # no new announcement


def test_prompt_dismiss_button_and_hides_on_run(context, base_url):
    page = open_page(context, base_url)
    paste(page, NO_COST)
    page.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
    page.click("#inputsPromptDismiss")
    assert page.is_hidden("#inputsPrompt") and page.evaluate("document.activeElement.id") == "questionnaire"
    page.evaluate("newIntake()")  # Clear starts a new intake
    paste(page, NO_COST)
    page.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
    page.click("#runAgent")
    page.wait_for_function("document.body.dataset.stage === 'results'")
    assert page.is_hidden("#inputsPrompt")
    assert page.errors == []


def test_prompt_on_load_sample_shows_nothing_for_the_complete_sample(context, base_url):
    page = open_page(context, base_url)
    page.click("#helpPopoverBtn")
    page.click("#loadSample")
    page.wait_for_function("document.querySelector('#statusText').textContent === 'Sample loaded'")
    page.wait_for_timeout(1100)
    assert page.is_hidden("#inputsPrompt")


def test_old_questionnaire_prompt(context, base_url):  # §10.15 (screen)
    page = open_page(context, base_url)
    paste(page, LEGACY)
    page.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
    assert page.is_visible("#inputsPromptOld")
    assert prompt_items(page) == ["hours", "hourly_cost", "success_metric"]


def test_see_services_link_only_when_url_set(context, base_url, monkeypatch):  # §10.7a (screen)
    page = open_page(context, base_url)
    paste(page, NO_COST)
    page.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
    assert page.is_visible("#inputsPromptLink") and page.get_attribute("#inputsPromptLink", "href") == URL
    monkeypatch.setenv("DIA_CONSULTING_INTAKE_URL", "")
    page2 = open_page(context, base_url)
    paste(page2, NO_COST)
    page2.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
    assert page2.is_hidden("#inputsPromptServices") and page2.get_attribute("#inputsPromptLink", "href") is None
    assert "Bash Systems & Services offers business consulting." in page2.inner_text("#inputsPrompt")


def test_no_generate_controls_in_prompt_or_results(context, base_url):  # §10.3
    page = open_page(context, base_url)
    paste(page, NO_COST)
    page.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
    run_intake(page, NO_COST)
    labels = page.eval_on_selector_all(
        "#inputsPrompt button, #inputsPrompt a, #resultsStage button, #resultsStage a",
        "els => els.map(e => (e.textContent + ' ' + (e.getAttribute('aria-label') || '')).trim())")
    assert labels and not [label for label in labels if re.search(r"fill|estimate|suggest|generate", label, re.I)]


def test_off_mode_prompt_shows_and_nothing_is_sent_before_run(context, base_url):  # §10.16
    page = open_page(context, base_url)
    page.evaluate("setAssistantMode('off')")
    requests = []
    page.on("request", lambda request: requests.append((request.url, request.resource_type, request.post_data)))
    paste(page, NO_COST)
    page.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
    page.wait_for_timeout(300)
    # Only lazily loaded static assets may be fetched (a web font, the app's own /static icons); no API call and
    # no data leaves.
    leaving = [r for r in requests if not (r[1] == "font" or r[0].startswith(f"{base_url}/static/"))]
    assert leaving == [], leaving
    assert not any(r[2] for r in requests)
    assert page.errors == []


def test_cta_counts_in_browser_exports_and_none_in_share_on_x(context, base_url):  # §10.6 (browser surfaces)
    page = open_page(context, base_url)
    run_intake(page, NO_COST)
    assert page.evaluate("buildMarkdownExport()").count(CTA) == 1  # Copy all and Download MD use this
    assert page.evaluate("exportJson(latestPayload)").count(CTA) <= 1
    opened = []
    page.evaluate("() => { window.open = (url) => { window.__shared = url; }; }")
    page.click("#shareX")
    shared = page.evaluate("decodeURIComponent(window.__shared)")
    assert "intent/tweet" in shared and "Bash Systems" not in shared and CTA not in shared
    assert opened == []


# --- Cabinet v2 (§7.1, §10.13, §10.13a) ------------------------------------------------------------------------

def _entry(n: int, saved: str) -> dict:
    result = build_intake_response(SAMPLE)
    return {"id": f"old-{n}", "title": f"Legacy {n}", "savedAt": saved, "questionnaire": SAMPLE, "result": result,
            "tier": result["score"]["tier"], "score": result["score"]["total_score"], "maxScore": 20}


def cabinet(page):
    return page.evaluate("JSON.parse(localStorage.getItem('dia-cabinet-v2') || '[]')")


def test_migration_from_session_v1_newest_first_then_removed(context, base_url):  # §10.13a
    v1 = [_entry(1, "2026-10-20T10:00:00Z"), _entry(2, "2026-10-22T10:00:00Z")]
    context.add_init_script(
        f"if (!sessionStorage.getItem('migrated-once')) {{ sessionStorage.setItem('migrated-once', '1');"
        f" sessionStorage.setItem('dia-cabinet-v1', {json.dumps(json.dumps(v1))}); }}")
    page = open_page(context, base_url)
    assert [item["id"] for item in cabinet(page)] == ["old-2", "old-1"]
    assert page.evaluate("sessionStorage.getItem('dia-cabinet-v1')") is None
    page.evaluate("migrateCabinet()")  # idempotent: nothing to merge, no duplicates
    assert [item["id"] for item in cabinet(page)] == ["old-2", "old-1"]
    page.evaluate("() => CabinetDrawer.open()")
    assert page.locator("#cabinetDrawer .drawer-card").count() == 2
    # Remove one card; it persists across a reload.
    page.locator("#cabinetDrawer .card-remove").first.click()
    page.wait_for_timeout(100)
    page.reload()
    assert [item["id"] for item in cabinet(page)] == ["old-1"]
    # Clear cabinet (after its confirm) empties the store; Keep leaves it alone.
    page.evaluate("() => CabinetDrawer.open()")
    page.click("#cabinetClear")
    assert page.is_visible("#cabinetClearConfirm")
    assert page.evaluate("document.activeElement.id") == "cabinetClearKeep"
    assert "Clear all saved intakes on this device?" in page.inner_text("#cabinetClearConfirm")
    assert "Export the ones you want to keep first." in page.inner_text("#cabinetClearConfirm")
    page.click("#cabinetClearKeep")
    assert len(cabinet(page)) == 1
    page.click("#cabinetClear")
    page.click("#cabinetClearYes")
    assert cabinet(page) == []
    page.reload()
    assert cabinet(page) == []
    assert page.errors == []


def test_migration_merges_with_v2_without_duplicates_and_caps_at_15(context, base_url):
    v2 = [_entry(n, f"2026-10-{n + 1:02d}T10:00:00Z") for n in range(1, 11)]
    v1 = [_entry(n, f"2026-10-{n + 1:02d}T10:00:00Z") for n in range(5, 18)]  # 5-10 overlap
    context.add_init_script(
        f"if (!sessionStorage.getItem('seeded')) {{ sessionStorage.setItem('seeded', '1');"
        f" localStorage.setItem('dia-cabinet-v2', {json.dumps(json.dumps(v2))});"
        f" sessionStorage.setItem('dia-cabinet-v1', {json.dumps(json.dumps(v1))}); }}")
    page = open_page(context, base_url)
    ids = [item["id"] for item in cabinet(page)]
    assert ids == [f"old-{n}" for n in range(17, 2, -1)] and len(ids) == 15


def test_cabinet_falls_back_to_session_storage_when_local_is_blocked(context, base_url):
    context.add_init_script("Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('blocked', 'SecurityError'); } });")
    page = open_page(context, base_url)
    run_intake(page, SAMPLE)
    assert len(page.evaluate("JSON.parse(sessionStorage.getItem('dia-cabinet-v2') || '[]')")) == 1
    assert page.errors == []


def test_rerun_keeps_prior_answers_and_files_a_linked_card(browser, base_url):  # §10.13, §7.2, §7.3
    context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/130")
    try:
        page = open_page(context, base_url)
        run_intake(page, NO_COST_NO_METRIC)
        original = cabinet(page)[0]
        page.click("#rerunUpdated")
        assert page.input_value("#questionnaire") == original["questionnaire"]
        assert page.is_visible("#questionnaire") and page.evaluate("!document.querySelector('#questionnaire').readOnly")
        assert page.text_content("#statusText") == f"Editing a copy of {original['title']}. The original stays in the cabinet."
        caret = page.evaluate("document.querySelector('#questionnaire').selectionStart")
        assert original["questionnaire"][caret:].startswith("## 6")
        # The pre-run prompt can show once in the new session.
        page.wait_for_selector("#inputsPrompt", state="visible", timeout=2000)
        assert prompt_items(page) == ["hourly_cost", "success_metric"]
        page.evaluate("t => { const q = document.querySelector('#questionnaire'); q.value = t; q.dispatchEvent(new Event('input')); }",
                      SAMPLE)
        page.click("#runAgent")
        page.wait_for_function("JSON.parse(localStorage.getItem('dia-cabinet-v2')).length === 2")
        items = cabinet(page)
        assert items[0]["previousId"] == original["id"] and items[1] == original
        page.evaluate("() => CabinetDrawer.open()")
        meta = page.locator("#cabinetDrawer .card-meta").first.text_content()
        assert meta.startswith("Re-run · ")
        page.evaluate("() => CabinetDrawer.close()")
        # Scored before / after (screen only), below the recommended tier card (v1.1.2: answer first).
        assert page.is_visible("#beforeAfter")
        assert page.evaluate("""() => !!(document.querySelector('.score-card').compareDocumentPosition(
            document.querySelector('#beforeAfter')) & Node.DOCUMENT_POSITION_FOLLOWING)""")
        assert page.locator("#beforeAfter").bounding_box()["y"] >= sum(
            page.locator(".score-card").bounding_box()[k] for k in ("y", "height"))
        previous_total = items[1]["result"]["score"]["total_score"]
        current_total = items[0]["result"]["score"]["total_score"]
        assert page.is_visible(".score-card #scoreDelta")
        assert page.inner_text("#scoreDelta") == f"Was {previous_total}, now {current_total}"
        rows = dict(zip(page.eval_on_selector_all("#beforeAfter dt", "els => els.map(e => e.textContent)"),
                        page.eval_on_selector_all("#beforeAfter dd", "els => els.map(e => e.textContent)")))
        assert list(rows) == ["Score", "Strategic value", "Value at stake", "Inputs", "Flags cleared", "New flags", "Answers changed"]
        assert rows["Value at stake"] == "not computed → about $36,960 a year"
        assert "value at stake missing" in rows["Flags cleared"] and "no success metric" in rows["Flags cleared"]
        assert rows["Answers changed"] == "Q2 hourly cost, Q3 success metric"
        assert "Scored before / after" not in page.evaluate("buildMarkdownExport()")
        assert "Scored before / after" not in page.evaluate("exportJson(latestPayload)")
        # Both cards survive closing and reopening the tab.
        page.close()
        page = open_page(context, base_url)
        assert [item["id"] for item in cabinet(page)] == [items[0]["id"], original["id"]]
        # Re-run from the card works too; the original card is unchanged.
        page.evaluate("() => CabinetDrawer.open()")
        page.locator("#cabinetDrawer .card-rerun").nth(1).click()
        assert page.input_value("#questionnaire") == original["questionnaire"]
        assert cabinet(page)[1] == original
        assert page.errors == []
    finally:
        context.close()


def test_before_after_for_a_legacy_previous_run_says_older_rules(context, base_url):  # §7.3
    page = open_page(context, base_url)
    run_intake(page, SAMPLE)
    page.evaluate("""() => { const items = readCabinet(); delete items[0].result.score.value_at_stake;
        delete items[0].result.score.rules_version; writeCabinet(items); }""")
    page.click("#rerunUpdated")
    page.click("#runAgent")
    page.wait_for_function("document.body.dataset.stage === 'results' && !document.querySelector('#beforeAfter').hidden")
    assert page.inner_text("#beforeAfter dl") == "Scored with older rules, so no comparison."
    assert not page.is_visible("#scoreDelta")
    run_intake(page, SAMPLE)  # a fresh run (not a re-run) hides both
    assert not page.is_visible("#beforeAfter") and not page.is_visible("#scoreDelta")


def test_agent_prompt_carries_rule_7():  # v1.1.2 item 4: deterministic, never skipped (§10.4 refusal check stays manual)
    from client_discovery.agent_instruction import AGENT_INSTRUCTION

    prompt = " ".join(AGENT_INSTRUCTION.split())
    assert "Recommend; never do the work (persona rule 7)." in prompt
    assert "Never fill in, guess or estimate intake values" in prompt
    for value in ("weekly hours", "people", "systems", "success metric", "hourly cost"):
        assert value in prompt
    assert "never write into the composer or any field" in prompt
    assert "reply with the matching question and two paths:" in prompt
    assert "(1) the client works it out with its own team and runs DIA again" in prompt
    assert f"(2) {CTA}" in prompt
    assert "Follow-up drafts are questions only, never answers." in prompt
    # The agent is built with exactly this instruction (checked from the source, so no SDK or API key is needed).
    tree = ast.parse((ROOT / "character.py").read_text(encoding="utf-8"))
    build = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_agent")
    keywords = [kw for node in ast.walk(build) if isinstance(node, ast.Call) for kw in node.keywords if kw.arg == "instruction"]
    assert len(keywords) == 1 and isinstance(keywords[0].value, ast.Name) and keywords[0].value.id == "AGENT_INSTRUCTION"
    assert "from client_discovery.agent_instruction import AGENT_INSTRUCTION" in (ROOT / "character.py").read_text(encoding="utf-8")


# --- v1.1.2 item 3: one questionnaire map for every "(question N)" ------------------------------------------------

# What each flag action asks the client to clear, by questionnaire field (pinned here, numbered by the map).
FLAG_QUESTION_FIELDS = {
    "VALUE_AT_STAKE_MISSING": [["workflow_tasks", "hourly_cost"]],
    "VALUE_AT_STAKE_PARTIAL": [["workflow_tasks"]],
    "NO_SUCCESS_METRIC": [["success_metric"]],
    "NO_OWNER": [["process_owner"]],
    "DATA_CLEANUP_FIRST": [["data_readiness"]],
    "SCOPE_RISK_SYSTEMS": [["systems_access"]],
    "COMPLIANCE_REVIEW": [["sensitive_data"]],
    "TRUST_REPAIR": [["past_attempts"], ["success_metric"]],  # the earlier attempt is Q9; the metric is Q3
    "HUMAN_IN_LOOP": [["exceptions"]],
    "SUPPORT_TERMS_NEEDED": [["maintenance"]],
    "ROI_WEAK": [],
}
REF = re.compile(r"\(questions? [^)]*\)")
FLAGGED = questionnaire(q4="", q5="HubSpot; no API access", q6="Spreadsheets", q8="Yes, health records",
                        q7="Duplicates; VIP requests; refunds", q10="")


def _ref(numbers: list[int]) -> str:
    if len(numbers) == 1:
        return f"(question {numbers[0]})"
    return f"(questions {', '.join(map(str, numbers[:-1]))} and {numbers[-1]})"


ALLOWED_REFS = {_ref([QUESTION_NUMBERS[f] for f in fields]) for spec in FLAG_QUESTION_FIELDS.values() for fields in spec}


def test_question_map_is_the_questionnaire_order():
    assert list(QUESTION_NUMBERS) == list(SECTION6_ALIASES)
    section = SAMPLE[SAMPLE.index("## 6. Workflows and value"):]
    rows = [line for line in section.splitlines() if line.startswith("| ") and not line.startswith(("| Question", "| ---"))]
    assert len(rows) == len(QUESTION_NUMBERS) == 11
    for field, number in QUESTION_NUMBERS.items():
        label = rows[number - 1].split("|")[1].strip().lower()
        assert any(alias in label for alias in SECTION6_ALIASES[field]), (field, number, label)
        # The field parses from the row the map says it is on.
        marker = f"marker {number} {field}"
        assert getattr(parse_questionnaire_markdown(questionnaire(**{f"q{number}": marker})), field) == marker


def test_every_flag_action_takes_its_numbers_from_the_map_for_both_questionnaire_versions():
    from client_discovery.report_model import FLAG_ACTIONS, flag_action

    assert set(FLAG_QUESTION_FIELDS) == set(FLAG_ACTIONS)
    for flag, template in FLAG_ACTIONS.items():
        assert not REF.search(template), f"{flag}: hard-coded question number"
        current = flag_action(flag, "Northstar Studio", numbered=True)
        expected = [_ref([QUESTION_NUMBERS[field] for field in fields]) for fields in FLAG_QUESTION_FIELDS[flag]]
        assert REF.findall(current) == expected, flag
        older = flag_action(flag, "Northstar Studio", numbered=False)  # no section 6: no numbers at all
        assert "question" not in older.lower() and "  " not in older and " ." not in older and " ," not in older, flag
        assert REF.sub("", current).replace(" ,", ",").replace(" .", ".") == older, flag
    trust = flag_action("TRUST_REPAIR", "Northstar Studio")
    assert "attempt (question 9)" in trust and "metric (question 3)" in trust  # Proto's bug: the metric is Q3


@pytest.mark.parametrize("version", ["current", "older"])
def test_report_flag_actions_use_the_map_or_no_number(version):
    from dataclasses import replace as dc_replace

    from client_discovery.core import score_opportunity, validate_intake
    from client_discovery.report_model import FLAG_ACTIONS, build_report, flag_action, render_documents

    text = questionnaire(q9="None") if version == "current" else LEGACY
    intake = parse_questionnaire_markdown(text)
    assert intake.workflows_section is (version == "current")
    score = dc_replace(score_opportunity(intake), flags=list(FLAG_ACTIONS))  # every flag at once
    report = build_report(intake, score, validate_intake(intake), "2026-10-23")
    assert len(report["actions"]) >= 8
    assert set(report["actions"]) <= {flag_action(f, report["client"], version == "current") for f in FLAG_ACTIONS}
    refs = REF.findall("\n".join(render_documents(report).values()))
    if version == "older":
        assert refs == []
    else:
        assert refs and set(refs) <= ALLOWED_REFS


@pytest.mark.parametrize("text", [FLAGGED, SAMPLE, LEGACY], ids=["current-flags", "current-sample", "older"])
def test_screen_payload_markdown_and_pdf_carry_the_same_mapped_numbers(text):
    from client_discovery.report_model import FLAG_ACTIONS, flag_action

    result = build_intake_response(text)
    numbered = result["intake"]["workflows_section"]
    client = result["intake"]["company_name"]
    assert set(result["flag_actions"]) <= {flag_action(f, client, numbered) for f in result["flags"] if f in FLAG_ACTIONS}
    md = "\n".join(result["documents"].values())
    pdf = pdf_text(result)
    for action in result["flag_actions"]:
        assert action in md
    payload_refs = sorted(set(REF.findall(" ".join(result["flag_actions"]))))
    if not numbered:
        assert payload_refs == [] and REF.findall(md) == [] and REF.findall(pdf) == []
        return
    assert payload_refs and set(payload_refs) <= ALLOWED_REFS
    assert sorted(set(REF.findall(md))) == payload_refs
    assert sorted(set(REF.findall(pdf))) == payload_refs


def test_no_hard_coded_question_numbers_in_the_code():
    pattern = re.compile(r"\(questions? \d")
    paths = [*(ROOT / "client_discovery").glob("*.py"), ROOT / "app.py", ROOT / "character.py", ROOT / "static/app.js",
             *(ROOT / "templates").rglob("*.html")]
    for path in paths:
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name


def test_js_question_numbers_mirror_the_map():
    block = APP_JS[APP_JS.index("const QUESTION_NUMBERS = {"):]
    block = block[:block.index("};")]
    assert {k: int(v) for k, v in re.findall(r"(\w+): (\d+)", block)} == QUESTION_NUMBERS
    assert "Q${QUESTION_NUMBERS[key]}" in APP_JS and not re.search(r'"Q\d+ ', APP_JS)


def test_answers_changed_has_no_numbers_for_an_older_questionnaire(context, base_url):
    page = open_page(context, base_url)
    labels = page.evaluate("""() => [answerLabel('success_metric', true), answerLabel('success_metric', false),
        answerLabel('budget', true)]""")
    assert labels == ["Q3 success metric", "success metric", "budget"]


# --- v1.1.2 item 1: Re-run names its item; Remove keeps its confirmation ------------------------------------------

def test_card_rerun_accessible_name_includes_the_title_and_remove_keeps_undo(context, base_url):
    page = open_page(context, base_url)
    run_intake(page, SAMPLE)
    title = cabinet(page)[0]["title"]
    page.evaluate("() => CabinetDrawer.open()")
    rerun = page.get_by_role("button", name=f"Re-run {title} with updated answers", exact=True)
    assert rerun.count() == 1 and rerun.inner_text() == "Re-run"
    remove = page.get_by_role("button", name=f"Remove {title}", exact=True)
    assert remove.count() == 1
    # Remove still confirms with an Undo toast that restores the card.
    remove.click()
    page.wait_for_selector("#toast:not([hidden])")
    assert page.inner_text("#toastText") == f"Removed “{title}”"
    assert page.evaluate("readCabinet().length") == 0
    page.locator("#toastActions button", has_text="Undo").click()
    assert [item["title"] for item in cabinet(page)] == [title] and page.evaluate("readCabinet().length") == 1
    page.evaluate("() => CabinetDrawer.open()")
    assert page.get_by_role("button", name=f"Re-run {title} with updated answers", exact=True).count() == 1
    assert page.errors == []


# --- Remove: the card and the count go at once; storage follows when the toast expires --------------------------

def _seed_three(context):
    v1 = [_entry(1, "2026-10-20T10:00:00Z"), _entry(2, "2026-10-21T10:00:00Z"), _entry(3, "2026-10-22T10:00:00Z")]
    context.add_init_script(
        f"if (!sessionStorage.getItem('seeded')) {{ sessionStorage.setItem('seeded', '1');"
        f" sessionStorage.setItem('dia-cabinet-v1', {json.dumps(json.dumps(v1))}); }}")


def _shown(page):  # cards a user can see (a collapsing card is aria-hidden and inert)
    return page.evaluate("""() => [...document.querySelectorAll('#cabinetList > li')]
        .filter((li) => !li.classList.contains('is-removing') && !li.inert).map((li) => li.dataset.id)""")


def _remove_now(page, card_id):  # clicks Remove and reads the drawer in the same task, before any animation frame
    return page.evaluate("""id => {
        const button = [...document.querySelectorAll('#cabinetList > li')].find((li) => li.dataset.id === id).querySelector('.card-remove');
        button.focus();  // as a real click or keypress would
        button.click();
        return { summary: document.querySelector('#cabinetSummary').textContent,
                 empty: !document.querySelector('#cabinetEmpty').hidden,
                 inDom: [...document.querySelectorAll('#cabinetList > li')].map((li) => li.dataset.id),
                 shown: [...document.querySelectorAll('#cabinetList > li')].filter((li) => !li.inert).map((li) => li.dataset.id),
                 collapsing: [...document.querySelectorAll('#cabinetList > li.is-removing')].map((li) => li.dataset.id) };
    }""", card_id)


def test_remove_collapses_the_card_and_updates_the_count_at_once_and_undo_restores_both(context, base_url):
    _seed_three(context)
    page = open_page(context, base_url)
    page.emulate_media(reduced_motion="no-preference")
    page.evaluate("() => CabinetDrawer.open()")
    assert _shown(page) == ["old-3", "old-2", "old-1"] and page.inner_text("#cabinetSummary") == "3 intakes"
    now = _remove_now(page, "old-2")
    assert now["summary"] == "2 intakes" and now["shown"] == ["old-3", "old-1"] and now["collapsing"] == ["old-2"]
    assert page.evaluate("document.activeElement.closest('li')?.dataset.id") == "old-1"  # focus moves on, not lost
    page.wait_for_function("![...document.querySelectorAll('#cabinetList > li')].some((li) => li.dataset.id === 'old-2')")
    assert page.inner_text("#toastText") == "Removed “Legacy 2”"
    # Storage keeps it until the toast expires; every read already leaves it out.
    assert [item["id"] for item in cabinet(page)] == ["old-3", "old-2", "old-1"]
    assert page.evaluate("readCabinet().map((i) => i.id)") == ["old-3", "old-1"]
    # Undo: the card is back in its place and the count with it.
    page.locator("#toastActions button", has_text="Undo").click()
    assert _shown(page) == ["old-3", "old-2", "old-1"] and page.inner_text("#cabinetSummary") == "3 intakes"
    assert [item["id"] for item in cabinet(page)] == ["old-3", "old-2", "old-1"]
    assert page.evaluate("document.activeElement.closest('li')?.dataset.id") == "old-2"
    page.wait_for_timeout(400)  # the late collapse callback must not undo the Undo
    assert _shown(page) == ["old-3", "old-2", "old-1"]
    assert page.errors == []


def test_removal_is_final_when_the_toast_expires_or_the_page_is_left(context, base_url):
    _seed_three(context)
    page = open_page(context, base_url)
    page.evaluate("() => CabinetDrawer.open()")
    _remove_now(page, "old-3")
    assert [item["id"] for item in cabinet(page)] == ["old-3", "old-2", "old-1"]
    page.evaluate("hideToast()")  # the toast's timer (or ×) ends the window
    assert [item["id"] for item in cabinet(page)] == ["old-2", "old-1"]
    assert page.locator("#toastActions button").count() == 0 or page.is_hidden("#toast")
    # A second Remove while a toast is up makes the first one final.
    _remove_now(page, "old-2")
    page.wait_for_timeout(50)
    _remove_now(page, "old-1")
    assert [item["id"] for item in cabinet(page)] == ["old-1"]  # old-2 final, old-1 pending
    # Reload during the toast window: leaving the page ends the window, so screen, count and storage agree.
    page.reload()
    assert cabinet(page) == [] and page.evaluate("readCabinet().length") == 0
    page.evaluate("() => CabinetDrawer.open()")
    assert _shown(page) == [] and page.is_visible("#cabinetEmpty") and page.inner_text("#cabinetSummary") == ""
    assert page.errors == []


def test_removing_the_last_card_shows_the_empty_state_at_once_and_undo_brings_it_back(context, base_url):
    page = open_page(context, base_url)
    run_intake(page, SAMPLE)
    card_id = cabinet(page)[0]["id"]
    page.evaluate("() => CabinetDrawer.open()")
    now = _remove_now(page, card_id)
    assert now["summary"] == "" and now["empty"] and now["shown"] == []
    assert page.evaluate("document.activeElement.id") == "cabinetStart"
    assert page.is_hidden("#cabinetClear")
    page.locator("#toastActions button", has_text="Undo").click()
    assert _shown(page) == [card_id] and page.inner_text("#cabinetSummary") == "1 intake" and page.is_hidden("#cabinetEmpty")
    assert page.errors == []


def test_reduced_motion_removes_the_card_instantly(context, base_url):
    _seed_three(context)
    page = open_page(context, base_url)
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("() => CabinetDrawer.open()")
    now = _remove_now(page, "old-2")
    assert now["inDom"] == ["old-3", "old-1"] and now["collapsing"] == [] and now["summary"] == "2 intakes"
    page.locator("#toastActions button", has_text="Undo").click()
    assert _shown(page) == ["old-3", "old-2", "old-1"] and page.inner_text("#cabinetSummary") == "3 intakes"
    assert page.errors == []


def test_removal_becomes_final_when_the_toast_timer_runs_out(context, base_url):
    _seed_three(context)
    page = context.new_page()
    page.clock.install()
    page.goto(base_url)
    page.wait_for_function("typeof CabinetDrawer !== 'undefined'")
    page.evaluate("() => CabinetDrawer.open()")
    _remove_now(page, "old-1")
    page.clock.run_for(9_000)
    assert [item["id"] for item in cabinet(page)] == ["old-3", "old-2", "old-1"] and page.is_visible("#toast")
    page.clock.run_for(1_500)
    assert page.is_hidden("#toast") and [item["id"] for item in cabinet(page)] == ["old-3", "old-2"]
    assert _shown(page) == ["old-3", "old-2"] and page.inner_text("#cabinetSummary") == "2 intakes"
