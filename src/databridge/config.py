from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_DNS

import yaml


# ── Config dataclasses ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 5010
    workers: int = 1
    root_path: str = ""
    debug: bool = False
    silence_probes: bool = True
    hide_auth_inputs: bool = False
    public_url: str = ""  # e.g. https://databridge.example.com — used for {{download_url}} in webhook templates


_SYSTEM_SOURCE_VALID_KEYS = {
    "name", "type", "url", "bucket", "region", "access_key_id", "secret_access_key",
    "key_prefix", "addressing_style", "presign_expiry", "database", "table",
    "user", "password", "catalog", "schema_name", "endpoint", "public_endpoint",
    "duckdb_temp_dir", "search_column", "timestamp_column",
}

@dataclass(frozen=True)
class SystemSourceConfig:
    name: str
    type: str
    url: str = ""
    endpoint: str = ""
    public_endpoint: str = ""
    bucket: str = ""
    region: str = "us-east-1"
    access_key_id: str = ""
    secret_access_key: str = ""
    key_prefix: str = ""
    addressing_style: str = "virtual"
    presign_expiry: int = 3600
    database: str = "default"
    table: str = "llogr_events"
    user: str = ""
    password: str = ""
    catalog: str = ""
    schema_name: str = ""
    search_column: str = "message"
    timestamp_column: str = "timestamp"
    duckdb_temp_dir: str = "/tmp/duckdb_temp"

    @property
    def id(self) -> UUID:
        """Deterministic UUID v5 of the source name. Rename = new ID."""
        return uuid5(NAMESPACE_DNS, self.name)


_DATASINK_VALID_KEYS = {
    "name", "type", "url", "path", "filename_template",
    "bucket", "region", "access_key_id", "secret_access_key", "endpoint", "key_prefix",
}
_DATASINK_SERVICE_TYPES = {"dataset-mock", "annotator-mock"}
_DATASINK_LOCAL_TYPES = {"local-zip", "local-jsonl"}
_DATASINK_S3_TYPES = {"s3-jsonl", "s3-zip"}
_DATASINK_ALL_TYPES = _DATASINK_SERVICE_TYPES | _DATASINK_LOCAL_TYPES | _DATASINK_S3_TYPES


@dataclass(frozen=True)
class DatasinkConfig:
    name: str
    type: str
    url: str = ""
    path: str = ""
    filename_template: str = ""
    bucket: str = ""
    region: str = "us-east-1"
    access_key_id: str = ""
    secret_access_key: str = ""
    endpoint: str = ""
    key_prefix: str = ""


@dataclass(frozen=True)
class ExportSettings:
    stale_job_timeout_minutes: int = 15
    max_concurrent_jobs_per_org: int = 5
    job_ttl_days: int = 7
    poll_interval_seconds: int = 3
    keepalive_interval_minutes: int = 2
    batch_size: int = 100
    redis_url: str = "redis://localhost:6379"
    worker_metrics_port: int = 9101
    webhook_allowed_url_prefixes: tuple[str, ...] = field(default_factory=tuple)


_SETTINGS_VALID_KEYS = {"server", "database_url", "db_pool_max_size", "encryption_key", "datasources", "datasinks", "export"}
_SERVER_VALID_KEYS = {
    "host", "port", "workers", "root_path", "debug", "silence_probes", "hide_auth_inputs", "public_url",
}
_EXPORT_VALID_KEYS = {
    "stale_job_timeout_minutes", "max_concurrent_jobs_per_org", "job_ttl_days",
    "poll_interval_seconds", "keepalive_interval_minutes", "batch_size", "redis_url",
    "worker_metrics_port", "webhook_allowed_url_prefixes",
}

@dataclass(frozen=True)
class Settings:
    server: ServerConfig
    database_url: str
    encryption_key: str
    datasources: tuple[SystemSourceConfig, ...]
    datasinks: tuple[DatasinkConfig, ...] = field(default_factory=tuple)
    export: ExportSettings = field(default_factory=ExportSettings)
    db_pool_max_size: int = 10


# ── Secret injection ─────────────────────────────────────────────────────────

def _load_vault_secrets(vault_path: str) -> dict[str, str]:
    p = Path(vault_path)
    if not p.exists():
        return {}
    secrets: dict[str, str] = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for prefix in ("export ", ""):
            if line.startswith(prefix):
                line = line[len(prefix):]
                break
        if "=" in line:
            k, _, v = line.partition("=")
            secrets[k.strip()] = v.strip()
        elif ": " in line:
            k, _, v = line.partition(": ")
            secrets[k.strip()] = v.strip()
    return secrets


def _resolve_secrets(value: Any, vault: dict[str, str]) -> Any:
    if isinstance(value, str):
        if value.startswith("vault:"):
            key = value[6:]
            if key not in vault:
                raise ValueError(
                    f"Unresolvable vault reference: vault:{key!r}. "
                    f"Key not found in vault sidecar. Available keys: {sorted(vault)}"
                )
            return vault[key]
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _resolve_secrets(v, vault) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_secrets(v, vault) for v in value]
    return value


# ── Config file resolution ────────────────────────────────────────────────────

def _find_config() -> Path:
    if env := os.environ.get("DATABRIDGE_CONFIG"):
        p = Path(env)
        if not p.exists():
            print(
                f"ERROR: DATABRIDGE_CONFIG={env!r} does not exist.",
                file=sys.stderr,
            )
            sys.exit(1)
        return p
    # two dirs above config.py (local dev: project root when running from src/)
    src_relative = Path(__file__).resolve().parents[2] / "config.yaml"
    if src_relative.exists():
        return src_relative
    # cwd default (production)
    cwd_cfg = Path("config.yaml")
    if cwd_cfg.exists():
        return cwd_cfg
    raise FileNotFoundError(
        f"config.yaml not found. Searched: {src_relative}, {cwd_cfg.resolve()}. "
        "Set DATABRIDGE_CONFIG env var to the config file path."
    )


# ── Strict validation ─────────────────────────────────────────────────────────

def _validate_keys(data: dict, valid: set[str], section: str) -> None:
    unknown = set(data) - valid
    if unknown:
        raise ValueError(
            f"Unknown key(s) in {section}: {sorted(unknown)}. "
            f"Valid keys: {sorted(valid)}"
        )


# ── Main loader ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    config_path = _find_config()
    vault_path = os.environ.get("VAULT_SECRETS_PATH", "/vault/secrets/env")
    vault = _load_vault_secrets(vault_path)

    raw = yaml.safe_load(config_path.read_text()) or {}
    raw = _resolve_secrets(raw, vault)

    _validate_keys(raw, _SETTINGS_VALID_KEYS, "top-level config")

    # server
    server_raw = raw.get("server", {})
    _validate_keys(server_raw, _SERVER_VALID_KEYS, "server")
    server = ServerConfig(**server_raw)

    # datasources
    datasource_list = raw.get("datasources") or []
    sources: list[SystemSourceConfig] = []
    for ds in datasource_list:
        _validate_keys(ds, _SYSTEM_SOURCE_VALID_KEYS, f"datasource '{ds.get('name', '?')}'")
        sources.append(SystemSourceConfig(**ds))

    # datasinks
    datasink_list = raw.get("datasinks") or []
    sinks: list[DatasinkConfig] = []
    for sk in datasink_list:
        _validate_keys(sk, _DATASINK_VALID_KEYS, f"datasink '{sk.get('name', '?')}'")
        sk_name = sk.get("name", "?")
        sk_type = sk.get("type", "")
        if sk_type not in _DATASINK_ALL_TYPES:
            raise ValueError(
                f"datasink '{sk_name}': unknown type {sk_type!r}. "
                f"Valid types: {sorted(_DATASINK_ALL_TYPES)}"
            )
        if sk_type in _DATASINK_SERVICE_TYPES and not sk.get("url"):
            raise ValueError(f"datasink '{sk_name}': 'url' is required for type {sk_type!r}")
        if sk_type in _DATASINK_LOCAL_TYPES and not sk.get("path"):
            raise ValueError(f"datasink '{sk_name}': 'path' is required for type {sk_type!r}")
        if sk_type in _DATASINK_S3_TYPES and not sk.get("bucket"):
            raise ValueError(f"datasink '{sk_name}': 'bucket' is required for type {sk_type!r}")
        sinks.append(DatasinkConfig(**sk))

    # export settings
    export_raw = raw.get("export") or {}
    _validate_keys(export_raw, _EXPORT_VALID_KEYS, "export")
    if "webhook_allowed_url_prefixes" in export_raw:
        export_raw = dict(export_raw)
        export_raw["webhook_allowed_url_prefixes"] = tuple(export_raw["webhook_allowed_url_prefixes"])
    export_settings = ExportSettings(**export_raw)

    return Settings(
        server=server,
        database_url=raw.get("database_url", ""),
        encryption_key=raw.get("encryption_key", ""),
        datasources=tuple(sources),
        datasinks=tuple(sinks),
        export=export_settings,
        db_pool_max_size=raw.get("db_pool_max_size", 10),
    )
