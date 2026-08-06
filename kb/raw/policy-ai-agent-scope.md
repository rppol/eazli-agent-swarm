# AI Agent Disclaimers & Scope

Source: https://www.eazli.com/terms-ai-agent
Last Updated (per page): 03 Feb 2026
Captured: 2026-08-06

This is the single most important document in the corpus. It is eazli's own definition
of what each agent may and may not do. Every agent in this prototype is constrained by it.

---

## Definitions

- **Agent**: An AI-powered assistant within Eazli providing information, suggestions, comparisons, guidance, or navigation support.
- **Outputs**: Any text, recommendations, lists, summaries, comparisons, calculations, images, renderings, or visualizations.
- **Third Party**: Any seller, vendor, service provider, broker, or external entity not operated by Eazli.
- **Booking/Contracting**: Any purchase, booking, or agreement with a Third Party, whether through or outside the platform.
- **High-Risk Uses**: Safety, medical, legal, engineering/structural, electrical/gas, or material financial decisions.

## General Terms Applicable to All Agents

**1. Informational Nature; No Warranties.** All Agent Outputs are informational and advisory and may be incomplete, inaccurate, or contain incorrect assumptions ("hallucinations"). Outputs do not constitute: a warranty of product/service quality or performance, a binding commitment by Eazli, or a substitute for official sources or licensed professionals.

**2. No Binding Decisions; No Autonomous Execution.** Agents do not have authority to make legally binding decisions on your behalf, sign contracts, commit you, or complete purchases/bookings/payments without your explicit confirmation and approved platform flows.

**3. User Verification Obligation.** You are responsible for verifying: prices, availability, specifications, colors/dimensions, warranties; returns and refund policies; provider licensing/qualifications; and any legal/technical requirements before commitment.

**4. High-Risk Use Warning.** Agents must not be relied upon for decisions relating to safety risks (fire, electrical, gas, structural), medical advice, legal advice, or major financing/credit/investment decisions, without consulting a licensed professional or official authority.

**5. Third-Party Responsibility.** Eazli operates as a technology marketplace/connector. Third-party sellers/providers remain responsible for their products/services and legal obligations.

**6. No Agency; No Partnership.**

---

## Agent-Specific Disclaimers

### (A) Zeina — AI Guide (Orientation Layer)

> **Scope**: Welcomes users, explains Eazli in plain language, answers trust and safety questions, and routes users to specialized agents.
>
> **Limits**: Not a substitute for legal/medical/safety/engineering/financial advice.
>
> **Disclaimer**: Zeina may provide outdated or incorrect information. Always verify via official Eazli pages, customer support, or qualified professionals.

### (B) Noura — AI Interior Designer (Visual Designer Agent)

> **Scope**: Creates room visualizations, layouts, and mood boards to help users "see" decisions before committing.
>
> **Visualizations, Dimensions & Color Disclaimer**: Any visualization/rendering/mockup is illustrative only and not an engineering drawing or precise measurement. Dimensions and colors may vary due to lighting, screen differences, materials, and manufacturing batches. Users must verify real-world measurements: product dimensions, doorways, hallways, stairs, elevators, and site conditions.
>
> **Fitment (non-exhaustive)**: You are responsible for verifying all site and access measurements (including doorways, hallways, stairs, and elevators). Eazli shall not be liable if an item (e.g., a sofa) cannot be delivered, moved in, installed, or used as intended due to inaccurate measurements or unverified assumptions.
>
> **Recommendations & Seller Policies**: Recommendations do not guarantee availability, final pricing, delivery times, or quality.

### (C) Adam — AI Sales Advisor (Sales & Decision Companion)

> **Scope**: A subscription-only companion that helps users discover, compare, and decide. He narrows options based on taste and budget.
>
> **Disclaimer**: Adam is a decision partner, not a legal or financial authority. **He does not design rooms or create visuals.** While Adam may suggest products, users are responsible for verifying final specifications and prices with the Third-Party seller before purchase.

---

## Constraints this imposes on the prototype

These are enforced in the agent definitions and in the geometry engine, not merely documented.

| Source clause | Enforced as |
|---|---|
| Zeina "routes users to specialized agents" | `zeina-guide` emits a routing decision + brief; it never returns products or layouts |
| Adam "does not design rooms or create visuals" | `adam-advisor` has no layout tools; it may only fill slots Noura defined |
| Adam is "subscription-only" | `check_quota` gates Adam behind an active Explorer/Active plan |
| "No autonomous execution... without your explicit confirmation" | No agent may check out. The run ends at an approved plan, never an order |
| "Any visualization is illustrative only and not an engineering drawing" | Layout output is labelled illustrative; every numeric claim comes from `geometry.py`, not from a model |
| **"verify all site and access measurements (doorways, hallways, stairs, elevators)"** | **`check_access_path` — the delivery-access check. See below.** |
| "may be incomplete, inaccurate, or contain incorrect assumptions" | `fit-auditor` independently re-verifies every claim against deterministic tooling |

## The access-path gap

The Fitment clause is the most revealing sentence on the page. eazli disclaims liability for
whether *"an item (e.g., a sofa) cannot be delivered, moved in, installed, or used as intended"* —
and specifically names **doorways, hallways, stairs, and elevators**.

That is a different problem from "does the sofa fit in the room". A 218 cm sofa can fit a living
room perfectly and still be undeliverable because it cannot turn the corner of a 100 cm hallway
or enter a 75 cm door leaf.

It is also, by their own admission, currently pushed onto the user. Their vendor page separately
claims **27–43% reduction in returns "driven by better fit, visualization & expectations"** —
so the commercial incentive to close this gap already exists on their side.

`geometry.py` therefore implements two distinct checks:

1. `check_fit` — does the item work *in the room* (clearances, walkways, overlap)
2. `check_access_path` — can the item physically *get to* the room (door leaf width, hallway
   width, corner turning, stair/elevator envelope)

An item that passes (1) and fails (2) is the highest-value rejection the system can produce.
