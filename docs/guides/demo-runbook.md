# The demo runbook: five scenes that show the strong points

The presenter's script for showing this platform's differentiators live.
Every scene is a real API flow — nothing is mocked — and every scene ends
with visible proof on screen. Total run: ~15 minutes. Pre-flight: the
stack is up (`GET /health` returns ok), and open each surface fresh (the
pages always revalidate, so a reload is always current).

| scene | surface | the strong point it proves | minutes |
|---|---|---|---|
| 1 | the storefront checkout | the lie detector: geocode → verdict → the customer's hand | 5 |
| 2 | the city drill | prove an entire city in 4 minutes, exit code as the gate | 4 |
| 3 | the data trail | provenance: our data explains itself | 2 |
| 4 | the restaurant portal | the owner's door: the pickup identity is the owner's | 2 |
| 5 | the routing guide §5b | the architecture story, city-agnostic and provider-portable | 2 |

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

**Fallback:** if the verdict row reads `ok` (map data improved since),
pick `Calle de Santa Isabel 8, Madrid` — the drill currently flags it as
a corner case. If the badge is slow, the page polls every 4 s.

---

## Scene 2 · The city drill (the audit gate)

**Run** (inside the api container or wherever the stack's env lives):

```
python scripts/verify_city_addresses.py --file data/city-addresses/madrid.txt --restaurants --routes 5
```

**What appears:** a verdict table — every address fresh-resolved and
judged by both witnesses, every pickup point audited down to the map's
own door numbers, the router probed with test pairs — ending in
`N checked · 0 flagged` and exit code 0. Flip any seeded address and the
same command exits 1.

**Say:** *"One command proves an entire city before the first customer
orders. The big platforms absorb wrong addresses with support orgs; we
run a 4-minute audit with an exit code. It's the onboarding gate for any
new city — not just Madrid."*

**Fallback:** Overpass (the door-number witness) is a public service and
sometimes down — the rows then read `doors n/a (Overpass down)` and the
audit still completes: degradation is part of the demo.

---

## Scene 3 · The data trail (provenance)

**Do:** open a completed order's tracking page → then `GET /orders/{id}`
in Swagger → show the `created` event payload.

**What appears:** the order's whole explanation — the typed address, the
resolved display name, the verdict (`dropoff_precision`),
the snap distance in meters — plus, per restaurant, `pin_source`
telling WHO vouched for the pickup point.

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
mermaid and the "does this generalize?" answer.

**Say:** *"Any city: the decisive witness is the city's OWN router
graph. Any routing provider: the snap question is provider-free, one
seam away. And the strongest witness — the dragged pin — needs no
provider at all. Onboarding a new city ends with this drill as its
verification gate."*

---

## Pre-flight checklist

- [ ] `GET /health` returns ok (API + database up)
- [ ] the OSRM router container is running (Scene 1's verdicts need it)
- [ ] the drill ran recently — or run Scene 2 FIRST and show its output
- [ ] a throwaway order is fine: demo orders are real but disposable
