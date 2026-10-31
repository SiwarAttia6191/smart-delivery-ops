"""The visible default alert destination (#117).

Alertmanager's committed route delivers to this sink: every webhook POST
lands in the container log (`docker compose logs alert-echo`) as
ALERT DELIVERED — which makes the paging flow provable out of the box,
before the operator swaps the URL in observability/alertmanager.yml for
a real Slack/email/pager destination. Stdlib only; the container has no
app code.
"""

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")


class AlertEcho(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {"unparseable": True}
        alerts = payload.get("alerts", [])
        for alert in alerts:
            logging.warning(
                "ALERT DELIVERED status=%s %s %s",
                payload.get("status"),
                alert.get("labels"),
                str(alert.get("annotations"))[:400],
            )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args) -> None:  # quiet the access lines
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9094), AlertEcho).serve_forever()
