"""dia-savant inputs addendum v1.1.1 (proto/dia-savant-inputs-addendum.md §10, flows.md v9.0.1 §2.9/§3.1/§5.2):
the pre-run inputs prompt, "Recommended next steps" with its two paths, the cabinet's move to localStorage, and
Re-run with updated answers. One test (or group) per §10 acceptance test is marked "§10.n"."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from app import build_intake_response, consulting_config
from client_discovery import inputs
from client_discovery.core import QUESTION_ALIASES, SECTION6_ALIASES, parse_questionnaire_markdown
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


def pdf_text(result: dict) -> str:
    from client_discovery.report import render_pdf

    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(render_pdf(result))
        handle.flush()
        text = subprocess.run(["pdftotext", handle.name, "-"], capture_output=True, text=True, check=True).stdout
    return " ".join(text.split())


def pdf_links(result: dict) -> list[str]:
    from client_discovery.report import render_pdf

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
    from client_discovery.report_model import FLAG_ACTIONS

    doc = analysis(SAMPLE)
    assert f"Next step: {FLAG_ACTIONS['TRUST_REPAIR'].format(client='Northstar Studio')}" in doc
    clean = questionnaire(q9="None")
    assert "Next step: Confirm scope on a kickoff call." in analysis(clean)


def test_dedupe_drops_value_and_metric_flag_actions_when_steps_exist():  # §10.11
    from client_discovery.report_model import FLAG_ACTIONS

    for text, flag in ((NO_METRIC, "NO_SUCCESS_METRIC"), (NO_COST, "VALUE_AT_STAKE_MISSING"), (PARTIAL, "VALUE_AT_STAKE_PARTIAL")):
        result = build_intake_response(text)
        assert flag in result["flags"]
        action = FLAG_ACTIONS[flag].format(client="Northstar Studio")
        assert action not in result["documents"]["opportunity-analysis.md"], flag
    # Other flag actions stay.
    assert FLAG_ACTIONS["TRUST_REPAIR"].format(client="Northstar Studio") in analysis(NO_METRIC)


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
    assert INDEX.index('id="intakeError"') < INDEX.index('id="inputsPrompt"') < INDEX.index('id="loadSample"')
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
    assert page.text_content("#status") == "1 key input is missing. Tips are below the composer."
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
    assert page.text_content("#status") == "2 key inputs are missing. Tips are below the composer."
    page.evaluate("t => { const q = document.querySelector('#questionnaire'); q.value = t; q.dispatchEvent(new Event('input')); }",
                  NO_METRIC)
    assert prompt_items(page) == ["success_metric"]
    page.evaluate("t => { const q = document.querySelector('#questionnaire'); q.value = t; q.dispatchEvent(new Event('input')); }",
                  SAMPLE)
    assert page.is_hidden("#inputsPrompt")
    assert page.text_content("#status") == "2 key inputs are missing. Tips are below the composer."  # no new announcement


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
    page.click("#loadSample")
    page.wait_for_function("document.querySelector('#status').textContent === 'Sample loaded'")
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
    # Only a lazily loaded web font may be fetched (the prompt's bold weight); no API call and no data leaves.
    assert [r for r in requests if r[1] != "font"] == [], requests
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
        assert page.text_content("#status") == f"Editing a copy of {original['title']}. The original stays in the cabinet."
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
        # Scored before / after (screen only).
        assert page.is_visible("#beforeAfter")
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


def test_agent_prompt_carries_rule_7():  # §10.4 is manual (AI output); the instruction itself is pinned here
    character = pytest.importorskip("character")
    try:
        prompt = character.build_agent().instruction
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"agent unavailable: {error}")
    assert "never do the work (persona rule 7)" in prompt
    assert "Never fill in, guess or estimate intake values" in prompt
    assert "Bash Systems & Services offers business" in " ".join(prompt.split())
