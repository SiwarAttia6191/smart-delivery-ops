# 0005 — The GDPR posture for real clients

Date: 2026-11-07 · Status: accepted — the posture is fixed BEFORE the
first real client; the engineering minimum is filed in milestone 14 ·
Trigger: the onboarding plan (BC2/BC3, milestone "Client Onboarding v1")
loads a client's real customer book — names, phone numbers, addresses —
onto the VPS the moment a pilot signs. BC4/BC5 settled who we sell to and
at what price; nothing settled what we owe those people. The platform
processes personal data of EU data subjects (Madrid is the first market),
so GDPR applies at the first real tenant — not at some later scale.

## Context

Today the platform holds only demo fixtures (BC1: invented businesses on
real coordinates) and the operator's own knowledge corpus, so the GDPR
surface is currently zero. Onboarding changes that: bulk import (#223) is
DELIBERATELY designed to carry a client's customers across. The facts the
decision starts from:

- **Roles.** The client (restaurant / delivery operator) is the data
  CONTROLLER; this platform is the data PROCESSOR acting on their
  instruction. The RLS wall already implements the strongest technical
  expression of that split: one tenant can never read another's data.
- **Processors already in the chain.** Hetzner (EU hosting — the 0004
  VPS), OpenAI (the one cross-border processor: copilot chat, embeddings
  and the knowledge corpus touch it; DPF-certified), GitHub Actions (CI
  only — demo fixtures, never tenant data).
- **Already shipped and GDPR-relevant.** PII redaction in logs (#192 —
  phone numbers never reach a log line), hashed passwords, RLS on every
  tenant table, the local hash-embedder default (vectors stay in our
  Postgres unless embeddings are explicitly reconfigured).
- **What does not exist yet.** Consent capture in the import flow, a
  right-to-erasure path, a privacy notice, a DPA template.

## Decision

**1. Collect the minimum, store it scoped, log it redacted.** Name,
phone, address — what delivery needs, nothing more. Tenant-scoped by RLS
(built), redacted in logs (built, #192). No analytics on individual
customers — the marts aggregate, and stay that way.

**2. Lawful basis, per use.** Order execution and dispatch: contract
performance (art. 6(1)(b)) — nobody consents to having their food
delivered. Marketing, loyalty and review contact (#216/#217): the
customer's consent — the bulk-import contract gains a `consent_marketing`
column (per row), and the storefront checkout gains the checkbox when the
loyalty work lands.

**3. The controller/processor split is explicit and offered as a DPA.**
A one-page data-processing-agreement template is offered at signing: we
process on the client's instruction, EU hosting, named sub-processors
(Hetzner, OpenAI), deletion on termination.

**4. Rights paths are procedures before they are features.** Before the
first real client — though not blocking the onboarding build — a tested
erasure procedure (null the PII across a customer's orders and events,
keeping the audit shape) and an export procedure (a per-tenant dump so
portability is a command, not a project). Filed as the milestone-14
issue; automation follows only if pilots actually ask.

**5. Retention.** Tenant data lives while the tenant lives. Dead letters
and debug logs keep their short retention; backups stay 7-day rolling.
On termination: full tenant export handed over, then erased after 30
days.

**6. No new processors without a posture check.** The provider-seam
pattern (payments #164, geocoder #232) is also the GDPR seam: any new
external service (SMS, push, paid maps) answers the same three questions
before it ships — where does the data go, who else sees it, what is
retained.

## Consequences

- The engineering minimum is tracked now: the `consent_marketing` column
  as a checkbox on #223, and erasure/export + the privacy notice as the
  new milestone-14 issue — both due before pilot outreach, neither
  blocking the onboarding build itself.
- The privacy notice is a plain-language storefront page, linked at
  checkout and in the footer — the same honesty posture as #192's
  redaction, which is the internal half of it.
- OpenAI stays the ONE cross-border processor and is named in the DPA.
  If a client refuses even that, the copilot's canned-answer rung is the
  data-residency path: the degraded mode is also the no-external-sharing
  mode.
- Deliberately NOT done in v1: a DPO (not required at this scale —
  processor role, small volume), formal certifications (SOC2/ISO — later,
  if a client's procurement demands it), cookie banners (the storefront
  sets no tracking cookies to consent to).
