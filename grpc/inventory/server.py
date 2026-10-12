"""
Inventory Service (gRPC version).

Implements the Inventory.CheckAvailability RPC defined in grpc/proto/inventory.proto.

Set INVENTORY_DELAY_SEC to make every check slow on purpose
(used for the deadline failure demo).
"""
import logging
import os
import sys
import time
from concurrent import futures

import grpc

# Make the generated stubs in grpc/proto importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "proto"))
import inventory_pb2  # noqa: E402
import inventory_pb2_grpc  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [inventory-grpc] %(message)s")
log = logging.getLogger(__name__)

PORT = int(os.getenv("INVENTORY_GRPC_PORT", "50051"))
DELAY_SEC = float(os.getenv("INVENTORY_DELAY_SEC", "0"))

# Same in-memory stock table as the REST version
STOCK = {
    "apple": 10,
    "notebook": 5,
    "pen": 0,
}


class InventoryServicer(inventory_pb2_grpc.InventoryServicer):
    def CheckAvailability(self, request, context):
        start = time.perf_counter()
        metadata = dict(context.invocation_metadata())
        request_id = metadata.get("x-request-id", "none")
        code = grpc.StatusCode.OK
        try:
            # INVALID_ARGUMENT: malformed request
            if not request.item_id:
                code = grpc.StatusCode.INVALID_ARGUMENT
                context.abort(code, "item_id must be a non-empty string")
            if request.quantity <= 0:
                code = grpc.StatusCode.INVALID_ARGUMENT
                context.abort(code, "quantity must be a positive integer")

            # Injected slowness for the failure-handling demo
            if DELAY_SEC > 0:
                log.info("request_id=%s injecting delay of %.1fs", request_id, DELAY_SEC)
                time.sleep(DELAY_SEC)

            # NOT_FOUND: item does not exist
            if request.item_id not in STOCK:
                code = grpc.StatusCode.NOT_FOUND
                context.abort(code, f"item '{request.item_id}' not found")

            in_stock = STOCK[request.item_id]

            # FAILED_PRECONDITION: item exists but not enough units
            if in_stock < request.quantity:
                code = grpc.StatusCode.FAILED_PRECONDITION
                context.abort(
                    code,
                    f"insufficient stock for '{request.item_id}': "
                    f"requested {request.quantity}, in stock {in_stock}",
                )

            return inventory_pb2.CheckResponse(
                available=True,
                item_id=request.item_id,
                requested=request.quantity,
                in_stock=in_stock,
            )
        finally:
            latency_ms = (time.perf_counter() - start) * 1000
            log.info(
                "request_id=%s rpc=CheckAvailability item_id=%s quantity=%d status=%s latency_ms=%.1f",
                request_id, request.item_id, request.quantity, code.name, latency_ms,
            )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    inventory_pb2_grpc.add_InventoryServicer_to_server(InventoryServicer(), server)
    server.add_insecure_port(f"[::]:{PORT}")
    server.start()
    log.info("starting on port %d (delay=%.1fs)", PORT, DELAY_SEC)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()