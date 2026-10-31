// Read-heavy analytics traffic (#103): the dashboard/scraper profile.
//   docker compose --profile tools run --rm k6 run /scripts/analytics_read.js

import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://api:8000";

export const options = {
  scenarios: {
    reads: {
      executor: "ramping-arrival-rate", // arrivals, not VUs — closer to scrapers
      startRate: 10,
      timeUnit: "1s",
      preAllocatedVUs: 20,
      stages: [
        { duration: "20s", target: 50 }, // ramp to 50 req/s
        { duration: "40s", target: 50 },
        { duration: "10s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    // 400ms = measured SLO at 50 req/s: summary aggregates + outbox gauges
    "http_req_duration{endpoint:summary}": ["p(95)<400"],
    // 750ms = measured operator-view SLO at 50 req/s: by-tenant scopes one
    // session per tenant (RLS slices) and stays ~4ms idle, ~700ms at load
    "http_req_duration{endpoint:by_tenant}": ["p(95)<800"],
  },
};

export default function () {
  const summary = http.get(`${BASE}/analytics/summary`, { tags: { endpoint: "summary" } });
  check(summary, { "summary ok": (r) => r.status === 200 });

  const byTenant = http.get(`${BASE}/analytics/by-tenant`, { tags: { endpoint: "by_tenant" } });
  check(byTenant, { "by-tenant ok": (r) => r.status === 200 });

  http.get(`${BASE}/analytics/metrics`, { tags: { endpoint: "metrics" } });
  http.get(`${BASE}/orders`, { tags: { endpoint: "orders_list" } });

  sleep(1);
}
