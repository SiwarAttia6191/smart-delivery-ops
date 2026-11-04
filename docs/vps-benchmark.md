# VPS benchmark — the demo box, low cost vs quality (decision 0004 phase B)

Researched 2026-11-04 against the demo unit's ACTUAL requirements (from
`docker-compose.demo.yml`), because the right box is the one the workload
fits — not the biggest one the price range allows.

## What the demo unit needs (measured, not guessed)

| Resource | Demand | Where |
|---|---|---|
| RAM | ~2.5 GB of caps (postgres 768m + api 1g @ 2 workers + osrm 768m) + OS ≈ **3 GB peak** | compose mem_limits |
| CPU | modest — no ML on the demo box; osrm-routed (MLD) is memory-bound, not CPU-bound | 1–2 vCPU plenty |
| Disk | ~2 GB docker images + ~200 MB OSRM extract + small DB → **20 GB comfortable** | volumes |
| Network | tiny (a demo page + health pings); EU-central → Tunisia ≈ 45–60 ms — fine for a guided demo | any EU region |
| Everything else | Docker + a public IPv4; no GPU, no Windows | — |

**Verdict: a 4 GB / 2 vCPU box is the sweet spot** (2 GB works with swap
but runs the caps at the edge; 8 GB is wasted money on a demo).

## The 2026 market (prices moved — a RAM surge raised nearly everyone)

| Provider | ~4 GB plan | Price/mo | Quality notes |
|---|---|---|---|
| **Hetzner CX22** | 2 vCPU / 4 GB | **€3.79–4.59** | best price/GB in 2026 even after the June hike; EU regions (Nuremberg/Falkenstein/Helsinki); included IPv4, snapshots, solid docs |
| **Netcup** | 4 vCPU / 4 GB NVMe | ~€8.74 | often MORE cpu per euro (DDR5, newer gen); bills in minimum periods; EU only |
| **OVH VLE-2** | 2 vCPU / 4 GB | ~$5.50 | raised VPS prices 40–67% in 2026; fine EU coverage incl. closer-to-Tunisia options |
| **Scaleway** | 4 GB tier | ~€7–10 | Paris/Amsterdam (Paris ≈ lowest EU latency to Tunis) |
| **Vultr** | 4 GB | ~$20 | premium pricing; many regions incl. closer ones |
| **DigitalOcean** | Basic 4 GB | ~$24 | premium pricing; the "boring, works" choice |
| **Oracle Cloud (free tier)** | 4 ARM OCPU / 24 GB | **€0** | "always free" ARM — but capacity is hard to get in some regions and idle reclaim happens; ARM = fine for our images (multi-arch) |

Sources: [bitdoze price-increase tracker](https://www.bitdoze.com/vps-price-increases), [netcup-vs-hetzner after the 2026 hikes](https://netcupvoucher.com/blog/netcup-vs-hetzner-after-rampocalypse-2026), [VPSBenchmarks' comparison](https://www.vpsbenchmarks.com/intelligence_chats/edfb2be02e), [eucloudcost Hetzner-vs-OVH](https://www.eucloudcost.com/compare/hetzner-vs-ovh), [getdeploying compute prices](https://getdeploying.com/reference/compute-prices), [ployz cheapest-VPS list](https://ployz.dev/blog/cheapest-vps). 2026 prices moved several times — verify on the provider page at purchase.

## The recommendation

1. **Default: Hetzner CX22 (€4.51 with IPv4).** Best 2026 price/quality,
   EU-central latency to Tunisia is demo-appropriate, and
   `deploy/deploy_demo.sh` is provider-agnostic — it runs there unchanged.
2. **If you want more CPU per euro and accept minimum periods: Netcup.**
3. **If €0 is the constraint: Oracle's free ARM tier is a real option** —
   24 GB RAM dwarfs our needs — with two accepted risks: region
   availability is luck, and Oracle reclaims idle free resources (the
   demo pinger keeps it warm, which helps).
4. **Skip DO/Vultr for this**: 3–4× the price buys regions we don't need.

What flips the choice later: a paying client demo needing their region →
DO/Vultr; heavier ML on the demo box → CPX22 (8 GB) or Netcup.

## Cost summary (decision 0004)

- Hetzner CX22: **~€4.51/mo ≈ €54/year** — the project's first recurring
  cost. Optional domain ~€10/year; DuckDNS = free.
- Everything else in the demo stack: €0 (open-source packages, your own
  Postgres, the OSRM extract from Geofabrik's free download).

## When the box exists

`deploy/deploy_demo.sh` does the rest (docker, clone, secrets, OSRM data,
unit up, seeded, optional Caddy TLS) — see `deploy/README.md`. Sizing is
already coded: the demo compose caps fit 4 GB with ~25% headroom.
