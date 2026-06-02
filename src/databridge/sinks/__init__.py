from __future__ import annotations

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink
from databridge.sinks.dataset_mock import DatasetMockSink
from databridge.sinks.annotator_mock import AnnotatorMockSink
from databridge.sinks.local_zip import LocalZipSink
from databridge.sinks.local_jsonl import LocalJsonlSink

_SINK_REGISTRY: dict[str, type[BaseSink]] = {
    "dataset-mock": DatasetMockSink,
    "annotator-mock": AnnotatorMockSink,
    "local-zip": LocalZipSink,
    "local-jsonl": LocalJsonlSink,
}


def get_sink(config: DatasinkConfig) -> BaseSink:
    cls = _SINK_REGISTRY.get(config.type)
    if cls is None:
        raise ValueError(f"Unknown datasink type: {config.type!r}")
    return cls(config)
