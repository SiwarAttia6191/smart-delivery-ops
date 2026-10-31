# 0003 — Hosting migration when the repository goes private

Date: 2026-10-31 · Status: accepted (plan of record) · Trigger: flipping
the GitHub repository to PRIVATE

## Context

Two static pages are served by GitHub Pages from `main`:

- `docs/index.html` — the **portfolio** page: engineering walkthrough,
  benchmarks, architecture, every claim linked to the code it comes from.
- `docs/product.html` — the **product** page (#125): the sales pitch and
  the three tiers, carrying **no code links** by design.

Pages-from-main works only while the repository is PUBLIC. On the free
plan, flipping to private stops the site. The decision below was made
together with the landing split: the two pages have different
dependencies on a public repo, so they have different migration stories.

## Decision

**1. The product page is the asset that must never die.** It has no code
links, so it deploys unchanged to any static host. When the repo goes
private: deploy `docs/product.html` (+ `styles.css`, its only local
dependency) to **Cloudflare Pages or Vercel's free tier** — both build
static sites from private repos, and their hobby terms cover a product
landing. Mechanics are identical to Pages: watch the repo, publish
`docs/`. The GitHub Pages URL dies with the flip; the new host gets a
redirect notice for one release cycle.

**2. The portfolio page loses its superpower when the code goes dark.**
"Every claim traces to code" is only true while the code is reachable.
Options, in order:

- **GitHub Pro ($4/month): Pages keeps working from the private repo.**
  Cheapest path that preserves both pages and every evidence link. The
  default choice unless the repo goes private for an extended period.
- **Fold the portfolio into the product site.** Rebuild the portfolio
  page as a self-contained PDF/site with screenshots and redacted
  excerpts in place of live links; the benchmarks and architecture
  narrative survive, the click-through-to-code claims become figures.
  This is the fallback for a long-lived private repo on the free plan.

**3. What does NOT migrate:** anything pulling from a private API at
runtime (the live-demo wiring on the portfolio page degrades to its
static fallback by design — the embedded browsers never reach a private
repo's API anyway).

## Consequences

- The product page is written host-agnostic (relative `styles.css` link,
  no repo-absolute URLs) — verified at split time.
- The flip checklist: deploy `docs/` to Cloudflare/Vercel → set the
  redirect from the dead Pages URL → decide GitHub Pro vs. portfolio
  rebuild → update the README badge/link block.
- Revisit if the repo stays public — this plan costs nothing until then.

## References

- #114 (this plan), #125/#126 (the landing split that shaped it)
- docs/product.html, docs/index.html, docs/styles.css
