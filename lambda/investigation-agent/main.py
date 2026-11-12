
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("investigation_agent")

MAX_REQUEST_BYTES = 65536


def investigate(payload):
    if not isinstance(payload, dict):
        return {
            "status": "INVALID_REQUEST",
            "errors": ["Request must be a JSON object"],
        }

    case_id = payload.get("case_id")
    message_id = payload.get("message_id")
    invoice_ids = payload.get("invoice_ids", [])

    errors = []

    if not isinstance(case_id, str) or not case_id.strip():
        errors.append("case_id is required")

    if not isinstance(message_id, str) or not message_id.strip():
        errors.append("message_id is required")

    if (
        not isinstance(invoice_ids, list)
        or not invoice_ids
        or any(
            not isinstance(item, str) or not item.strip()
            for item in invoice_ids
        )
    ):
        errors.append("invoice_ids must be a non-empty string list")

    if errors:
        return {
            "case_id": case_id,
            "message_id": message_id,
            "status": "INVALID_REQUEST",
            "errors": errors,
        }

    return {
        "case_id": case_id,
        "message_id": message_id,
        "invoice_ids": invoice_ids,
        "status": "PENDING_ENTERPRISE_INTEGRATION",
        "investigation_completed": False,
        "payment_status": None,
        "payment_reference": None,
        "enterprise_sources_checked": [],
        "gateway_enabled": False,
        "next_stage": "AWAIT_GATEWAY_INTEGRATION",
    }


class Handler(BaseHTTPRequestHandler):

    def send_json(self, status_code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/ping":
            self.send_json(200, {"status": "Healthy"})
            return

        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/invocations":
            self.send_json(404, {"error": "Not found"})
            return

        try:
            content_length = int(
                self.headers.get("Content-Length", "0")
            )

            if (
                content_length <= 0
                or content_length > MAX_REQUEST_BYTES
            ):
                self.send_json(
                    413,
                    {"error": "Invalid request size"},
                )
                return

            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))

            result = investigate(payload)
            self.send_json(200, result)

        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(
                400,
                {"error": "Invalid JSON request"},
            )
        except Exception:
            logger.exception("Investigation runtime error")
            self.send_json(
                500,
                {"error": "Internal server error"},
            )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    logger.info("Investigation runtime listening on port %s", port)
    server.serve_forever()
