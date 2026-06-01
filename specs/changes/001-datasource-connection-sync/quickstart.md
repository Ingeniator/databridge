# Quickstart: Local Development

## Prerequisites

- Python 3.13 (`python3.13 --version`)
- `uv` package manager (`uv --version`)
- Docker + Docker Compose (`docker compose version`)
- PostgreSQL running (via docker-compose below)

---

## 1. Start dependencies

From the `ai-suite` root:

```bash
docker compose up -d postgres annotator-mock
```

| Service | Port |
|---------|------|
| postgres | 5432 |
| annotator-mock | 8010 |

---

## 2. Install Python dependencies

From `databridge/`:

```bash
uv sync
```

---

## 3. Configure the service

Copy the example config and edit:

```bash
cp config.yaml.example config.yaml
```

Minimum `config.yaml` for local dev (no Vault sidecar — use `$VAR` env-var expansion or plain values):

```yaml
server:
  host: "0.0.0.0"
  port: 5010
  debug: true
  silence_probes: false
  hide_auth_inputs: false

database_url: "postgresql://postgres:postgres@localhost:5432/databridge"
encryption_key: "${DATABRIDGE_ENCRYPTION_KEY}"

# Optional: system sources visible to all users
datasources: []
  # - name: "local-clickhouse"
  #   type: clickhouse
  #   url: "http://localhost:8123"
  #   database: "default"
  #   table: "llogr_events"
  #   user: "default"
  #   password: ""
```

Generate the Fernet encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export DATABRIDGE_ENCRYPTION_KEY=<paste key here>
```

Two env vars supported (optional overrides):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABRIDGE_CONFIG` | `config.yaml` | Path to config file |
| `VAULT_SECRETS_PATH` | `/vault/secrets/env` | Vault sidecar file path |

---

## 4. Run database migrations

```bash
uv run alembic upgrade head
```

---

## 5. Start the service

```bash
uv run uvicorn databridge.main:app --host 0.0.0.0 --port 5010 --reload
```

UI: http://localhost:5010
API docs: http://localhost:5010/docs

---

## 6. Run tests

```bash
# Unit + integration tests (requires postgres running)
uv run pytest tests/unit tests/integration -v

# E2E (requires service running on :5010)
uv run pytest tests/e2e -v
```

---

## 7. Create a test connection (curl)

```bash
# Add a ClickHouse source connection
curl -s -X POST http://localhost:5010/api/v1/connections \
  -H 'Content-Type: application/json' \
  -H 'X-Group-ID: test-user' \
  -d '{
    "label": "Local ClickHouse",
    "type": "clickhouse",
    "role": "source",
    "connection_url": "http://localhost:8123",
    "credentials": {"user": "default", "password": "", "database": "default"}
  }' | jq .

# Test without saving (pre-save ping)
curl -s -X POST http://localhost:5010/api/v1/connections/test \
  -H 'Content-Type: application/json' \
  -H 'X-Group-ID: test-user' \
  -d '{"type": "clickhouse", "connection_url": "http://localhost:8123",
       "credentials": {"user": "default", "password": ""}}' | jq .

# Ping a saved connection
curl -s -X POST http://localhost:5010/api/v1/connections/<id>/ping \
  -H 'X-Group-ID: test-user' | jq .

# Check readiness (per-component)
curl -s http://localhost:5010/ready | jq .
```
