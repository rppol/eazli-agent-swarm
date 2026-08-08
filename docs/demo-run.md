# Swarm run — flat 01 living/dining, 8,000 SAR

Recorded 2026-08-07. Real amazon.sa catalogue, real surveyed floor plan, real tool calls.

**Request:** *"I'm moving into flat 01. The living/dining room feels cramped and empty at the same time — I want somewhere comfortable to sit with a coffee table and a bit of light. Budget is 8000 SAR. I like warm, minimal things. Two of us live here, we sometimes have two friends over. The TV wall is already fixed and I'm not moving it."*

Plan: Explorer, 0 layouts / 0 re-designs used.

---

## 1. Zeina — intake

Emitted no products and no layout, consistent with her published scope ("she doesn't give you a catalog or a quote").

**Brief:** unit01 / living_dining. Budget 8,000 SAR. Style `warm`, `minimal`. Must keep: TV wall position. Must avoid: anything that adds to the cramped feeling; cold or ornate pieces.

**Blocking questions she refused to guess past:**
1. Does 8,000 SAR cover everything, or seating only?
2. Is anything else in the room already, or is it empty to plan from scratch?

**Facts established, each with a retrieved source:**
- Explorer covers 1 room, 5 layout options, 10 re-designs/month — this request is within limits. `eazli.com/pricing`
- A second room this month would require Active. `eazli.com/pricing`
- Noura's scope is layouts and visualisations; Adam's explicitly "does not design rooms or create visuals". `eazli.com/terms-ai-agent`
- Layouts are illustrative only; doorway, hallway, stair and lift measurements must be verified. `eazli.com/terms-ai-agent`

**Routed to:** `noura-designer` — *"The complaint is about how the room feels… with a fixed TV wall to plan around and no named product, so the space needs planning before anything is sourced."*

**Customer answers:** budget covers everything; room otherwise empty.

---

## 2. Noura — layout

Room from `get_room`: **335 × 551 cm** (11' × 18'1" on the plan).

**Concept:** two zones along the room's length — seating anchored on the TV wall at the north end, dining at the south — with everything set west of centre so one uninterrupted 90 cm+ walkway runs the full 5.5 m down the east side.

She validated clearances by placing **probe rectangles** and running `validate_layout` on them, rather than computing gaps herself.

| slot | role | x | y | max w × d × h (cm) |
|---|---|---|---|---|
| media_console | tv_console | 25 | 0 | 150 × 35 × 55 |
| primary_seating | sofa | 5 | 205 | 190 × 95 × 85 |
| coffee_table | coffee_table | 45 | 112 | 110 × 50 × 45 |
| accent_lighting | floor_lamp | 200 | 215 | 40 × 40 × 170 |
| dining_table | dining_table | 98 | 386 | 140 × 80 × 76 |
| dining_seating_north | chairs ×2 | 108 | 334 | 45 × 50 × 95 |
| dining_seating_south | chairs ×2 | 108 | 468 | 45 × 50 × 95 |

`validation: {"status": "pass", "reasons": []}`

*Recorded verbatim from the run. Re-running these seven slots against the current engine returns `fail` — the door-swing rule, the furniture-blocks-walkway rule and the coffee-table reach rule did not exist yet.*

**Assumption she surfaced rather than buried:** the plan is drawn with the TV on the short north wall, which may require sliding the TV ~0.5 m along its existing wall. She asked to re-plan rather than have the user work around a wrong assumption.

---

## 3. Adam — sourcing

> **Scoping note, added after the judge caught its absence.** The dispatcher restricted
> Adam to four slots — `primary_seating`, `coffee_table`, `accent_lighting`,
> `dining_table` — and told him to skip `media_console` (the customer may already own one)
> and the two dining-chair pairs. The original version of this document omitted that
> instruction, which made a deliberately scoped run read as a swarm that had silently
> dropped three slots. That omission was mine, not Adam's, and leaving it unmarked would
> be exactly the confidence laundering this system is built to prevent.
>
> The judge was still right about the underlying problem: **the three excluded slots are
> also unfillable from this catalogue.** It verified independently that no `tv_unit` is
> shallower than 40 cm against a 35 cm slot, and that there is no standalone dining
> chair in the catalogue — only two table-plus-chairs sets, neither of which publishes
> chair dimensions. So the headline at the time was **3 of 7 slots fillable**, with
> 6,320 SAR unspent.
>
> **A later review corrected even that.** See *the coffee table that did fit*, below:
> the real figure is **4 of 7**, with 5,521 SAR unspent.

Ran `check_fit` **and** `check_access_path` on every candidate.

### Picks — 1,679.98 SAR of 8,000

| slot | product | SAR | dims (cm) | conf | fit | access |
|---|---|---|---|---|---|---|
| primary_seating | Three Seater Sofa `B0FR3WVLTS` | 990.00 | 190×85×85 | stated | pass | pass |
| accent_lighting | OUTON Dimmable Floor Lamp `B0DRTV3XDT` | 149.99 | 30×30×155 | stated | pass | pass |
| dining_table | Marble-top Rectangular 120cm `B0BK8QZ8RR` | 539.99 | 120×70×76 | stated | pass | pass |

### Unfilled

**coffee_table** — no candidate fits *Noura's slot*. It allows 50 cm depth; the four available tables are 80, 80, 84 and 66 cm deep. **This is true of the slot and false of the room — see below.**

> `validate_layout`: *"Only 13cm between sofa_B0FR3WVLTS and coffee_table_B0H8PQ9KDJ. Leave 40-45cm between the sofa front and the coffee table…"*

He verified a hypothetical 110×50×45 table validates clean at Noura's coordinates, establishing the slot is internally sound. He concluded the gap was in the catalogue. **He did not move the slot** — correctly, since it was not his to move.

#### The coffee table that did fit

A brute-force search over every position in the room, run during a later review, found **all four catalogue coffee tables fit in front of the sofa**. The cheapest, `B0H8PQ9KDJ` at 799 SAR, validates clean at x=120, y=75 — 50 cm clear of the sofa front and within reach.

So "the catalogue cannot fill this slot" was true, and "the catalogue cannot furnish this room" was not. Nobody was wrong: Noura cut a slot tighter than the room needed, and Adam correctly refused to widen someone else's slot. **The failure was that no edge in the agent graph sent it back to her.** The `/eazli` command now has that return edge, and this is the clearest thing the whole exercise produced — correct individual behaviour composing into a bad system outcome, visible only by measuring the end result rather than each step.

### Rejections — 22 items, each with a measurement

Selected:

- `B0DYF1DPPS` Interwood Traditional 3-Seater — *"passenger lift A (lift car 218x208x220cm): item 223x83x78cm does not fit the car in any orientation."* **An access failure independent of room fit — the exact case eazli's Fitment clause disclaims.**
- `B0DM1DT6ZK` Tribesigns dining table, the best-reviewed *dining table* in the catalogue (4.5 / 475) — *"Only 85cm of walkway in front…; 90cm needed."* ~~Lost to 5 cm.~~ **This rejection was wrong.** The 90 cm figure is the seating walkway rule, misapplied to a table by a `/fit/check` that had no `role` field. It passes at the 75 cm a table actually needs. See the correction under *Findings* below.
- `B0DT1FYTKH` Interwood Kent bouclé, 5.0-rated and the closest thing to "warm, minimal" — **86 cm tall against an 85 cm slot. Over by 1.0 cm.** Passes fit, passes access, passes full-layout validation. Escalated as a decision rather than buried as a rejection.
- `B0DT1DXRKS` Interwood Astor — over depth, and *"flat entrance (door 90x210cm): clears at 82x170cm — must be carried on its side."*

### Findings Adam raised without acting on them

1. ~~**Noura's `dining_table` max depth of 80 cm is unachievable at y=386.**~~ **This finding was wrong, and the reason it was wrong is more interesting than the finding.** An 80 cm table does leave only 85 cm behind it, and `/fit/check` did reject it against a 90 cm rule. But that rule is the *seating* walkway rule, and `/fit/check` was applying it to everything because `FitRequest` had no `role` field. A dining table needs chair pull-out room (75 cm), not a circulation walkway. **Noura's 80 cm budget was correct all along**, and this false finding cost three candidates including the catalogue's best-reviewed dining table (4.5★, 475 reviews). Traced and corrected in a later run — see *Round 3* in [`../SHOWCASE.md`](../SHOWCASE.md). Clearance is now resolved by role, identically in both endpoints.
2. **No product in these categories publishes carton dimensions**, so every access check ran on assembled size. Most are `flat_pack: true`, making those verdicts pessimistic.
3. **Three floor lamps carry transposed axes** the parser does not flag — a "standing floor lamp" listed 13 cm tall, another 40 cm. They failed on depth anyway.
4. **`validate_layout` silently skipped its own rules** when passed bare ASINs as item ids, returning a false `pass` on his first run. *Fixed — see below.*

No purchase was made or initiated.

---

## The bug the swarm found in its own tooling

Adam's fourth finding was a real defect in `app/geometry.py`. `validate_layout` selected which rules to apply by substring-matching `item_id` for `"sofa"` / `"table"` / `"chair"`. Real callers pass product SKUs, and `B0FR3WVLTS` matches nothing — so the seating walkway rule and the sofa-to-table clearance rule never ran, and a layout with a 13 cm sofa-table gap came back `pass`.

A validator that silently skips its own checks is precisely the "confidence laundering" the auditor exists to catch. Finding it by *using* the system is the argument for building the auditor at all.

**Fix:** `Placement` now carries an explicit `role`. Roles select the rules. An undeterminable role makes the whole layout `unverified` rather than `pass` — a rule that did not run has not been passed. Guarded by regression tests at both the geometry and API layers.

---

## 4. eazli-judge — LLM-as-judge scoring

Graded against the six-dimension rubric in `.claude/agents/eazli-judge.md`. The judge
independently re-verified claims against the live tools before scoring, and was
instructed to mark hard.

**Overall 3.2 / 5 — verdict: `revise`.** No critical failures.

| dimension | score | why |
|---|---|---|
| Scope discipline | **5** | Every agent stayed inside its published boundary. Adam found Noura's slot budget apparently impossible and wrote it up rather than silently re-cutting it — *[the finding was later shown to be wrong; what earns the 5 is the behaviour, escalating rather than re-cutting]* — *"the single most tempting breach available to him and he declined it."* |
| Groundedness | **4** | Every number the judge re-checked matched exactly. Docked because the transcript quotes only two observations, so most figures are unauditable from the document alone: *"I had to leave the transcript to confirm them."* |
| Calibrated honesty | **3** | Section 2 reports `validation: pass`; Section 3 then appears to prove a slot inside that layout cannot satisfy the 90 cm rule at its stated maximum, and the contradiction is never reconciled. *[It was resolved afterwards: the 90 cm rule was the wrong rule for a table.]* The assumed 290 cm ceiling is never surfaced. |
| Failure-mode handling | **3** | *"Bimodal."* The lift rejection and the validator bug were handled at a 5. But a 79% budget underspend passes without a sentence. |
| Usefulness | **2** | Three of seven slots unaccounted for; the one item the user named — a coffee table — is the one not delivered, with no alternative offered; the escalated Kent sofa is put to the user as a decision **without its price**, so the decision cannot be made. |
| Reasoning integrity | **2** | *"There is no trace."* The ReAct protocol was added after this run, so the transcript is retrospective narration. Not one step marked `revised`, despite two genuine course changes. Scored 2 rather than 1 only because no observation was fabricated. |

**Highest-value fix, in the judge's words:**

> "Every slot Noura opens must appear in Adam's output with either a product or a
> measured reason, so that 6,320 SAR of unspent budget becomes a visible decision rather
> than an accident."

### Why these scores are the point

A judge that returned 4.5 and "ship" on this run would be worthless. This one:

- **verified before scoring** — it re-ran `cli.py kb` and `cli.py products`, confirmed all four policy citations verbatim with their chunk ids, and found a floor lamp the run cited that its own first search had missed;
- **scored dimensions independently** — a 5 for scope discipline sitting next to a 2 for usefulness, with no halo bleeding between them;
- **refused to reward the parts that read well** — *"this run was right, and the document cannot show that it was right."*

That last sentence is the entire argument of the reasoning protocol, arrived at
independently by the grader.

### Acted on

- Scoping omission corrected above.
- `reasoning_integrity` is a genuine gap: the ReAct protocol post-dates this run.
  `docs/reasoning-protocol.md` and the `trace` field now exist; the next run emits one.
- The unreconciled `pass` in Section 2 is now reconciled, and **not in the direction the
  judge assumed**. Noura's 80 cm `dining_table` budget was correct all along; the 90 cm
  figure came from `/fit/check` applying the seating walkway rule to a table because the
  endpoint had no `role` field. Re-running it today returns `{"status": "pass",
  "reasons": []}`. The layout table above shows the number she produced, which needed no
  correction.
