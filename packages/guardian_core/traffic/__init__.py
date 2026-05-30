"""Traffic replay ingestion: HAR + gRPC log parsing, schema inference, defacto merge."""

from guardian_core.traffic.defacto import build_defacto_contract
from guardian_core.traffic.grpc_parser import GrpcCallRecord, parse_grpc_log
from guardian_core.traffic.har_parser import HarRequestRecord, parse_har_bytes
from guardian_core.traffic.ingestor import IngestResult, ingest_traffic
from guardian_core.traffic.schema_inference import infer_schema, walk_field_paths
from guardian_core.traffic.url_match import RouteTree, normalize_observed_path

__all__ = [
    "GrpcCallRecord",
    "HarRequestRecord",
    "IngestResult",
    "RouteTree",
    "build_defacto_contract",
    "infer_schema",
    "ingest_traffic",
    "normalize_observed_path",
    "parse_grpc_log",
    "parse_har_bytes",
    "walk_field_paths",
]
