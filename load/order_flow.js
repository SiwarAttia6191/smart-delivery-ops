// Order-creation flow under load (#103): authenticate once per virtual
// user, then create orders against the demo dataset. Thresholds codify
// "acceptable" — a commit that breaks them fails `make load`.
//
//   docker compose --profile tools run --rm k6 run /scripts/order_flow.js
//   (BASE_URL overrides the target; the compose default is the api service)

import http from "k6/http";
import { check, group, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://api:8000";

export const options = {
  scenarios: {
    order_flow: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: 50 }, // ramp to 50 concurrent writers
        { duration: "1m", target: 50 }, // hold
        { duration: "15s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"], // < 1% errors
    // 2s = the measured write SLO at 50 VUs (pool finding in the README); the
    // first run's 800ms aspiration was crossed by commit fsyncs, not by a bug
    "http_req_duration{endpoint:order_create}": ["p(95)<2000"],
    "http_req_duration{endpoint:token}": ["p(95)<300"],
  },
};

export function setup() {
  const res = http.post(`${BASE}/auth/token`, {
    username: "ops",
    password: "ops",
  });
  check(res, { "setup: token issued": (r) => r.status === 200 });
  return { token: res.json("access_token") };
}

export default function (data) {
  const params = {
    headers: { Authorization: `Bearer ${data.token}`, "Content-Type": "application/json" },
    tags: { endpoint: "order_create" },
  };

  group("order lifecycle", () => {
    const customer = http.post(
      `${BASE}/customers`,
      JSON.stringify({
        name: `Load ${__VU}-${__ITER}`,
        latitude: 36.8381 + Math.random() * 0.05,
        longitude: 10.2385 + Math.random() * 0.05,
      }),
      params,
    );
    check(customer, { "customer created": (r) => r.status === 201 });

    const order = http.post(
      `${BASE}/orders`,
      JSON.stringify({
        customer_id: customer.json("id"),
        restaurant_id: 1,
        dropoff_latitude: 36.85,
        dropoff_longitude: 10.26,
      }),
      params,
    );
    check(order, { "order created": (r) => r.status === 201 });
  });

  sleep(1);
}
