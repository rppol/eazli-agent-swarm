"""Ground-truth evaluation runner for the eazli swarm's factual layer.

Runs every scenario in scenarios.json directly against the geometry engine,
the catalogue parser and the retrieval index. No language model is involved,
so a failure here is unambiguous: something is arithmetically or factually
wrong, not merely phrased badly.

    PYTHONPATH=. uv run python evals/run_evals.py
    PYTHONPATH=. uv run python evals/run_evals.py --json

Exits non-zero on any failure, so it works as a CI gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app import store
from app.catalog import parse_capture
from app.geometry import Dims, Placement, check_access_path, check_fit, validate_layout
from app.home import load_home
from app.main import PLANS

SCENARIOS = Path(__file__).parent / "scenarios.json"


@dataclass
class Result:
    scenario_id: str
    kind: str
    passed: bool
    detail: str = ""


@dataclass
class Scorecard:
    results: list[Result] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def ok(self) -> bool:
        return self.passed == self.total


def _dims(spec: dict) -> Dims:
    return Dims(
        w=spec.get("w"), d=spec.get("d"), h=spec.get("h"),
        confidence=spec.get("confidence", "stated"),
    )


# --------------------------------------------------------------------------
# per-kind runners
# --------------------------------------------------------------------------

def _run_access(s: dict, card: Scorecard) -> None:
    home = load_home()
    dims = _dims(s["item"])
    for expectation in s["expect"]:
        verdict = check_access_path(dims, home.route_to(s["unit"], expectation["room"]))
        ok = verdict.status == expectation["status"]
        detail = f"{expectation['room']}: got {verdict.status}, want {expectation['status']}"

        needle = expectation.get("reason_contains")
        if ok and needle:
            ok = any(needle.lower() in r.lower() for r in verdict.reasons)
            if not ok:
                detail = f"{expectation['room']}: no reason mentioning {needle!r}"

        card.results.append(Result(s["id"], s["kind"], ok, "" if ok else detail))


def _run_access_carton(s: dict, card: Scorecard) -> None:
    home = load_home()
    route = home.route_to(s["unit"], s["room"])

    assembled = check_access_path(_dims(s["item"]), route).status
    carton = check_access_path(_dims(s["carton"]), route).status

    for label, got, want in (
        ("assembled", assembled, s["expect_assembled"]),
        ("carton", carton, s["expect_carton"]),
    ):
        card.results.append(
            Result(s["id"], s["kind"], got == want,
                   "" if got == want else f"{label}: got {got}, want {want}")
        )


def _run_fit(s: dict, card: Scorecard) -> None:
    home = load_home()
    room = home.unit(s["unit"]).room(s["room"]).to_room()
    item = s["item"]
    verdict = check_fit(
        Placement(item.get("id", "item"), _dims(item), x=s["x"], y=s["y"]), room
    )
    ok = verdict.status == s["expect_status"]
    detail = f"got {verdict.status}, want {s['expect_status']}"

    needle = s.get("reason_contains")
    if ok and needle:
        ok = any(needle.lower() in r.lower() for r in verdict.reasons)
        if not ok:
            detail = f"no reason mentioning {needle!r}; got {verdict.reasons}"

    card.results.append(Result(s["id"], s["kind"], ok, "" if ok else detail))


def _run_layout(s: dict, card: Scorecard) -> None:
    home = load_home()
    room = home.unit(s["unit"]).room(s["room"]).to_room()
    placements = [
        Placement(
            p["id"], _dims(p), x=p["x"], y=p["y"], role=p.get("role"), facing=p.get("facing", "S")
        )
        for p in s["placements"]
    ]
    verdict = validate_layout(room, placements)
    ok = verdict.status == s["expect_status"]
    detail = f"got {verdict.status}, want {s['expect_status']}"

    needle = s.get("reason_contains")
    if ok and needle:
        ok = any(needle.lower() in r.lower() for r in verdict.reasons)
        if not ok:
            detail = f"no reason mentioning {needle!r}"

    card.results.append(Result(s["id"], s["kind"], ok, "" if ok else detail))


def _run_quota(s: dict, card: Scorecard) -> None:
    limits = PLANS[s["plan"]]
    checks = []
    if "expect_layouts_allowed" in s:
        checks.append((
            "layouts",
            s.get("layouts_used", 0) < limits["layouts"],
            s["expect_layouts_allowed"],
        ))
    if "expect_rooms_allowed" in s:
        checks.append((
            "rooms",
            s.get("rooms_used", 0) < limits["rooms"],
            s["expect_rooms_allowed"],
        ))
    for label, got, want in checks:
        card.results.append(
            Result(s["id"], s["kind"], got == want,
                   "" if got == want else f"{label}: got {got}, want {want}")
        )


def _run_catalog(s: dict, card: Scorecard, catalog: dict) -> None:
    for asin in s["asins"]:
        product = catalog.get(asin)
        if product is None:
            card.results.append(Result(s["id"], s["kind"], False, f"{asin} not in catalogue"))
            continue

        ok = product.usable == s["expect_usable"]
        detail = f"{asin}: usable={product.usable}, want {s['expect_usable']}"

        flag = s.get("expect_flag")
        if ok and flag:
            ok = flag in product.flags
            if not ok:
                detail = f"{asin}: missing flag {flag!r}; has {product.flags}"

        card.results.append(Result(s["id"], s["kind"], ok, "" if ok else detail))


def _run_retrieval(s: dict, card: Scorecard) -> None:
    hits = store.search("eazli_kb", s["query"], k=5)
    needle = s["expect_text_contains"].lower()
    ok = any(needle in h["text"].lower() for h in hits)
    detail = "" if ok else f"top-5 for {s['query']!r} contained no {needle!r}"
    card.results.append(Result(s["id"], s["kind"], ok, detail))


def _run_provenance(s: dict, card: Scorecard) -> None:
    rule = s["forbid_source_for_doc_type"]
    hits = store.search("eazli_kb", s["query"], k=8)
    offenders = [
        h for h in hits
        if h.get("doc_type") == rule["doc_type"]
        and rule["must_not_contain"] in str(h.get("source_url", ""))
    ]
    ok = not offenders
    detail = "" if ok else f"{len(offenders)} analysis chunk(s) attributed to eazli"
    card.results.append(Result(s["id"], s["kind"], ok, detail))


RUNNERS = {
    "access": _run_access,
    "access_carton": _run_access_carton,
    "fit": _run_fit,
    "layout": _run_layout,
    "quota": _run_quota,
    "retrieval": _run_retrieval,
    "provenance": _run_provenance,
}


def run() -> Scorecard:
    spec = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    catalog = {p.asin: p for p in parse_capture()}
    card = Scorecard()

    for scenario in spec["scenarios"]:
        kind = scenario["kind"]
        if kind == "catalog":
            _run_catalog(scenario, card, catalog)
        else:
            RUNNERS[kind](scenario, card)
    return card


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    card = run()

    if args.json:
        print(json.dumps({
            "passed": card.passed,
            "total": card.total,
            "ok": card.ok,
            "failures": [
                {"id": r.scenario_id, "kind": r.kind, "detail": r.detail}
                for r in card.results if not r.passed
            ],
        }, indent=2))
    else:
        by_kind: dict[str, list[Result]] = {}
        for r in card.results:
            by_kind.setdefault(r.kind, []).append(r)

        print("eazli ground-truth evals\n" + "=" * 52)
        for kind, results in by_kind.items():
            good = sum(r.passed for r in results)
            print(f"  {kind:<16} {good:>2}/{len(results):<3} {'ok' if good == len(results) else 'FAIL'}")
        print("=" * 52)
        print(f"  {'total':<16} {card.passed:>2}/{card.total}")

        failures = [r for r in card.results if not r.passed]
        if failures:
            print("\nfailures:")
            for r in failures:
                print(f"  [{r.scenario_id}] {r.detail}")

    sys.exit(0 if card.ok else 1)


if __name__ == "__main__":
    main()
