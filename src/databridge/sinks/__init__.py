from __future__ import annotations

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink
from databridge.sinks.dataset_mock import DatasetMockSink
from databridge.sinks.annotator_mock import AnnotatorMockSink
from databridge.sinks.local_zip import LocalZipSink
from databridge.sinks.local_jsonl import LocalJsonlSink
from databridge.sinks.s3_jsonl import S3JsonlSink
from databridge.sinks.s3_zip import S3ZipSink

_SINK_REGISTRY: dict[str, type[BaseSink]] = {
    "dataset-mock": DatasetMockSink,
    "annotator-mock": AnnotatorMockSink,
    "local-zip": LocalZipSink,
    "local-jsonl": LocalJsonlSink,
    "s3-jsonl": S3JsonlSink,
    "s3-zip": S3ZipSink,
}


def get_sink(config: DatasinkConfig) -> BaseSink:
    cls = _SINK_REGISTRY.get(config.type)
    if cls is None:
        raise ValueError(f"Unknown datasink type: {config.type!r}")
    return cls(config)
