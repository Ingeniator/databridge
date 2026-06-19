from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FilterSnapshot(BaseModel):
    query: str = ""
    start: datetime | None = None
    end: datetime | None = None
    time_field: str | None = None
    limit: int = Field(default=50, ge=1, le=100_000)


class MaskingAction(str, Enum):
    mask = "mask"
    hash = "hash"
    drop = "drop"
    redact = "redact"


class MaskingRule(BaseModel):
    field_path: Annotated[str, Field(min_length=1, max_length=255)]
    action: MaskingAction


class SamplingMethod(str, Enum):
    random = "random"
    systematic = "systematic"
    stratified = "stratified"


class SamplingConfig(BaseModel):
    method: SamplingMethod = SamplingMethod.random
    target_column: str | None = None
    ratio_or_size: float = Field(gt=0.0)
    max_items: int | None = Field(default=None, gt=0)


class PiiFieldsResponse(BaseModel):
    candidate_fields: list[str]


class ExportJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ExportJob(BaseModel):
    id: UUID
    org_id: str
    user_id: str
    datasource_type: Literal["connection", "system"]
    datasource_ref: str
    datasource_filter: FilterSnapshot
    datasink_name: str
    destination_dataset: str
    asset_resolution: bool
    asset_url_fields: list[str]
    asset_url_prefix: str
    asset_datasink_name: str | None
    asset_dataset: str | None
    status: ExportJobStatus
    records_total: int | None
    records_processed: int
    records_skipped: int
    asset_errors: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    last_heartbeat_at: datetime | None
    masking_rules: list[MaskingRule] = Field(default_factory=list)
    sampling_config: SamplingConfig | None = None
    webhook_url: str | None = None
    webhook_enabled: bool = False


class ExportJobCreate(BaseModel):
    datasource_type: Literal["connection", "system"]
    datasource_ref: str
    datasource_filter: FilterSnapshot = Field(default_factory=FilterSnapshot)
    datasink_name: str
    destination_dataset: Annotated[str, Field(min_length=1, max_length=120)]
    asset_resolution: bool = False
    asset_url_fields: list[str] = Field(default_factory=list)
    asset_url_prefix: str = ""
    asset_datasink_name: str | None = None
    asset_dataset: str | None = None
    masking_rules: list[MaskingRule] = Field(default_factory=list)
    sampling_config: SamplingConfig | None = None
    webhook_url: str | None = None
    webhook_enabled: bool = False


class ExportJobResponse(BaseModel):
    id: UUID
    org_id: str
    user_id: str
    datasource_type: str
    datasource_ref: str
    datasource_filter: FilterSnapshot
    datasink_name: str
    destination_dataset: str
    asset_resolution: bool
    asset_url_fields: list[str]
    asset_url_prefix: str
    asset_datasink_name: str | None
    asset_dataset: str | None
    status: ExportJobStatus
    records_total: int | None
    records_processed: int
    records_skipped: int
    asset_errors: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    masking_rules: list[MaskingRule] = Field(default_factory=list)
    sampling_config: SamplingConfig | None = None
    webhook_url: str | None = None
    webhook_enabled: bool = False


class ExportJobListResponse(BaseModel):
    items: list[ExportJobResponse]
    total: int
    page: int
    page_size: int


class DatasinkInfo(BaseModel):
    name: str
    type: str


class DatasinkListResponse(BaseModel):
    datasinks: list[DatasinkInfo]


class DatasinkDatasetListResponse(BaseModel):
    datasets: list[str]


class AssetFieldDetectRequest(BaseModel):
    connection_id: UUID | None = None
    system_source_name: str | None = None


class AssetFieldDetectResponse(BaseModel):
    candidate_fields: list[str]


class AssetResolutionTestRequest(BaseModel):
    url_fields: list[str]
    url_prefix: str = ""


class AssetUrlTestResult(BaseModel):
    field: str
    raw_value: str
    resolved_url: str
    status_code: int | None = None
    ok: bool
    error: str | None = None


class AssetResolutionTestResponse(BaseModel):
    results: list[AssetUrlTestResult]
