# Decision record 0002 — open delivery datasets (#86)

**Status:** decided 2026-10-31 — synthetic backfill stays; external dataset
**rejected on licensing** (documented per the issue's "either way").

## Question

Should a public delivery/mobility dataset become a second training source
or a validation-only benchmark for the demand/ETA models, given the demo's
data is synthetic?

## Candidates evaluated

1. **Kaggle "Food Delivery Dataset"** (gauravmalik26 — the Brazilian
   operational release, ~180k deliveries with courier, restaurant,
   weather and delivery-time features). Best schema match to our problem.
   **Verdict: rejected.** The Kaggle license field is "Unknown": no
   redistribution rights, no clear derivative-works permission. A repo
   that ships versioned migrations and license-aware CI cannot commit an
   unknown-license dataset or any artifacts derived from it — and
   downloading it ad hoc would make every future re-run irreproducible.
2. **NYC TLC Trip Records** — genuinely public with redistribution
   permitted. **Verdict: not adopted.** Mobility ≠ food delivery: no
   restaurant dwell time, no courier assignment, no order hourly demand
   signal — the join surface with our features is near zero, and the
   pipeline complexity of a second ingestion path buys a proxy benchmark
   of doubtful validity.

## Decision

- **Training stays on the synthetic backfill** — whose structure
  (multi-week drift, ~6-week wave, weekend/holiday spikes, weather dips,
  outage hours, per-zone heterogeneity, overdispersed counts) is
  seed-deterministic, documented in `scripts/backfill_order_history.py`,
  and printed by every run. Every report labels its data source
  explicitly, so the synthetic nature is never hidden.
- **Revisit trigger:** a properly licensed operational delivery dataset
  (partner data agreement, or a dataset published under CC-BY/CC0 by its
  operator). At that point it enters as a *validation-only* benchmark
  first — never as training data the demo depends on.

## Why this is the honest answer

The models' claim is not "state-of-the-art on real traffic" — it is "the
serving pipeline, feature contract, benchmark discipline and promotion
gate are production-shaped." An unlicensed dataset would trade a real,
documented limitation (synthetic volume) for a hidden one (legal and
reproducibility), which is the worse trade in every review that matters.
