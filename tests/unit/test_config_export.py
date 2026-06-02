"""Failing unit test stubs — config DatasinkConfig and ExportSettings."""
import pytest
from databridge.config import DatasinkConfig, ExportSettings, _DATASINK_ALL_TYPES, get_settings
from databridge.config import _DATASINK_SERVICE_TYPES, _DATASINK_LOCAL_TYPES


def test_datasink_config_dataset_mock_parses():
    cfg = DatasinkConfig(name="mock", type="dataset-mock", url="http://mock:8020")
    assert cfg.name == "mock"
    assert cfg.type == "dataset-mock"
    assert cfg.url == "http://mock:8020"


def test_datasink_config_annotator_mock_parses():
    cfg = DatasinkConfig(name="ann", type="annotator-mock", url="http://ann:8010")
    assert cfg.type == "annotator-mock"


def test_datasink_config_local_zip_parses():
    cfg = DatasinkConfig(name="zp", type="local-zip", path="/exports", filename_template="{id}.json")
    assert cfg.path == "/exports"
    assert cfg.filename_template == "{id}.json"


def test_datasink_config_local_jsonl_parses():
    cfg = DatasinkConfig(name="jl", type="local-jsonl", path="/exports")
    assert cfg.path == "/exports"
    assert cfg.filename_template == ""


def test_export_settings_defaults():
    s = ExportSettings()
    assert s.stale_job_timeout_minutes == 15
    assert s.max_concurrent_jobs_per_org == 5
    assert s.job_ttl_days == 7
    assert s.poll_interval_seconds == 3
    assert s.keepalive_interval_minutes == 2
    assert s.batch_size == 100
    assert s.redis_url == "redis://localhost:6379"


def test_datasink_config_unknown_key_raises(tmp_path):
    import yaml
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({
        "server": {"debug": True},
        "database_url": "postgresql://x",
        "encryption_key": "k",
        "datasinks": [{"name": "bad", "type": "dataset-mock", "url": "http://x", "unknown_key": "val"}],
    }))
    import os
    old = os.environ.get("DATABRIDGE_CONFIG")
    os.environ["DATABRIDGE_CONFIG"] = str(cfg_file)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="Unknown key"):
            get_settings()
    finally:
        if old is None:
            del os.environ["DATABRIDGE_CONFIG"]
        else:
            os.environ["DATABRIDGE_CONFIG"] = old
        get_settings.cache_clear()


def test_datasink_config_missing_url_for_service_raises(tmp_path):
    import yaml
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({
        "server": {"debug": True},
        "database_url": "postgresql://x",
        "encryption_key": "k",
        "datasinks": [{"name": "bad", "type": "dataset-mock"}],
    }))
    import os
    old = os.environ.get("DATABRIDGE_CONFIG")
    os.environ["DATABRIDGE_CONFIG"] = str(cfg_file)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="url.*required"):
            get_settings()
    finally:
        if old is None:
            del os.environ["DATABRIDGE_CONFIG"]
        else:
            os.environ["DATABRIDGE_CONFIG"] = old
        get_settings.cache_clear()


def test_datasink_config_missing_path_for_local_raises(tmp_path):
    import yaml
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({
        "server": {"debug": True},
        "database_url": "postgresql://x",
        "encryption_key": "k",
        "datasinks": [{"name": "bad", "type": "local-jsonl"}],
    }))
    import os
    old = os.environ.get("DATABRIDGE_CONFIG")
    os.environ["DATABRIDGE_CONFIG"] = str(cfg_file)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="path.*required"):
            get_settings()
    finally:
        if old is None:
            del os.environ["DATABRIDGE_CONFIG"]
        else:
            os.environ["DATABRIDGE_CONFIG"] = old
        get_settings.cache_clear()
