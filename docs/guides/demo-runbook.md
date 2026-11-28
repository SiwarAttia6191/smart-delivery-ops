# The demo runbook: five scenes that show the strong points

The presenter's script for showing this platform's differentiators live.
Every scene is a real API flow — nothing is mocked — and every scene ends
with visible proof on screen, with the ACTUAL captures from real runs
attached as evidence. Total run: ~15 minutes. Pre-flight: the stack is up
(`GET /health` returns ok), and open each surface fresh (the pages always
revalidate, so a reload is always current).

> **On demo day you open one page: `/storefront/guides/demo-runbook.html` —
> the script, the talking points, the evidence, all rendered — and click
> through the scenes from there. The demos hub (`/storefront/demos.html`)
> links everything else.**

## How to pitch it — the day-of narrative

**The opening line (before clicking anything):**

> *"Every delivery platform starts with an address someone typed. Everything
> that goes wrong later — wrong doors, late couriers, support tickets —
> usually started there. We built the platform that doesn't trust that
> address until it's been verified — by machines and, finally, by the
> person who actually knows the door."*

Then run Scene 1. It is the hook: the audience watches the system catch
its own supplier's lie, on screen, in real time.

**The arc to keep in your head (it maps 1:1 to the scenes):**

1. *Trust nothing* — the geocoder lies confidently and caches the lie
   (Scene 1, the flag).
2. *The human closes the loop* — the customer drags the pin; the debate is
   over (Scene 1, the drag).
3. *Prove the whole thing at scale* — one command, whole city, exit code
   (Scene 2).
4. *Explain yourself* — every point carries its verdict, its witnesses and
   who vouched (Scene 3).
5. *The identity side* — restaurants set their own door; the money side of
   the marketplace gets the same rigor (Scene 4).
6. *It generalizes* — any city, any routing engine (Scene 5).

**Audience calibration:**

- **Business / restaurant owner**: lead with Scene 4 ("your door is your
  identity — Uber Eats makes you set it by hand too; we just also PROVE
  it"), then Scene 1 as "why your customers stop complaining."
- **Technical audience**: lead with Scene 2 (the exit code, the witnesses,
  the topology knobs), then Scene 3 (the trail as your debugging
  superpower: "the data answers the five questions").
- **Investor**: the one-breath version — *"the platforms buy forgiveness:
  support orgs and feedback fleets. We buy certainty: one command that
  proves the whole city before the first customer orders."*

**When something fails on stage (it is part of the story):**

- Nominatim throttles (HTTP 629 / timeouts) → *"and there's the honest
  degrade — the audit reports its own provider's health instead of lying."*
- A verdict flips to `ok` (map data improved) → use the Scene 1 fallback
  address.
- Docker cold → the recovery recipe below; say *"and the runbook even
  covers the stage failing."*

**Maintenance rule (AGENTS.md rule 8):** when a round ships a
customer-visible feature, add or update a scene in the SAME commit, and
rebuild the api image before presenting — the served pages and the drill's
input files live in the image, not the working tree.

---

## Scene 1 · The lie detector (the precision loop)

**Open:** `/storefront/ordering-demo.html`
**Do:** pick any restaurant → type `Calle de Fuencarral 24, Madrid` → *Quote the fee*.

**What appears:** the self-check row — `⚠ street check FAILED — the point
sits on a DIFFERENT street than the text; drag the pin` — and the pin map,
with the point visibly on **Valverde**, not Fuencarral.

**Say:** *"The geocoder answered confidently — and our two independent
witnesses caught it. This exact bug shipped on big platforms; ours is
detected before the order exists. The geocoder is guilty until proven
innocent."*

**Do:** drag the pin ~1 cm south onto Fuencarral → the hint flips to
*"Pin moved — the courier rides to YOUR point now."* → *Quote the fee*
again → the verdict row flips to `✓ placed by hand on the map` and the
distance reprices to YOUR point.

**Say:** *"The customer's hand outranks the geocoder and outranks a
re-price click. Coordinates are the truth; the text is just the label."*

**Do:** *Sign in* (ops/ops) → *Place order* → open the tracking link →
the green badge: `✓ Customer-pinned — this point was placed by hand on
the map at checkout.`

**Evidence — the two demo orders, side by side (the whole story in one
table; captured from the live API):**

| order | stored dropoff point | verdict badge |
|---|---|---|
| the stale-page order | `40.4221271, -3.7008484` — the geocoder's point, on Valverde | ⚠ street check FAILED |
| the fixed-page order | `40.4215744, -3.7008261` — the point the DRAG created | ✓ Customer-pinned |

Same typed address, same clicks — the difference is which page ran. That
pair is also why the checkout pins always revalidate after a deploy
(#362) and why a drag survives re-quoting (#356).

**The verdict vocabulary (what each flag means, in order of severity):**

| verdict | who said it | meaning |
|---|---|---|
| `ok` | the snap witness, confirmed by the reverse | the point sits on the requested street |
| `street_mismatch` | the snap (decisive) | the point sits on a DIFFERENT street — the parallel-street trap |
| `number_mismatch` | the reverse | street matched, door number drifts |
| `off_network` | the snap distance | >150 m from any road — not an address |
| `unverified` | nobody | witnesses abstained — honest, never a guess |
| `customer_pinned` | THE HUMAN | the customer dragged the pin; coords = truth, text = label |
| `owner_verified` | THE HUMAN | the owner set their door (#358) — outranks both witnesses |

**Fallback:** if the verdict row reads `ok` (map data improved since),
pick `Calle de Santa Isabel 8, Madrid` — the drill currently flags it as
a corner case. If the badge is slow, the page polls every 4 s.

---

## Scene 2 · The city drill (the audit gate)

**Run** (inside the api container or wherever the stack's env lives):

```
python scripts/verify_city_addresses.py --file data/city-addresses/madrid.txt --restaurants --routes 5
```

The input file is the demo's canon — 24 real Madrid addresses across
every district, including the two teaching cases (Fuencarral 24, the
parallel-street catch; Santa Isabel 8, the corner case).

**What appears:** a verdict table — every address fresh-resolved and
judged by both witnesses, every pickup point audited down to the map's
own door numbers, the router probed with test pairs — ending in
`N checked · N flagged` and a meaningful exit code.

**Evidence — a clean capture (the rows that matter, from a real run):**

```text
✓ street_confirmed  pickup · Casa Toni — Malasaña            Calle del Espíritu Santo (13 m) · reverse: Calle del Espíritu Santo · pin: owner_verified
✓ street_confirmed  pickup · Verde Veggie — Lavapiés         Plaza de Lavapiés (9 m) · reverse: Plaza de Lavapiés · doors ['1','2','4a','5','6','8']
✓ plaza_pickup      pickup · Bodega de Sol — Puerta del Sol  Pasaje de la Caja de Ahorros (106 m) · reverse: Plaza de la Puerta del Sol · pin: imported
✓ ok                router probe 40.4150,-3.7065 → 40.4221,-3.7008  road 3044 m vs straight 921 m = 3.3x
✓ ok                router probe 40.4221,-3.7008 → 40.4150,-3.7008  road 3936 m vs straight 921 m = 4.3x
N checked · 0 flagged
```

**Evidence — a throttled capture (Nominatim rate-limits under heavy
testing; the drill reports its own provider's health instead of lying):**

```text
✗ provider_error   Calle de Goya 30, Madrid     HTTP Error 629: Unknown Error
✓ ok               Calle de Toledo 25, Madrid   (40.413869, -3.707384) · snap 5 m · reverse: 25, Calle de Toledo, ...
34 checked · 11 flagged — FIX BEFORE GO-LIVE
```

**Say:** *"One command proves an entire city before the first customer
orders. The big platforms absorb wrong addresses with support orgs; we
run a 4-minute audit with an exit code. It's the onboarding gate for any
new city — not just Madrid. And when OUR provider throttles, the audit
says so loudly — it never fills silence with a guess."*

**The market comparison (if the audience asks "doesn't everyone do this?"):**

| | WITHOUT the drill (typical platform, incl. big players) | WITH the drill |
|---|---|---|
| when wrongness is found | when a customer/courier complains — one ticket at a time | before go-live (onboarding gate) or on demand — whole city in minutes |
| who finds it | support orgs (Wolt runs a 24/7 layer for "wrong location" tickets) | a deterministic script with an exit code |
| "which points are suspect RIGHT NOW?" | no such command — the data is a black box | one command, a verdict table, provenance per point |
| record of who verified | usually nothing | pin_source + verdict chain on every row |

The big players fill the gap with scale: support organizations, one
trusted commercial source (Google Maps — the single-witness mistake at
industry scale), courier feedback loops, paid validation APIs (Google
Address Validation, Smarty). Those work at their scale; ours is the
right-size tool for a lean platform.

**Fallback:** Overpass (the door-number witness) is a public service and
sometimes down — the rows then read `doors n/a (Overpass down)` and the
audit still completes: degradation is part of the demo. A throttled run
(reads `provider_error`) is the same honest degrade — rerun after a few
minutes for the clean table.

---

## Scene 3 · The data trail (provenance)

**Do:** open a completed order's tracking page → then `GET /orders/{id}`
in Swagger → show the `created` event payload.

**What appears:** the order's whole explanation — the typed address, the
resolved display name, the verdict (`dropoff_precision`),
the snap distance in meters — plus, per restaurant, `pin_source`
telling WHO vouched for the pickup point.

**Evidence — the five questions a wrong delivery raises, and the fields
that answer them (all real values from the live system):**

| the question | the field that answers it |
|---|---|
| where exactly did the order go? | the created event's resolved point + display name |
| was the point actually wrong? | `dropoff_precision: street_mismatch` |
| how wrong, and where did it land? | `dropoff_snap_m: 77` — 77 m away, on Calle de Valverde |
| who vouched for the pickup point? | `restaurants.pin_source: owner_verified` |
| has this been checked, and when? | the drill report — re-runnable any minute |

**Say:** *"When something goes wrong, our data answers the five
questions — where, was it wrong, how wrong, who vouched, when was it
last checked. Most platforms answer with a support ticket; ours answers
with fields."*

**Fallback:** any delivered order works; the old ones (pre-#343) read
`unverified` — say so honestly: *"verdicts began on the day we shipped
the layer; the drill can backfill them."*

---

## Scene 4 · The owner's door (the pickup identity)

**Open:** `/storefront/restaurant-portal.html` → sign in (ops/ops) →
the **Your door on the map** card.

**What appears:** the current pickup point, the provenance badge
(`owner_verified` / `imported`), and a draggable pin on the actual street.

**Evidence — the live capture (after the owner saved their door):**

```text
PATCH /restaurants/1/pickup-point  →
{ "latitude": 40.4256, "longitude": -3.7043,
  "address": "Calle del Espíritu Santo 12, Madrid",
  "pin_source": "owner_verified", "precision": "owner_verified" }
```

**Do:** drag the pin to the storefront entrance → *Save my door* → the
note flips to `✓ saved — this door is YOURS now (the drill keeps
auditing it)`.

**Say:** *"Uber Eats calls this Adjust Pin. The pickup point is the
restaurant's identity — every fee, route and ETA flows from it — and the
person who knows the door is the one witness that cannot be fooled.
That's the same principle on the money side of the marketplace."*

---

## Scene 5 · The architecture story

**Open:** `docs/guides/osrm-multi-city-routing.md` §5b — the witness
mermaid, the "does this generalize?" answer, and the witness-stack diagram
(machines advise, the human decides).

**Say:** *"Any city: the decisive witness is the city's OWN router
graph. Any routing provider: the snap question is provider-free, one
seam away. And the strongest witness — the dragged pin — needs no
provider at all. Onboarding a new city ends with this drill as its
verification gate. The big platforms buy forgiveness — support orgs and
feedback fleets; a small platform buys certainty — one command that
proves the whole city before the first customer ever orders."*

**The market references behind the claims (shown only if asked):**
Uber Eats' merchant "Adjust Pin" and the customer map pin; Domino's +
what3words for precise delivery points; Google Address Validation API's
confidence verdicts; the ~15%-of-delivery-issues-are-addresses figure.
All cited in the repo's market analysis notes.

---

## Pre-flight checklist

- [ ] `GET /health` returns ok (API + database up)
- [ ] the OSRM router container is running (Scene 1's verdicts need it)
- [ ] the api image is REBUILT if main moved — the served pages and the
      drill's input file live in the image, not the working tree
- [ ] the drill ran recently — or run Scene 2 FIRST and show its output
- [ ] a throwaway order is fine: demo orders are real but disposable

**If Docker is down on stage:** relaunch Docker Desktop, then
`docker start madrid-demo-postgres-1 osrm-madrid madrid-demo-api-1` —
the stack does not auto-start with the daemon. Everything above works
the moment `/health` answers.
