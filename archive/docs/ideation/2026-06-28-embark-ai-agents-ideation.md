---
date: 2026-06-28
topic: embark-ai-agents-automation-second-brain
focus: AI agents (company + per-advisor), workflow automation, and a Karpathy-style LLM-wiki second brain for a luxury travel host-agency network
mode: elsewhere-software
---

# Ideation: AI Agents, Automation & a Karpathy-Style Second Brain for a Luxury Host-Agency Network

> Fresh ideation (existing folder docs deliberately ignored). Generated via 6 parallel web-research threads + 6 ideation frames across 5 topic axes + synthesis (54 raw candidates → 29 → 11 survivors after adversarial filtering).

## The strategic reframe (organizes everything below)

The research is blunt: in luxury travel, **"AI can draft; it cannot vouch."** Only ~8% of travelers trust AI to book, the Air Canada precedent made firms legally liable for chatbot hallucinations, and at $20K–$200K+ trips the cost of one wrong detail is unrecoverable. So the winning strategy is **not autonomous booking**. It is:

1. **Automate the judgment-light hours** (commission chasing, document collection, proposal scaffolding, lifecycle comms).
2. **Build a knowledge moat from the network's aggregate exhaust** — supplier reliability, amenity fulfillment, trip precedent — that no solo advisor or consumer AI can replicate.
3. **Wrap it in draft-only governance** so an agent never vouches for something it cannot.

The **legacy GDS + ClientBase/TRAMS stack reinforces this**: clean agent-ready APIs mostly don't exist, so the moat *must* be a knowledge-and-document layer rather than an integration play — which is the more durable asset anyway.

Two structural resolutions recur through the whole set:
- **"Company vs per-advisor agents" → a two-layer data model**, not two agent fleets: a shared, read-only **network brain** (suppliers, amenities, SOPs, skills) + a hard-isolated **private per-advisor brain** (clients, style, book). Most ideas are `level: both` because they read shared and write private.
- **The compounding bet is the whole game.** Individual features are copyable; the *combination* of precedent corpus + amenity rules + supplier-reliability data accumulated across ~1,099 advisors is not. The strategy's job is to make daily advisor work *deposit* into that shared asset for free.

## Topic Context (grounding summary)

**Business.** A luxury travel agency operating as a **host network of independent-contractor ("1099") luxury advisors** (Fora / Gifted Travel Network / Brownell-style, Virtuoso-adjacent). The host provides supplier relationships, commission processing, back office, training, brand, and tools.

**Engagement goal.** AI / personal-agents strategy at two levels — company/network-level (host-operated) and per-advisor (each independent advisor) — plus workflow automation and a "second brain."

**"Second brain, Karpathy LLM-wiki style"** (user-clarified = **Andrej Karpathy**, not "Carpathia"). His April 2026 LLM-Wiki gist defines a three-layer, **LLM-maintained** knowledge base built *for the model to consume*: `raw/` (immutable sources) → `wiki/` (LLM-generated markdown with frontmatter `title/type/sources/related/confidence` + Obsidian wikilinks) → a `CLAUDE.md`/`AGENTS.md` schema defining **Ingest / Query / Lint** workflows. His own wiki reached ~100 articles / ~400K words with zero hand-authoring. He **bypasses vector DBs/RAG under ~100K tokens** ("compiled once, kept current" markdown navigated via `index.md`). Related: `llms.txt` (Answer.AI, 2024) is the external-content analog; "context engineering" (context window = RAM, knowledge store = disk, in the LLM-OS framing) is the operative production frame.

**Current stack (the binding constraint).** GDS (Sabre / Travelport / Amadeus) + ClientBase/TRAMS back office + ARC/IATA commission tracking. Legacy-heavy: APIs gated/complex, NDC migration in play, much data trapped in email, PDFs, and Windows desktop software. Favor **email/document automation, RPA, and a knowledge layer** over brittle GDS integration.

**Key external findings.**
- *Advisor time sinks (quantified):* itinerary/proposal building is the biggest — 60+ advisor hours for a complex trip solo, compressing to 1–3 days only with a trusted DMC. Commission reconciliation is the most underestimated — Sion: **40%+ of commissions carry discrepancies, 20%+ go entirely unpaid**; a 15-booking month spawns 30–40 events across 12+ suppliers over 6 months. Amenity-stacking (Virtuoso / Four Seasons Preferred Partner / Rosewood Elite / Amex FHR) is manual, property-by-property.
- *Host economics:* commission-split spread (Fora 70/30 → 80/20 at $300K → 90/10 at $2M; Brownell 70/90% tiers) + consortium override lift (Virtuoso ~$35B volume) + fees. **Data ownership is the single most-discussed advisor grievance** — portability guarantees "perversely increase retention by reducing resentment." Supplier intelligence from aggregate booking data is named as a structural advantage "no individual advisor can replicate." (Fora's 2025 Legends acquisition → "Sidekick" AI on 35,000 hotel partners proves the data-flywheel direction.)
- *AI in travel 2025–2026:* consumer agentic booking exists for commodity air/chain hotels (Mindtrip on Sabre Mosaic) but **no reliable API surface for villas, private aviation, expedition cruises, or preferred-partner content**; advisor tools (Tern AI — ~6 weeks of hours saved; Travefy) accelerate rather than replace. ~8% trust AI to book; "AI can draft, it cannot vouch."
- *Knowledge/RAG & agent architecture:* two-layer multi-tenant pattern (shared org index + hard-isolated per-advisor namespace); staleness is the #1 reason KBs die at 3–6 months → approve-don't-write capture + event-triggered re-index; Interloom ($16.5M, 2026) on "~70% of operational decisions never documented" (cut the gap 50%→5% at Commerzbank); three-tier memory (semantic/episodic/procedural). Governance: Salesforce 7K-session tenant isolation, onereach 3-tier HITL, 36%-prompt-injection audit of community skills, Anthropic Agent Skills standard (write-once-run-anywhere), Microsoft UFO² (accessibility-tree-first, vision-fallback) for legacy Windows GUI automation, EU AI Act Art. 14 (Aug 2026).

## Topic Axes

- **A1** Advisor productivity & workflows (itineraries, proposals, client comms, supplier sourcing, the per-advisor agent)
- **A2** Network / company operations & back office (commission reconciliation, supplier & DMC relationships, advisor onboarding/training, compliance, recruiting/retention)
- **A3** Knowledge layer / second brain (capturing, structuring & serving institutional + advisor knowledge to agents and humans, Karpathy-style)
- **A4** Client experience & relationships (lead intake, personalization, concierge during trip, post-trip, retention & referrals)
- **A5** Agent infrastructure & governance (how agents are built/permissioned/shared across 1099 advisors; data ownership; trust, brand safety & accuracy in luxury)

## Ranked Ideas

Organized as a **4-layer architecture** (bottom-up). 11 survivors kept (above the default 5–7) because they are the layers of one coherent system spanning the three pillars, not competing alternatives. Each carries a verifiable basis and passes the meeting-test.

### Layer 0 — Rails (feasibility + trust foundation)

#### 1. Legacy RPA data spine
**Description:** Since ClientBase/TRAMS and the GDS lack clean agent APIs, instrument the *desktop* with hybrid RPA (Windows accessibility-tree first, computer-vision fallback) to read/write the systems of record. Build once; feed reconciliation, precedent capture, and rebooking signals downstream. The advisor's existing keystrokes become the integration.
**Axis:** A5 / A2 · **Level:** company
**Basis (external):** Microsoft UFO² (HostAgent + per-app AppAgents); pure-screenshot agents score ~15% on OSWorld vs. far higher for hybrid; ai-travel — "screen-scraping RPA remains the only route today" for boutique/luxury supply.
**Rationale:** The engagement's binding constraint is the legacy stack; RPA over the desktop UI is the only route that works today and unlocks every downstream automation that must touch the systems of record.
**Downsides:** Brittle, maintenance-heavy; write-gating risk (industry paid $530M in debit memos in one year). Requires an honest feasibility spike before promising "GDS integration."
**Confidence:** 70% · **Complexity:** High · **Status:** Unexplored

#### 2. Trust & governance architecture (draft-only + isolation + tiered HITL)
**Description:** Before any per-advisor agent ships, stand up the rails: hard namespace isolation between advisors' private client data and shared org knowledge (enforced at agent-identity level, not just DB query filters); scoped-token tool permissions; agents structurally incapable of external action — every client-facing message, booking, or supplier commitment is **draft-only** behind a tiered HITL gate (pre-approval for any money / external comms / client-facing claim); full audit logging; two-tier skill review.
**Axis:** A5 · **Level:** company
**Basis (external):** Salesforce tenant-scoped storage keys + TTL isolation (7K+ sessions); onereach three-tier HITL; 36%-prompt-injection audit of community skills; Air Canada liability; EU AI Act Art. 14 (enforceable Aug 2026). "AI can draft; it cannot vouch."
**Rationale:** In a 1099 network, one agent leaking another's client list or one auto-sent hallucination is brand-existential. This is the precondition that makes every other idea safe to ship.
**Downsides:** Slows velocity; isolation has real infra cost (per-advisor index vs. shared-index-with-filtering tradeoff); not a flashy deliverable.
**Confidence:** 90% · **Complexity:** Medium · **Status:** Unexplored

### Layer 1 — The second brain (the stated core)

#### 3. Self-maintaining Karpathy knowledge federation
**Description:** Build the institutional knowledge core as an LLM-maintained wiki, not a RAG vector store: `raw/` (supplier sheets, FAM notes, program agreements, past proposals) → `wiki/` (LLM-generated per-entity markdown with frontmatter + wikilinks) → a `CLAUDE.md` schema defining Ingest/Query/Lint. Structure as a **federation of sub-100K-token wikis** (per destination/supplier/advisor) navigated by `index.md`, reserving RAG only for genuine cross-everything queries. Two tenants (shared read-only + private per-advisor). **Self-maintains** via approve-don't-write capture (agent pre-fills a 60-second debrief; advisor corrects/approves; approval cascades across 10–15 pages). Maintained like an open-source repo (CONTRIBUTING + PR review + `log.md` audit).
**Axis:** A3 · **Level:** both
**Basis (external):** Directly implements Karpathy's LLM-Wiki gist (~100 articles / ~400K words, zero hand-authoring; anti-RAG under ~100K tokens). km-rag names staleness as the #1 reason KBs die at 3–6 months; "approval is the knowledge-capture event."
**Rationale:** The literal stated engagement goal and the hot-path knowledge backbone — without it every other agent degrades to ad-hoc retrieval over scattered files. Also the most legacy-safe deliverable (sidesteps the GDS entirely).
**Downsides:** Needs a near-zero-friction capture UX or it rots; the federation boundary (when to add a graph/RAG layer above ~100K tokens) and the Lint/freshness ownership must be decided.
**Confidence:** 80% · **Complexity:** Medium-High · **Status:** Unexplored

#### 4. Supplier Intelligence Graph — the compounding moat
**Description:** Host-operated Layer-2 wiki pages, one per property/supplier, holding both static facts (programs, stackable amenities, exclusivity rules, GM contacts — compiled `llms.txt`-style) and **dynamic intelligence** (commission-payment reliability, amenity-fulfillment track record, repeat-booking rates) mined from the aggregate booking/commission/trip-debrief exhaust of all advisors. Contributing a 60-second debrief is the price of reading the scorecard. Both company and per-advisor agents read these pages at proposal and supplier-selection time, so the graph gets monotonically more valuable as the network grows.
**Axis:** A3 · **Level:** company
**Basis (direct):** host-model — "a supplier intelligence layer… hosts sitting on aggregate booking data have a structural advantage no individual advisor can replicate." No AI has preferred-partner/amenity ground truth (relationship-based, outside any API). Zep/Graphiti temporal storage handles "what was true when" for rate/program changes.
**Rationale:** The single asset that gets harder to replicate every day; the central compounding bet every other knowledge idea feeds into or draws from. Arguably worth more than the commission split as a retention anchor.
**Downsides:** Cold-start (depends on S1/S9/S11 feeds); advisors must trust a shared scorecard that may indict their favorite suppliers; host must treat booking exhaust as a first-class product asset.
**Confidence:** 80% · **Complexity:** Medium-High · **Status:** Unexplored

#### 5. Portability as anti-churn weapon + contribution unlock
**Description:** Architect each advisor's private knowledge (client dossiers, trip notes, supplier preferences, style, the per-advisor wiki) as a cryptographically-owned, namespace-isolated, BYOK-encrypted, **one-click-exportable** tenant — flat files make "take your brain with you" a literal export. The shared network Layer-1 stays the host's, so leaving costs the live enrichment, not the data. Counterintuitively, removing exit anxiety raises retention — and that trust is the precondition for advisors to contribute tacit knowledge to the shared layer.
**Axis:** A5 / A3 · **Level:** both
**Basis (direct):** host-model — data portability is "the single most-discussed grievance across advisor forums"; advisors who can't take CRM data "become hostile references"; portability works by "perversely increasing retention by reducing resentment." km-rag namespace-per-advisor makes clean export straightforward.
**Rationale:** Converts the model's sharpest structural conflict into a recruiting and retention asset, and is the upstream governance choice that unlocks (or quietly kills) every compounding idea that depends on voluntary contribution.
**Downsides:** Host must trade CRM lock-in leverage (a strategic/cultural decision); AI-enriched-dossier IP ownership needs an ICA addendum *before* data accumulates.
**Confidence:** 80% · **Complexity:** Low-Medium (technical) / hard (business decision) · **Status:** Unexplored

### Layer 2 — The agents

#### 6. Per-advisor "Chief-of-Staff" OS (exception queue, not a tool)
**Description:** The headline per-advisor agent, framed as an **exception queue, not a toolbox** (avoids the "forgot to use it" failure mode): it owns the admin surface by default — commission logging, document chase, pre-departure sequences, inbox triage — layered as a private episodic/procedural memory (clients, preferences, past complaints like "room noise at Brand Y — avoid," open VIP request chains) on top of the shared supplier graph, routing only judgment calls up to the human. Includes pre-filled VIP pre-arrival outreach to GM/key contacts.
**Axis:** A1 / A5 · **Level:** per-advisor
**Basis (direct):** advisor-work automation matrix — the human's residual surface *is* the low-automation column (discovery, supplier selection, crisis); Tern AI let one advisor reclaim ~6 weeks of hours. km-rag multi-tenant + three-tier memory supplies the architecture.
**Rationale:** The clearest ROI of the engagement; the exception-queue framing is what makes advisors actually adopt it, and the private RM memory amplifies the one thing research says AI cannot do — remember and vouch for the relationship.
**Downsides:** Change-management heavy; positioning (delegate vs. tool) shapes the entire product; depends on S2 governance + S3 brain; per-advisor isolation must be airtight.
**Confidence:** 75% · **Complexity:** High · **Status:** Unexplored

#### 7. Signed Agent Skills marketplace (the franchise operations manual)
**Description:** The answer to *"how do you build 1,099 personal agents without building 1,099"*: standardize capabilities as portable Agent Skills authored centrally and executed locally by every advisor's agent ("build a safari proposal," "amenity-stack a Virtuoso booking," "run a pre-departure document chase," "chase a late commission"). Top advisors author skills in natural language; a curated marketplace turns top workflows into network assets and authors earn a cut. Two-tier review: company-signed skills trusted by default; advisor-contributed skills pass a separate gate.
**Axis:** A5 · **Level:** both
**Basis (external):** Anthropic Agent Skills open standard (Dec 2025, ~40 products, write-once-run-anywhere); WRITER's natural-language Agent Skills/Playbooks; franchise analogy (franchisor authors brand-standard manual, franchisees execute within rails); 36%-injection audit → signing is the host's defensible role.
**Rationale:** The highest-leverage distribution mechanism in the strategy — central authoring with network-wide instant rollout — and the cleanest place to enforce luxury brand-safety/HITL consistently across independent contractors. Turns advisors into a contribution flywheel.
**Downsides:** Who owns/monetizes advisor-authored workflows; the review/signing pipeline that keeps a poisoned skill out of a $100K booking has real cost.
**Confidence:** 78% · **Complexity:** Medium · **Status:** Unexplored

#### 8. Agent-native onboarding / Mentor Agent (ramp-compression flywheel)
**Description:** Put procedural knowledge in the agent so a day-one advisor operates near senior level — a per-advisor co-pilot holding supplier rules, booking sequences, and escalation patterns, drafting first proposals, surfacing the right playbook at the moment a real client question lands, with the supplier graph + precedent + signed skills pre-loaded. Decouples ramp from knowledge acquisition; recruit for relationships and sales instinct, let the brain supply expertise. Because new advisors also contribute, recruiting enriches the brain, which speeds the next cohort.
**Axis:** A2 / A3 · **Level:** both
**Basis (external):** GDS competency takes 6–12 months and atrophies without use; industry-norm ramp ~3 years (Fora claims ~20 months with better tooling, betting on a 15,000-advisor mass market at $299/yr; Brownell Catalyst graduated-split mentorship). km-rag procedural memory + agent-arch franchise/skills model make centrally-authored mentorship run per-advisor unchanged.
**Rationale:** Collapses the most expensive part of onboarding, widens the recruitable pool, and is simultaneously the strongest recruiting pitch, churn defense, and a self-reinforcing growth loop — without scaling trainer headcount.
**Downsides:** Real brand/liability risk in recruiting non-experts on the bet the agent carries the expertise; needs a mature brain first.
**Confidence:** 70% · **Complexity:** Medium-High · **Status:** Unexplored

### Layer 3 — High-ROI automations (the wedge + the biggest time-sinks)

#### 9. Commission reconciliation & recovery — the self-funding wedge ⭐ START HERE
**Description:** At booking time, a network agent records the expected commission event (supplier, amount, expected pay date, advisor split) into a ledger; it ingests supplier remittance emails/PDFs and ClientBase/TRAMS records, auto-matches arrivals with fuzzy AR-style matching, and surfaces only the missing/short/late events — drafting the chase for a human to send. Aggregated at supplier level for negotiating leverage no solo advisor has. **Recovered commission (a share of the unpaid 20%) funds the rest of the engagement; the matched ledger is the first data feed into the Supplier Intelligence Graph (S4).**
**Axis:** A2 / A3 · **Level:** company
**Basis (direct):** Sion — 40%+ of commissions carry discrepancies, **20%+ go entirely unpaid**; a 15-booking month spawns 30–40 events across 12+ suppliers over 6 months; at volume chasing becomes "almost a full-time job." host-model independently names automated reconciliation a top stickiness lever that "returns hours per week." Fintech AR automation (Ramp, Bill.com, Stripe) is the proven matching analog; Graphiti bi-temporal storage preserves "what was true at booking time" for disputes.
**Rationale:** The rare bet with immediate, hard-dollar, attributable ROI; it self-funds the build *and* produces a permanent compounding data byproduct (supplier-payment reliability). The ideal flywheel starter.
**Downsides:** Needs clean structured data out of ClientBase/TRAMS (export vs. S1 RPA); recovery-share fee model needs advisor buy-in; chasing touches supplier relationships.
**Confidence:** 85% · **Complexity:** Medium · **Status:** Unexplored

#### 10. DMC-in-a-box proposal agent
**Description:** Invert proposal creation from blank-page assembly to edit-down curation: a per-advisor agent retrieves the closest proven itinerary (from the advisor's own past trips and/or the network's shared corpus) or assembles from vetted composable blocks ("3-day Cape Town arrival," "July Serengeti migration," "Amalfi villa-and-driver"), auto-annotates every property with its optimal amenity stack + benefit language, reliability-ranks supplier choices from S4, and ships a 70%-complete branded draft. At trip close the finished itinerary auto-drafts into a sanitized, reusable precedent — a law-firm-style brief bank. Folds amenity-stacking, agentic RFQ fan-out + quote-parsing, and supplier-promo triage.
**Axis:** A1 / A3 · **Level:** both
**Basis (direct):** advisor-work — itinerary building is the highest total time sink (60+ hours for a complex two-week trip solo, compressing to 1–3 days only with a trusted DMC, "the single biggest efficiency lever"). A DMC's edge is accumulated precedent, which the network already holds across thousands of overlapping itineraries. Tern AI proves agentic proposal drafting works (~6 weeks saved). No consumer AI has preferred-partner access or supplier-fulfillment ground truth, so the integrated artifact is structurally unreplicable.
**Rationale:** Attacks the single biggest hour-sink with the host's three unique assets simultaneously (precedent + amenity rules + reliability graph); the integration itself is the moat — a competitor can copy any one feature but not the combined corpus.
**Downsides:** The three data layers must be fresh/trusted enough that advisors ship without re-checking; private-book-vs-shared-corpus data architecture, de-identification, and reciprocity model are unresolved.
**Confidence:** 75% · **Complexity:** High · **Status:** Unexplored

#### 11. Client lifecycle comms on rails = the capture loop
**Description:** Operate the deterministic touchpoints as drafted-and-queued sequences a human approves and sends: instant lead acknowledgment, document collection (passport, dietary, health), the pre-departure reminder cadence, and post-trip review + re-book outreach — in house-voice or advisor voice. Triggers are deterministic (departure/return dates), so there is no live-pricing hallucination risk. **The post-trip review email is the natural trigger for the approve-don't-write debrief capture, so the lowest-risk quick win doubles as the always-on ingestion pipeline that keeps S3/S4 fresh as a side effect of work advisors already do.**
**Axis:** A4 / A3 · **Level:** both
**Basis (direct):** advisor-work automation matrix rates inquiry acknowledgment, document collection, pre-departure reminders, and post-trip review/re-book all High automation-fit (deterministic, date-triggered); post-trip outreach is flagged "routine but often neglected" (lost referrals and repeat revenue). km-rag — "approval is the knowledge-capture event."
**Rationale:** The lowest-risk, fastest quick-win that can fund the harder build, and structurally solves the staleness problem: instead of asking busy advisors to maintain a wiki, the wiki maintains itself off the comms they send anyway.
**Downsides:** Where trip dates live for the trigger (CRM export / S1 RPA / manual); house-voice vs. advisor-voice; needs the minimum-friction approval UX embedded in the flow.
**Confidence:** 82% · **Complexity:** Low-Medium · **Status:** Unexplored

## Suggested Sequencing (roadmap)

**S9** (commission recovery — found-money wedge, self-funds, proves ROI) **+ S2** (governance rails) **+ S1** (legacy RPA data spine, in parallel) → **S11** (lifecycle comms quick win; turns on capture) → **S3 + S4** (knowledge federation + supplier moat, now being fed by S9's ledger and S11's comms) → **S5** (portability unlocks voluntary contribution) → **S6 + S7** (per-advisor Chief-of-Staff OS + signed-skills distribution) → **S8** (onboarding/ramp flywheel) → **Phase 2: client-facing layer** (concierge, portal, intake desk, predictive rebooking).

## Rejection Summary

| # | Idea (candidate ID) | Disposition / Reason |
|---|---|---|
| 1 | Amenity-stacking engine (C1) | **Folded → S10** (DMC-in-a-box) — it's the amenity-annotation step of the proposal agent |
| 2 | Commission reconciliation, atomic (C2) | **Folded → S9** — survivor is the self-funding-wedge framing |
| 3 | CONTRIBUTING.md / PR-review maintainership (C6) | **Folded → S3** — the maintenance-governance layer of the knowledge federation |
| 4 | Lifecycle comms, atomic (C7) | **Folded → S11** |
| 5 | Per-advisor RM memory + VIP outreach (C8) | **Folded → S6** — the private-memory layer of the Chief-of-Staff OS |
| 6 | Proposal-from-precedent / DMC-in-a-box atomic (C13) | **Folded → S10** |
| 7 | Agentic RFQ fan-out & quote-parsing (C14) | **Folded → S10** — supplier-sourcing step of the proposal agent |
| 8 | Supplier-promo inbox triage (C15) | **Folded → S10** (advisor-productivity suite) |
| 9 | Hybrid RPA bridge, atomic (C16) | **Folded → S1** |
| 10 | Portable advisor-owned brain, atomic (C17) | **Folded → S5** |
| 11 | Chief-of-Staff agent, atomic (C21) | **Folded → S6** |
| 12 | Branded grounded concierge (C9) | **Deferred → phase-2 client layer** — real, but downstream of the stated three pillars; escalation-line + brand (advisor vs host) questions |
| 13 | Branded client portal (C10) | **Deferred → phase-2** — real, lower priority; advisor-vs-host branding + data-ownership tension |
| 14 | Network concierge intake desk (C11) | **Deferred → phase-2** — brand-political (who owns inbound leads, routing fairness) |
| 15 | "Likely to Travel" predictive rebooking (C12) | **Deferred → phase-2** — depends on the data spine; real upside once S1/S4 exist |
| 16 | Advisor-anchored client-experience bundle (C29) | **Deferred → phase-2** — the integrated client layer (intake + RM + concierge under governance) |
| — | A4 (client experience) axis | **Deliberate gap** in the core set — it is the explicit phase-2 layer; S11 is the one A4 bet load-bearing now because it doubles as the capture pipeline |
| — | Hard rejections | **None** — the divergent set was tightly grounded; filtering was consolidation + sequencing, not quality culling |

---
*Generated by `/ce-ideate` (elsewhere-software mode). Grounding: 6 web-research threads (Karpathy LLM-wiki, advisor workflows, host-agency model, AI-in-travel 2026, KM/RAG, agent architecture). Next rung: `/ce-brainstorm` on a chosen survivor to define exactly what it means, then `/ce-plan`.*
