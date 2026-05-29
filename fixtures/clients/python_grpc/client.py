"""Fixture: a gRPC client using a generated *_pb2_grpc stub."""

from __future__ import annotations

from typing import Any

import grpc
import inventory_pb2 as pb
import inventory_pb2_grpc as pbg


def list_skus(channel: grpc.Channel) -> Any:
    stub = pbg.InventoryStub(channel)
    return stub.ListSkus(pb.ListSkusRequest(limit=20, cursor=""))


def get_sku(channel: grpc.Channel, sku_id: str) -> Any:
    stub = pbg.InventoryStub(channel)
    return stub.GetSku(pb.GetSkuRequest(id=sku_id))


def reserve(channel: grpc.Channel, sku_id: str, qty: int) -> Any:
    stub = pbg.InventoryStub(channel)
    return stub.Reserve(pb.ReserveRequest(id=sku_id, qty=qty))
