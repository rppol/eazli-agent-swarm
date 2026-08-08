# eazli: a product teardown

Written from the public site in August 2026, before launch. Everything here comes from
eazli's own pages — the FAQ, the pricing page, the vendor page, and the AI Agent
Disclaimers & Scope policy. Where I am guessing, I say so.

This is not a critique. eazli's agent model is more carefully specified than most
shipped AI products, and the specification is what made a working prototype possible
in the first place. What follows is where I think the hard engineering is, and what I
built to test that belief.

---

## 1. What eazli is

An AI-powered shopping *and* design platform for the home, aimed at the GCC. Prices in
SAR, positioned as "the region's AI-powered home platform", operated by Eazli UAB
(Lithuania) under Aetheria Holdings. The root domain is still a holding page — build
`v0.36.6` — so this is a company shipping into a market it hasn't opened yet.

The FAQ is unambiguous about the business model:

> "Eazli operates primarily as a marketplace/intermediary. Products and services are
> provided by independent sellers/service providers, and Eazli facilitates discovery,
> ordering, payment (where applicable), coordination, and support."

Two revenue surfaces: consumer subscriptions (Explorer and Active, with a 2-week trial)
and a vendor marketplace that is free to join.

That combination is the interesting part. A marketplace that also *designs* has to make
promises about how third-party goods behave in a specific customer's home — and it does
not control the data those goods arrive with. Section 3 is about what that costs.

---

## 2. The agent model, and why it's unusually good

Three agents, each with a published scope and — rarer — published **negative** scope.

| | Zeina | Noura | Adam |
|---|---|---|---|
| Public title | AI Guide | AI Interior Designer | AI Sales Advisor |
| Policy role | "Orientation Layer" | "Visual Designer Agent" | "Sales & Decision Companion" |
| Does | welcomes, explains, **routes to specialized agents** | layouts, visualisations, mood boards | discover, compare, decide |
| Explicitly does *not* | "doesn't give you a catalog or a quote" | — | **"does not design rooms or create visuals"** |

Most agent products describe capabilities. eazli describes *limits*, per agent, in a
policy document. That is the difference between marketing copy and a specification, and
it is why I could implement the swarm without inventing an architecture: the
orchestrator-with-specialists shape is theirs, not mine.

The FAQ even anticipates the integration question — *"How do the agents work together?"*
— and answers it with a claim that is much stronger than it looks:

> "They collaborate behind the scenes so your design choices align with **what can
> actually be purchased, delivered, and installed**."

Delivered and installed. Not "available". That sentence is a promise about physics, and
keeping it requires something in the system whose job is to say no.

---

## 3. The hard problems

### 3.1 The gap between the legal page and the sales page

The AI Agent policy contains this, under Noura's Fitment clause:

> "You are responsible for verifying all site and access measurements (including
> **doorways, hallways, stairs, and elevators**). Eazli shall not be liable if an item
> (e.g., **a sofa**) cannot be delivered, moved in, installed, or used as intended due to
> inaccurate measurements or unverified assumptions."

Meanwhile the vendor page sells:

> "**27%–43%** Reduction in returns driven by better **fit**, visualization and expectations."

The same capability is disclaimed to the customer and monetised to the vendor. That is
not hypocrisy — it is a normal position for a pre-launch company that knows a problem is
hard. But it does mean the returns number is currently a promise without a mechanism, and
the mechanism is the interesting build.

It is also a *different* problem from the one most tools solve. "Does the sofa fit the
room" is easy. "Can the sofa reach the room" is a route problem across doors, corridors,
right-angle turns and lift cars. An item can pass the first and fail the second, and only
the second one generates a return.

**Concretely, from a real floor plan:** a 218 × 95 × 84 cm three-seat sofa fits the
living room comfortably. It reaches that room only if carried on its side through the
90 cm entrance. It cannot reach a bedroom in the same flat at all — no orientation clears
a 75 cm internal door. A 300 cm sectional never leaves the lobby: it doesn't fit the
218 × 208 cm lift car, and needs 300 cm of swing at a corridor turn that allows 157 cm.

None of that is visible from a product listing.

### 3.2 Catalogue dimension quality is the binding constraint

Any fit guarantee is downstream of dimension data, and dimension data from third-party
sellers is bad. I scraped 75 real amazon.sa furniture listings — a reasonable proxy for
what vendor-supplied feeds will look like — and parsed them:

| | count |
|---|---|
| Dimensions stated with explicit axis labels | 57 |
| Parsed from an unlabelled field (axis order ambiguous) | 9 |
| **Self-contradictory across fields** | **3** |
| **No dimensions at all** | **6** |
| Physically impossible for their category | 2 |
| Wrong product category entirely | 10 |
| **Usable for a confirmed fit claim** | **57 / 75 (76%)** |

The two 57s are a coincidence, not the same set: eight `stated` items are unusable
(wrong category or impossible size) and eight `parsed` items are usable.

The failure modes are specific and worth naming, because they don't look like errors:

- **Unit drift.** `1.05D x 2.2W x 0.83H Meters` and `104.5D x 170W x 82.5H centimeters`
  and `50 Inches` and `495 mm`, sometimes within one listing.
- **Self-contradiction.** One sofa states `104.5 × 170 × 82.5` in the labelled field and
  `101.6 × 168.9 × 81.2` in the bare one. Three wardrobes disagree by over 40 cm.
- **Axis-order ambiguity.** The unlabelled `Item Dimensions` field is D×W×H on some
  listings and W×D×H on others. Position alone is not information.
- **Wrong axis labels.** A "standing floor lamp" listed as `154D x 8.2W x 40.2H`. The
  154 is obviously the height. The label says depth.
- **Category pollution.** Searching "coffee table" returns Philips **coffee machines**.

A system that assumes clean input produces confident nonsense on roughly a quarter of
this catalogue. The 27–43% returns reduction is achievable, but it is a **data quality
programme** wearing an AI costume — vendor onboarding validation, unit normalisation,
contradiction detection, and a way to say "unverified" out loud.

### 3.3 Nobody's job is to say no

The three published agents are all aligned with the customer proceeding. Zeina orients,
Noura designs, Adam sells. Each is individually well scoped, and none of them is
structurally motivated to reject the plan.

But eazli's policy says outputs "may be incomplete, inaccurate, or contain incorrect
assumptions ('hallucinations')", and both the policy's Fitment clause and the FAQ
name **measurements** first among the things a user must verify. If measurement error is the named failure mode, an agent whose only job is to
re-verify measurements adversarially is missing from the roster.

This is not theoretical. In my own build, the layout validator silently skipped two of
its rules when given product SKUs instead of semantic ids, and returned a clean `pass` on
a layout with a 13 cm sofa-to-table gap. **A sourcing agent found it by using the
system.** A pipeline of cooperative agents would have shipped it.

### 3.4 The unit economics of a re-design

Active advertises "15 layout revisions and **20 re-designs** each month". If a re-design
means a generated visualisation, that is 20 image generations plus orchestration per
subscriber per month, against a subscription price the page doesn't state.

I can't evaluate this without their numbers. But it is the line item I would want to see
modelled before launch, because it is the one that scales linearly with engagement while
revenue is flat — the classic AI-product margin trap. Anything that resolves a design
deterministically instead of generatively is margin.

### 3.5 Arabic and RTL

The site is English-first with a language switcher and SAR pricing, targeting a
majority-Arabic market. One amazon.sa listing in my sample had an Arabic title. Every
retrieval and parsing decision in this space eventually has to be bilingual, and
embedding models handle Arabic considerably worse than English. I have not built for
this. I think it is a significant piece of work and I'd want to know how they're
planning it.

---

## 4. What I built

A working swarm that implements eazli's own agent model and closes the gap in §3.1.

**The architecture decision that matters: the LLM proposes, Python verifies.**

No agent decides whether something fits. `app/geometry.py` is deterministic, unit-tested
Python behind a tool call; agents may only *ask* it. This directly targets the weakness
eazli names in its own disclaimer — "verify… especially for measurements" — by removing
measurement from the model's remit entirely.

```
Claude Code agents          MCP shim  →  FastAPI  →  ChromaDB
  zeina  (intake/route)     typed        geometry.py     eazli_kb
  noura  (layout)           tools        (deterministic) products
  adam   (sourcing)                                      design_principles
  fit-auditor (adversarial)
  eazli-judge (evaluation)
```

Two checks, deliberately separate, because they fail independently:

- `check_fit` — does it work *in* the room? **Assembled** dimensions.
- `check_access_path` — can it *get to* the room? **Carton** dimensions where published.

Using the wrong one is confidently wrong in opposite directions: a flat-pack wardrobe
rejected for a hallway it would sail through, a rigid sofa waved through a door it cannot
clear.

Three things I'd point at specifically:

**`dims_confidence` travels with every number.** `stated` / `parsed` / `conflicted` /
`missing`. An item with no dimensions returns `unverified`, never `pass`. Refusing to
answer is a feature — it's the behaviour that actually reduces returns.

**Rules that don't run don't pass.** After the validator bug, an undeterminable item role
makes the whole layout `unverified` rather than `pass`. Silence is not success.

**Evaluation is split by decidability.** 32 ground-truth scenarios run in CI with no
model involved — geometry, catalogue provenance, plan limits. An LLM-as-judge grades only
what is genuinely qualitative: groundedness, scope discipline, calibrated honesty,
usefulness, failure handling, and whether the ReAct trace was load-bearing or written
after the fact. Asking a judge to arbitrate arithmetic is how eval suites start lying to
you.

---

## 5. What I did not solve

- **Arabic / RTL.** Named in §3.5, not built.
- **Real vendor integration.** The catalogue is amazon.sa as a stand-in for vendor feeds.
- **Photoreal visualisation.** The Noura *agent* outputs validated slot geometry;
  `viz/render.py` draws it deterministically as a plan, an isometric view and an OBJ
  model. Neither produces the photoreal render eazli's Noura does.
- **Checkout.** Deliberately — their policy forbids agents completing purchases.
- **Stairs and multi-floor routes.** The engine models lifts, corridors, turns and doors.
- **The corner-turn model assumes the item stays horizontal.** Movers tilt. So a turn
  failure means "a human should look at this", not "impossible".

---

## 6. What I'd want to talk about

1. **Is access-path checking on the roadmap, or deliberately out of scope?** It's the
   difference between the returns number being a mechanism or a hope.
2. **How is vendor dimension data going to be validated at onboarding?** Everything in
   §3.2 becomes eazli's problem the moment a vendor uploads a feed, and it is much
   cheaper to reject bad data at the door than to reason around it forever.
3. **What does a re-design cost you?** §3.4.
4. **Who says no?** Whether an adversarial verification role exists in the real system,
   or whether the three customer-facing agents are expected to police themselves.

---

*All quotations are from eazli.com as captured on 6 August 2026. The floor plan used for
testing is a real apartment plate; the catalogue is 75 real amazon.sa listings. Nothing
in the prototype is mocked.*
