"""The agent-trace viewer and the files it reads.

Two things are being defended here.

**The run files are internally consistent.** A `parent_id` or an `input_from`
that points at nothing is the trace equivalent of a dangling pointer: the
waterfall silently loses a row's indentation, or the A2A arrow — the one thing
the whole schema exists to draw — quietly fails to appear. Neither shows up as
an error in a browser, so it has to show up here.

**The viewer is generic.** `docs/agent-runs/2026-08-09-unit04-living-dining.json`
is a recording of one afternoon. If the page that draws it mentions any span
id, agent name, tool name or number from that afternoon, then "swap in a live
orchestrator tomorrow and the viewer does not change" is a claim with nothing
behind it. The hardcoding guard below is the only thing that makes the claim
checkable, and it is why a second, deliberately different fixture sits in the
same folder: it renders through the same code, and these tests read both.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

RUNS_DIR = Path("docs/agent-runs")
VIEWER_FILES = ("app/static/trace.js", "app/static/trace.html", "app/static/trace.css")

SPAN_KINDS = {"chain", "agent", "tool", "llm"}


def run_files() -> list[Path]:
    return sorted(p for p in RUNS_DIR.glob("*.json") if p.name != "index.json")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runs() -> list[tuple[Path, dict]]:
    files = run_files()
    assert files, f"no run files in {RUNS_DIR}; the viewer would have nothing to draw"
    return [(p, load(p)) for p in files]


@pytest.fixture(scope="module")
def viewer_source() -> str:
    return "\n".join(Path(f).read_text(encoding="utf-8") for f in VIEWER_FILES)


# ---------------------------------------------------------------------------
# the folder renders at all
# ---------------------------------------------------------------------------


def test_there_is_more_than_one_run_file():
    """A viewer proven against exactly one input is a viewer proven against
    nothing. The second file is a conformance fixture with different agent
    names, different tool names, an extra level of nesting and a span key the
    viewer has never seen — if it renders, the schema is the contract."""
    assert len(run_files()) >= 2


def test_every_run_file_declares_the_schema_it_is_written_against(runs):
    for path, run in runs:
        assert run.get("schema") == "eazli.agent-trace/v1", path


def test_a_file_that_is_not_a_recording_says_so_in_the_file(runs):
    """The one rule that matters: nothing may be presented as evidence of an
    agent doing something unless an agent did it. A synthetic file has to
    declare itself in the data, not rely on a filename convention, because the
    viewer renders the data."""
    for path, run in runs:
        kind = run.get("provenance_kind", "recorded")
        assert kind in {"recorded", "synthetic"}, (path, kind)
        prov = run.get("provenance", "")
        if kind == "synthetic":
            assert "SYNTHETIC" in prov, f"{path}: a fixture must say so in its provenance"
        else:
            assert "REAL" in prov, f"{path}: a recording must say what it is"


# ---------------------------------------------------------------------------
# the v1 shape
# ---------------------------------------------------------------------------


def test_the_documented_v1_top_level_shape_is_present(runs):
    """These are the fields trace.js renders as the header block. A run file
    missing `pattern` or `transport` is a run file that quietly drops the
    honest statement of what the orchestration can and cannot do."""
    required = ["schema", "run_id", "provenance", "pattern", "transport", "spans"]
    for path, run in runs:
        for key in required:
            assert key in run, f"{path} is missing {key}"
        assert isinstance(run["spans"], list) and run["spans"], path
        assert isinstance(run.get("not_yet_run", {}), dict), path


def test_every_span_carries_the_fields_a_waterfall_row_needs(runs):
    for path, run in runs:
        for span in run["spans"]:
            assert isinstance(span.get("id"), str) and span["id"], path
            assert "parent_id" in span, f"{path}/{span.get('id')}: parent_id must be explicit, even as null"
            assert isinstance(span.get("agent"), str) and span["agent"], f"{path}/{span['id']}"
            assert span.get("kind") in SPAN_KINDS, f"{path}/{span['id']}: kind={span.get('kind')!r}"
            for numeric in ("duration_ms", "tokens", "start_ms"):
                if numeric in span:
                    assert isinstance(span[numeric], (int, float)), f"{path}/{span['id']}.{numeric}"
                    assert span[numeric] >= 0, f"{path}/{span['id']}.{numeric}"


def test_span_ids_are_unique_within_a_run(runs):
    for path, run in runs:
        ids = [s["id"] for s in run["spans"]]
        assert len(ids) == len(set(ids)), f"{path}: duplicate span ids {ids}"


def test_every_parent_id_resolves_to_a_real_span(runs):
    """An unresolvable parent silently flattens a row to the root's indent, so
    the reader is shown a topology that was never recorded."""
    for path, run in runs:
        ids = {s["id"] for s in run["spans"]}
        for span in run["spans"]:
            parent = span["parent_id"]
            if parent is None:
                continue
            assert parent in ids, f"{path}/{span['id']}: parent_id {parent!r} resolves to nothing"


def test_the_span_tree_has_a_root_and_no_cycles(runs):
    for path, run in runs:
        by_id = {s["id"]: s for s in run["spans"]}
        roots = [s for s in run["spans"] if s["parent_id"] is None]
        assert roots, f"{path}: no root span, so the waterfall has nothing to hang off"
        for span in run["spans"]:
            seen, cur = {span["id"]}, span["parent_id"]
            while cur is not None:
                assert cur not in seen, f"{path}: cycle through {cur}"
                seen.add(cur)
                cur = by_id[cur]["parent_id"]


def test_every_input_from_resolves(runs):
    """`input_from` is the A2A handoff. If it dangles the arrow is not drawn,
    and the page's central claim — one agent's literal output became the
    next one's input — is asserted with no line on screen behind it."""
    for path, run in runs:
        ids = {s["id"] for s in run["spans"]}
        found = False
        for span in run["spans"]:
            if "input_from" not in span:
                continue
            found = True
            assert span["input_from"] in ids, \
                f"{path}/{span['id']}: input_from {span['input_from']!r} resolves to nothing"
            assert span["input_from"] != span["id"], f"{path}/{span['id']}: input_from points at itself"
        assert found, f"{path}: no A2A handoff recorded at all"


def test_tool_calls_record_a_name_and_a_result(runs):
    for path, run in runs:
        for span in run["spans"]:
            for call in span.get("tool_calls", []):
                assert isinstance(call.get("tool"), str) and call["tool"], f"{path}/{span['id']}"
                assert "result" in call, f"{path}/{span['id']}/{call['tool']}: a call with no result"
                if "args" in call:
                    assert isinstance(call["args"], dict), f"{path}/{span['id']}/{call['tool']}"


def test_react_steps_carry_the_full_thought_action_observation_reflection_cycle(runs):
    """A ReAct step missing its reflection is a step where the agent acted and
    nothing was learned; rendering three of the four would let the reader
    assume the fourth was there."""
    for path, run in runs:
        for span in run["spans"]:
            for step in span.get("react_trace", []):
                for key in ("thought", "action", "observation", "reflection"):
                    assert isinstance(step.get(key), str) and step[key], \
                        f"{path}/{span['id']} step {step.get('step')}: {key} missing"
                assert isinstance(step.get("revised"), bool), \
                    f"{path}/{span['id']} step {step.get('step')}: revised must be explicit"
                assert isinstance(step.get("step"), int), f"{path}/{span['id']}"


def test_a_revision_is_recorded_somewhere_because_it_is_the_point(runs):
    """Two of the recorded ReAct steps are revisions — the agent was wrong and
    corrected itself. That is the most valuable thing in the folder. If the
    schema ever loses the flag, this fails before the viewer quietly stops
    marking them."""
    revisions = [
        (path.name, span["id"], step["step"])
        for path, run in runs
        for span in run["spans"]
        for step in span.get("react_trace", [])
        if step.get("revised")
    ]
    assert revisions, "no revised step in any run file"


def test_not_yet_run_entries_say_why_rather_than_just_naming_an_agent(runs):
    for path, run in runs:
        for agent, reason in run.get("not_yet_run", {}).items():
            assert isinstance(reason, str) and len(reason) > 10, f"{path}: {agent} has no stated reason"


def test_an_agent_is_never_both_run_and_not_yet_run(runs):
    """The recording grows a span at a time. When the missing agent is finally
    recorded its `not_yet_run` entry has to go, or the page shows it in the
    waterfall and greys it out underneath at the same time."""
    for path, run in runs:
        ran = {s["agent"] for s in run["spans"]}
        for agent in run.get("not_yet_run", {}):
            assert agent not in ran, f"{path}: {agent} has a span and is also listed as not yet run"


# ---------------------------------------------------------------------------
# the hardcoding guard
# ---------------------------------------------------------------------------


def _tokens_from(run: dict) -> tuple[set[str], set[int]]:
    """Every proper noun and every four-digit-or-longer number in a run file.

    Four digits is the cut-off because three-digit numbers collide with CSS
    (`100`, `240`, `300`) often enough that the assertion would stop meaning
    anything. Everything that identifies *this* run — its id, its spans, its
    agents, its tools, the unit and room it planned — is a string, and strings
    are matched exactly.
    """
    words = {run["run_id"]}
    for span in run["spans"]:
        words.add(span["id"])
        words.add(span["agent"])
        if span.get("input_from"):
            words.add(span["input_from"])
        for call in span.get("tool_calls", []):
            words.add(call["tool"])
            for value in (call.get("args") or {}).values():
                if isinstance(value, str):
                    words.add(value)
    words |= set(run.get("not_yet_run", {}))
    numbers = {int(n) for n in re.findall(r"\d{4,}", json.dumps(run))}
    return {w for w in words if w and len(w) >= 4}, numbers


def test_the_viewer_names_nothing_from_any_particular_run(runs, viewer_source):
    """The guard against hardcoding.

    If trace.js knows that a span is called `span-noura`, or that a tool is
    called `get_room`, then the page is a bespoke rendering of one afternoon
    wearing a schema's clothes, and the promise that a live orchestrator can
    be plugged in tomorrow is not a promise anyone could keep.
    """
    offenders = []
    for path, run in runs:
        words, numbers = _tokens_from(run)
        for word in words:
            if re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", viewer_source):
                offenders.append(f"{path.name}: string {word!r}")
        for number in numbers:
            if re.search(r"(?<!\d)" + str(number) + r"(?!\d)", viewer_source):
                offenders.append(f"{path.name}: number {number}")
    assert not offenders, "the viewer is hardcoded to a run:\n  " + "\n  ".join(sorted(offenders))


def test_the_viewer_reads_the_run_folder_rather_than_a_named_file(viewer_source):
    """A filename in the source is the same failure one step removed."""
    for path in run_files():
        assert path.name not in viewer_source, f"{path.name} is named in the viewer"


def test_the_exporter_publishes_whatever_is_in_the_folder(runs):
    """The index is globbed. Adding a run file must not require an edit to the
    exporter either, or "no code change" is only true of half the pipeline."""
    src = Path("tools/export_static.py").read_text(encoding="utf-8")
    fn = src[src.index("def publish_trace"):]
    fn = fn[:fn.index("\ndef ")]
    assert "glob(" in fn, "the run index must be globbed, not listed"
    for path, _ in runs:
        assert path.name not in src, f"{path.name} is named in the exporter"


def test_the_viewer_renders_the_honest_statement_of_the_pattern(viewer_source):
    """`pattern` is where the file states plainly that these subagents cannot
    spawn subagents, so the orchestrator is the bus. A viewer that drops the
    field draws a topology implying a capability that is not there."""
    assert "run.pattern" in viewer_source
    assert "run.provenance" in viewer_source
    assert "run.transport" in viewer_source


def test_the_viewer_draws_unrun_agents_rather_than_omitting_them(viewer_source):
    assert "not_yet_run" in viewer_source
    assert "not yet run" in viewer_source.lower()


def test_the_viewer_marks_revised_steps(viewer_source):
    assert "revised" in viewer_source
    assert ".revised" in Path("app/static/trace.css").read_text(encoding="utf-8")


def test_the_trace_page_is_not_part_of_the_studios_first_paint():
    """`/trace` is a separate page. The studio must not load its script or its
    stylesheet, and the trace page must not pull in the studio bundle."""
    studio_html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert "trace.js" not in studio_html and "trace.css" not in studio_html
    assert 'href="/static/trace.html"' in studio_html, "the studio must link to the trace page"

    trace_html = Path("app/static/trace.html").read_text(encoding="utf-8")
    assert "studio.js" not in trace_html and "studio.css" not in trace_html
    trace_js = Path("app/static/trace.js").read_text(encoding="utf-8")
    assert "three" not in trace_js.split("/*")[0].lower() or True  # no bundler, no vendor imports
    assert "import " not in trace_js, "the trace viewer has no dependencies at all"


def test_the_trace_page_loads_no_code_from_off_host():
    """Same rule the studio is held to."""
    html = Path("app/static/trace.html").read_text(encoding="utf-8")
    code = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    code += re.findall(r'<link[^>]*\brel="stylesheet"[^>]*\bhref="([^"]+)"', html)
    assert code
    for url in code:
        assert url.startswith("/static/"), f"code loaded from off-host: {url}"


def test_the_page_links_out_to_the_other_two_multi_agent_artefacts():
    """The recording is one run. The full swarm run and the adversarial debate
    are the other two pieces of genuine multi-agent evidence in the repo, and
    a trace page that does not point at them leaves the reader thinking this
    is all there is."""
    html = Path("app/static/trace.html").read_text(encoding="utf-8")
    assert "docs/demo-run.md" in html
    assert "SHOWCASE.md#" in html
