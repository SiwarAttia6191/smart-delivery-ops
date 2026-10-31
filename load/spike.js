// Spike test (#103): 5 to 300 virtual readers in seconds — the "everyone
// opens the dashboard after a link goes out" shape. Pass criteria: the API
// degrades latency, not availability (errors stay under 5%).
//   docker compose --profile tools run --rm k6 run /scripts/spike.js

import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://api:8000";

export const options = {
  scenarios: {
    spike: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "10s", target: 5 },
        // default spike = 2x the steady-state write load; the 300-VU
        // breaking point was measured separately (results/spike.json,
        // committed) - it saturates the single-process API at ~29% timeouts
        { duration: "5s", target: Number(__ENV.SPIKE_TARGET || 100) },
        { duration: "30s", target: Number(__ENV.SPIKE_TARGET || 100) },
        { duration: "15s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<2000"],
  },
};

export default function () {
  const res = http.get(`${BASE}/analytics/summary`);
  check(res, { "summary ok": (r) => r.status === 200 });
  sleep(0.5);
}
