---
description: Run the eazli agent swarm on a home request
argument-hint: [request, e.g. "living room feels cramped, 8000 SAR, warm and minimal"]
---

Run the eazli agent swarm end to end on this request:

**$ARGUMENTS**

## Before you start

Confirm the tool service is up:

```bash
curl -s localhost:8000/health
```

All three collections (`eazli_kb`, `design_principles`, `products`) must report `true`. If the service is down, start it with `uv run uvicorn app.main:app --port 8000` and wait for health to pass. If a collection is missing, rebuild it with `PYTHONPATH=. uv run python ingest/build_kb.py` or `ingest/build_catalog.py`.

## You are the dispatcher

Claude Code subagents cannot spawn other subagents, so orchestration happens here, in the main thread. That is a real constraint and it has an upside: every handoff passes through one place, which is where quota and policy get enforced and where the audit trail lives.

Run this sequence. Do not skip the auditor, and do not do the specialists' work yourself.

**1. Intake — `zeina-guide`**

Pass the raw request. Get back a brief and a routing decision.

If the brief contains `open_questions`, ask the user those questions and wait. Do not guess past a question Zeina judged to be blocking — the whole point of the intake step is that it refuses to proceed on assumptions.

**2. Layout — `noura-designer`**

Only if Zeina routed to her. Pass the brief. Get back validated slots.

Check that `validation.status` is `pass`. If it is not, Noura has not finished; send it back with the failing reasons.

**3. Sourcing — `adam-advisor`**

Pass the brief and Noura's slots. Get back picks, rejections, and unverified items.

**4. Audit — `fit-auditor`**

Pass everything: the brief, the slots, and Adam's output. This agent has authority to reject.

If the verdict is `rejected`, return to whichever agent owns each blocker — Noura for layout problems, Adam for product problems — with the auditor's specific measurement. Re-run the audit afterwards.

Allow at most two correction rounds. If it still fails, report honestly that the request could not be satisfied within its constraints, and say which constraint is binding. A refusal with a measurement is a better outcome than a plan that does not hold.

## What to show the user

Write it out as prose, not raw JSON:

1. **The plan** — each slot, the product chosen, its price, and where it goes.
2. **What was ruled out and why** — quote the actual measurement. This is the most valuable part of the output; do not compress it away.
3. **Anything unverified** — items with missing or conflicting dimensions, and the assumed values (ceiling height, door widths) that any verdict leaned on.
4. **The money** — itemised total against the budget, in SAR.
5. **Plan usage** — layouts and re-designs consumed, against Explorer or Active limits.

## Boundaries

- Nothing here completes a purchase. eazli's AI Agent policy is explicit that agents have no authority to do that. The run ends at a recommendation the user can act on.
- Every spatial number shown to the user must come from a tool. If you find yourself computing a clearance in your head, call the tool instead.
- Do not smooth over the rejections to make the result look cleaner. They are the evidence that the system is doing anything at all.
