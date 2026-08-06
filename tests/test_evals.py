"""The eval harness is itself production code, so it gets tests.

An eval suite that silently passes because it stopped checking anything is
worse than no eval suite, because it manufactures confidence. These tests
assert that the harness genuinely exercises the system and genuinely fails
when the system is wrong.
"""

import json
from pathlib import Path

import pytest

from evals.run_evals import Scorecard, _run_access, run

SCENARIOS = json.loads(
    (Path(__file__).parent.parent / "evals" / "scenarios.json").read_text(encoding="utf-8")
)


def test_every_ground_truth_scenario_passes():
    card = run()
    failures = [f"{r.scenario_id}: {r.detail}" for r in card.results if not r.passed]
    assert not failures, "ground-truth regressions:\n" + "\n".join(failures)


def test_the_suite_actually_checks_a_meaningful_number_of_things():
    assert run().total >= 20


def test_every_scenario_kind_has_a_runner():
    from evals.run_evals import RUNNERS

    kinds = {s["kind"] for s in SCENARIOS["scenarios"]}
    assert kinds <= set(RUNNERS) | {"catalog"}


def test_scenarios_cover_both_outcomes():
    """A suite that only asserts failures would pass on a system that rejects
    everything, and vice versa."""
    text = json.dumps(SCENARIOS)
    assert '"pass"' in text and '"fail"' in text
    assert '"unverified"' in text


def test_harness_reports_failure_when_expectation_is_wrong():
    """Deliberately assert something false and confirm the harness notices."""
    card = Scorecard()
    _run_access(
        {
            "id": "deliberately-wrong",
            "kind": "access",
            "unit": "unit01",
            "item": {"w": 300, "d": 95, "h": 84},
            "expect": [{"room": "living_dining", "status": "pass"}],
        },
        card,
    )
    assert not card.ok
    assert "want pass" in card.results[0].detail


def test_scorecard_is_ok_only_when_everything_passed():
    from evals.run_evals import Result

    assert Scorecard([Result("a", "fit", True)]).ok
    assert not Scorecard([Result("a", "fit", True), Result("b", "fit", False)]).ok
