# Reasoning protocol: ReAct with a gradeable trace

Every worker agent in this swarm reasons in an explicit **ReAct** loop —
*Thought → Action → Observation → Reflection* — and emits the loop as
structured data alongside its answer.

The trace is not decoration. It is the artifact that makes three things possible
that a bare answer cannot support.

## Why a trace, specifically

**1. It separates what was reasoned from what was measured.**

This system's entire claim is that spatial answers come from Python, not from a
language model. A trace makes that auditable: every `observation` is a verbatim
tool result, and every `thought` is model reasoning. If a number appears in a
thought that never appeared in an observation, the model invented it. That is
mechanically checkable, and `fit-auditor` checks it.

**2. It exposes post-hoc rationalisation.**

The failure mode of chain-of-thought is that a model decides first and narrates
afterwards, producing reasoning that looks rigorous and explains a conclusion it
did not actually reach that way. The tell is a trace where no observation ever
changes the plan — where every action confirms what the first thought already
assumed.

So the protocol requires agents to record **revisions**: the moments where an
observation contradicted an expectation. A trace with zero revisions across a
dozen tool calls is not evidence of good reasoning; it is evidence that the
reasoning was not load-bearing. The judge scores this directly.

**3. It makes the swarm debuggable across context boundaries.**

Subagents do not share context. When Adam rejects a product, the dispatcher and
the auditor see only his output. The trace is how the reason survives the
handoff intact, rather than being compressed into "didn't fit".

## The loop

Each step is one JSON object:

```json
{
  "step": 3,
  "thought": "Noura's slot allows 95cm depth. The Interwood Astor is listed at 104.5cm, which should fail — but the listing also carries a bare field saying 101.6cm. Either way it is over, so I expect a fail and I should not spend a check on it.",
  "action": "cli.py fit unit01 living_dining 170 104.5 82.5 --x 5 --y 205",
  "observation": "{\"status\": \"fail\", \"reasons\": [\"... falls outside the room bounds ...\"]}",
  "reflection": "Failed as expected, but for a different reason than I predicted — bounds, not the slot budget. My mental model of the slot origin was wrong: y=205 is the sofa's front edge, not its back.",
  "revised": true
}
```

Field discipline:

- **`thought`** — what you expect and *why*, written **before** the action. State the prediction. A thought that only describes what you are about to do carries no information.
- **`action`** — the exact tool call or command. Reproducible verbatim.
- **`observation`** — the tool's actual output, quoted. Never your summary of it.
- **`reflection`** — did the observation match the prediction? If not, what was wrong in your model?
- **`revised`** — `true` when this observation changed your plan.

## Rules

1. **Predict before you act.** A thought written after seeing the result is not a thought, it is a caption.
2. **Never fabricate an observation.** If a call failed, the observation is the error.
3. **Record contradictions, do not smooth them.** Being wrong and correcting is the useful signal; the trace exists to capture it.
4. **Stop when the answer is determined**, not when the trace looks thorough. Padding a trace with confirmatory calls is its own dishonesty.
5. **One loop per decision**, not per keystroke. Choosing a sofa is one loop even if it takes four tool calls; the steps within it are the calls.

## What the judge looks for

`eazli-judge` scores the trace on **reasoning integrity**:

| Signal | Reading |
|---|---|
| Observations quoted verbatim, numbers in the answer all traceable to them | grounded |
| At least one `revised: true` where an observation genuinely changed course | load-bearing reasoning |
| A number in the final answer that appears in no observation | fabrication — critical failure |
| Twelve steps, zero revisions, every action confirming step 1 | post-hoc rationalisation |
| Thoughts that describe the action rather than predict its outcome | narration, not reasoning |

The last two score badly even when the final answer is correct. A right answer
reached by a process that cannot be checked is not reliable, it is lucky — and
the whole argument of this project is that the difference matters.
