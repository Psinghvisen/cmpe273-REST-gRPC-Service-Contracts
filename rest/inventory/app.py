"""
Inventory Service (REST version).

Checks whether a requested item and quantity are available.
Endpoint: POST /inventory/check   body: {"item_id": "apple", "quantity": 2}

Set INVENTORY_DELAY_SEC to make every check slow on purpose
(used for the timeout failure demo).
"""
import logging
import os
import time
import uuid

from flask import Flask, g, jsonify, request

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [inventory-rest] %(message)s")
log = logging.getLogger(__name__)

PORT = int(os.getenv("INVENTORY_PORT", "8001"))
DELAY_SEC = float(os.getenv("INVENTORY_DELAY_SEC", "0"))

# In-memory stock table: item_id -> units on hand
STOCK = {
    "apple": 10,
    "notebook": 5,
    "pen": 0,
}


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
    return jsonify({"service": "inventory", "status": "ok"}), 200


@app.post("/inventory/check")
def check_inventory():
    data = request.get_json(silent=True) or {}
    item_id = data.get("item_id")
    quantity = data.get("quantity")

    # 400: malformed request
    if not isinstance(item_id, str) or not item_id:
        return jsonify({"error": "invalid_request", "message": "item_id must be a non-empty string"}), 400
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        return jsonify({"error": "invalid_request", "message": "quantity must be a positive integer"}), 400

    # Injected slowness for the failure-handling demo
    if DELAY_SEC > 0:
        log.info("request_id=%s injecting delay of %.1fs", g.request_id, DELAY_SEC)
        time.sleep(DELAY_SEC)

    # 404: item does not exist
    if item_id not in STOCK:
        return jsonify({"error": "item_not_found", "item_id": item_id}), 404

    in_stock = STOCK[item_id]

    # 409: item exists but not enough units
    if in_stock < quantity:
        return jsonify({
            "available": False,
            "item_id": item_id,
            "requested": quantity,
            "in_stock": in_stock,
        }), 409

    # 200: available
    return jsonify({
        "available": True,
        "item_id": item_id,
        "requested": quantity,
        "in_stock": in_stock,
    }), 200


if __name__ == "__main__":
    log.info("starting on port %d (delay=%.1fs)", PORT, DELAY_SEC)
    app.run(host="0.0.0.0", port=PORT)