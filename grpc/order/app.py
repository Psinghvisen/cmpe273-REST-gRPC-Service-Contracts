"""
Order Service (gRPC version).

Clients still place orders over HTTP/JSON (POST /orders), exactly like the
REST version. The difference is the Order -> Inventory call, which uses gRPC
with a deadline instead of REST with a timeout.

If Inventory takes longer than the deadline, gRPC returns DEADLINE_EXCEEDED,
the Order Service turns that into a 504, and keeps running.
"""
import logging
import os
import sys
import time
import uuid

import grpc
from flask import Flask, g, jsonify, request

# Make the generated stubs in grpc/proto importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "proto"))
import inventory_pb2  # noqa: E402
import inventory_pb2_grpc  # noqa: E402

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [order-grpc] %(message)s")
log = logging.getLogger(__name__)

PORT = int(os.getenv("ORDER_PORT", "9000"))
INVENTORY_TARGET = os.getenv("INVENTORY_GRPC_TARGET", "localhost:50051")
DEADLINE_SEC = float(os.getenv("GRPC_DEADLINE_SEC", "2"))

# One channel, reused for every request (channels are expensive to create)
channel = grpc.insecure_channel(INVENTORY_TARGET)
stub = inventory_pb2_grpc.InventoryStub(channel)


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
    return jsonify({"service": "order-grpc", "status": "ok"}), 200


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
        resp = stub.CheckAvailability(
            inventory_pb2.CheckRequest(item_id=item_id, quantity=quantity),
            timeout=DEADLINE_SEC,                        # the gRPC deadline
            metadata=[("x-request-id", g.request_id)],   # propagate request ID
        )
    except grpc.RpcError as e:
        code = e.code()
        elapsed = time.perf_counter() - call_start

        if code == grpc.StatusCode.DEADLINE_EXCEEDED:
            log.warning("request_id=%s inventory DEADLINE_EXCEEDED after %.2fs (deadline %.1fs)",
                        g.request_id, elapsed, DEADLINE_SEC)
            return jsonify({
                "status": "failed",
                "error": "inventory_deadline_exceeded",
                "grpc_status": code.name,
                "message": f"Inventory Service did not respond within the {DEADLINE_SEC}s deadline",
                "request_id": g.request_id,
            }), 504
        if code == grpc.StatusCode.UNAVAILABLE:
            log.warning("request_id=%s inventory UNAVAILABLE", g.request_id)
            return jsonify({"status": "failed", "error": "inventory_unavailable",
                            "grpc_status": code.name, "request_id": g.request_id}), 503
        if code == grpc.StatusCode.NOT_FOUND:
            return jsonify({"status": "rejected", "error": "item_not_found", "grpc_status": code.name,
                            "message": e.details(), "request_id": g.request_id}), 404
        if code == grpc.StatusCode.FAILED_PRECONDITION:
            return jsonify({"status": "rejected", "error": "insufficient_stock", "grpc_status": code.name,
                            "message": e.details(), "request_id": g.request_id}), 409
        if code == grpc.StatusCode.INVALID_ARGUMENT:
            return jsonify({"status": "rejected", "error": "invalid_request", "grpc_status": code.name,
                            "message": e.details(), "request_id": g.request_id}), 400

        log.warning("request_id=%s inventory unexpected gRPC status %s", g.request_id, code.name)
        return jsonify({"status": "failed", "error": "inventory_error", "grpc_status": code.name,
                        "request_id": g.request_id}), 502

    return jsonify({
        "status": "confirmed",
        "order_id": str(uuid.uuid4()),
        "item_id": resp.item_id,
        "quantity": resp.requested,
        "request_id": g.request_id,
    }), 201


if __name__ == "__main__":
    log.info("starting on port %d, inventory=%s, deadline=%.1fs", PORT, INVENTORY_TARGET, DEADLINE_SEC)
    app.run(host="0.0.0.0", port=PORT, threaded=True)