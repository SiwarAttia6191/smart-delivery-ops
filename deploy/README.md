# Demo deployment pack (decision 0004, phase B prepared — awaiting the VPS)

Everything Phase B needs except the box itself. When the provider decision
lands (~€5/month Hetzner CX22 recommended), deployment is:

```bash
# on the VPS, once as a sudo user:
./deploy/deploy_demo.sh                       # docker, clone, secrets, OSRM data, unit up, seeded
DEMO_DOMAIN=demo.<your-domain> ./deploy/deploy_demo.sh   # adds the Caddy TLS front
# nightly clean slate (cron line printed by the deploy script):
30 3 * * * cd /opt/smart-delivery-ops && docker compose -f docker-compose.demo.yml run --rm api python scripts/reset_demo.py
```

Then point the sales pages at it (phase C): product.html's "Try it" section
and index.html's demos hub get `https://<demo-domain>/storefront/ordering-demo.html`
(the API-served, same-origin storefront).

## Contents

| File | What it is |
|---|---|
| `deploy_demo.sh` | one-shot server bootstrap: docker engine, repo clone/pull, secrets (`scripts/ensure_secrets.py`), OSRM extract (`scripts/fetch_osrm_extract.py`), demo unit up + seeded, optional Caddy front. Idempotent — re-running converges. |
| `caddy.override.yml` + `Caddyfile` | automatic-HTTPS reverse proxy in front of the api; compose override so the base demo file stays TLS-agnostic |
| `reset_demo.py` (in `scripts/`) | the nightly reset: truncate the day's orders/events/payments, zero coupon counters, re-seed idempotently |
| `.github/workflows/deploy-demo.yml` | one-click redeploy from the Actions tab after the repo secrets (`DEMO_HOST`/`DEMO_USER`/`DEMO_SSH_KEY`/`DEMO_DOMAIN`) are set — inert until then |

## Deliberate posture (decision 0004)

Published sandbox credentials by design (`ops/ops` printed on the demo
pages); no OpenAI key on the box (the copilot serves its degraded chain);
no real client data ever; the nightly reset bounds any mischief; the
api's host port mapping should be dropped once Caddy fronts it.
