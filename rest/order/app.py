"""
Order Service (REST version).

Accepts an order and asks the Inventory Service (over REST/JSON) whether
the item and quantity are available.
Endpoint: POST /orders   body: {"item_id": "apple", "quantity": 2}

The Order -> Inventory call has a timeout. If Inventory is too slow,
the Order Service returns 504 instead of hanging, and keeps running.
"""
import logging
import os
import time
import uuid

import requests
from flask import Flask, g, jsonify, request

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [order-rest] %(message)s")
log = logging.getLogger(__name__)

PORT = int(os.getenv("ORDER_PORT", "8000"))
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://localhost:8001")
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT_SEC", "1"))
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT_SEC", "2"))


@app.before_request
def start_timer():
    g.start = time.perf_counter()
    g.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))


@app.after_request
def log_request(response):
    latency_ms = (time.perf_counter() - g.start) * 1000
    response.headers["X-Request-ID"] = g.request_id
    log.info(
        "request_id=%s method=%s path=%s status=%s latency_ms=%.1f",
        g.request_id, request.method, request.path, response.status_code, latency_ms,
    )
    return response


@app.get("/health")
def health():
    return jsonify({"service": "order", "status": "ok"}), 200


@app.post("/orders")
def create_order():
    data = request.get_json(silent=True) or {}
    item_id = data.get("item_id")
    quantity = data.get("quantity")

    if not isinstance(item_id, str) or not item_id:
        return jsonify({"error": "invalid_request", "message": "item_id must be a non-empty string"}), 400
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        return jsonify({"error": "invalid_request", "message": "quantity must be a positive integer"}), 400

    call_start = time.perf_counter()
    try:
        resp = requests.post(
            f"{INVENTORY_URL}/inventory/check",
            json={"item_id": item_id, "quantity": quantity},
            headers={"X-Request-ID": g.request_id},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
    except requests.exceptions.ReadTimeout:
        elapsed = time.perf_counter() - call_start
        log.warning("request_id=%s inventory TIMEOUT after %.2fs (limit %.1fs)",
                    g.request_id, elapsed, READ_TIMEOUT)
        return jsonify({
            "status": "failed",
            "error": "inventory_timeout",
            "message": f"Inventory Service did not respond within {READ_TIMEOUT}s",
            "request_id": g.request_id,
        }), 504
    except requests.exceptions.ConnectionError:
        log.warning("request_id=%s inventory UNREACHABLE", g.request_id)
        return jsonify({
            "status": "failed",
            "error": "inventory_unavailable",
            "message": "Inventory Service is unreachable",
            "request_id": g.request_id,
        }), 503

    body = resp.json() if resp.content else {}

    if resp.status_code == 200:
        return jsonify({
            "status": "confirmed",
            "order_id": str(uuid.uuid4()),
            "item_id": item_id,
            "quantity": quantity,
            "request_id": g.request_id,
        }), 201
    if resp.status_code == 404:
        return jsonify({"status": "rejected", "error": "item_not_found",
                        "item_id": item_id, "request_id": g.request_id}), 404
    if resp.status_code == 409:
        return jsonify({"status": "rejected", "error": "insufficient_stock",
                        "item_id": item_id, "requested": quantity,
                        "in_stock": body.get("in_stock"), "request_id": g.request_id}), 409
    if resp.status_code == 400:
        return jsonify({"status": "rejected", "error": "invalid_request",
                        "detail": body, "request_id": g.request_id}), 400

    log.warning("request_id=%s inventory returned unexpected status %s", g.request_id, resp.status_code)
    return jsonify({"status": "failed", "error": "inventory_error",
                    "upstream_status": resp.status_code, "request_id": g.request_id}), 502


if __name__ == "__main__":
    log.info("starting on port %d, inventory=%s, timeout=(%.1fs connect, %.1fs read)",
             PORT, INVENTORY_URL, CONNECT_TIMEOUT, READ_TIMEOUT)
    app.run(host="0.0.0.0", port=PORT, threaded=True)