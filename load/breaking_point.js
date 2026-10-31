// The breaking-point probe (#116): 300 concurrent readers holding the API
// for a minute — the saturation shape behind #103's measured ceiling
// (~29% timeouts at 300 VUs on the single-process API). Committed so the
// before/after of every scaling step is the SAME run, not an ad-hoc one.
//   docker compose --profile tools run --rm k6 run /scripts/breaking_point.js

import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://api:8000";

export const options = {
  scenarios: {
    saturate: {
      executor: "constant-vus",
      vus: Number(__ENV.BP_VUS || 300),
      duration: "60s",
    },
  },
  // the breaking point is a MEASUREMENT, not a gate: this probe is allowed
  // to fail its own thresholds — the numbers it reports are the product
  thresholds: {
    http_req_failed: ["rate<0.99"],
  },
  tags: { endpoint: "summary" },
};

export default function () {
  const res = http.get(`${BASE}/analytics/summary`, { tags: { endpoint: "summary" } });
  check(res, { "status 200": (r) => r.status === 200 });
  sleep(0.2);
}
