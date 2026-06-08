# Quickstart: Browser UI Redesign

**Branch**: `002-browser-ui-redesign` | **Date**: 2026-06-03

---

## Prerequisites

Same as `002-datasink-export/quickstart.md`. You need:
- Docker Compose up (`docker-compose.dev.yml`)
- PostgreSQL + Redis running
- `.env` or `config.dev.yaml` configured
- `uv` installed

---

## Running the service with the new UI

```bash
# Ensure migrations are current (new 0003 migration for masking/sampling/webhook columns)
uv run alembic upgrade head

# Start the web service
uv run uvicorn databridge.main:app --reload --port 8000

# In a second terminal: start the ARQ worker
uv run python -m worker
```

Browse to `http://localhost:8000/`. You should see the new tabbed layout.

---

## Verifying new features

### 1. Connection tab bar

- Add two or more connections via the "+ Add Connection" tab (opens the same modal as before)
- Click each tab — the Refine Dataset card and Data Preview should update
- Check health badge cycles through: SYNCING → HEALTHY STATUS

### 2. Schema Discovery

- Select a ClickHouse or S3 connection
- Schema auto-detects; the section expands showing up to 3 field chips
- Click `▼` to collapse, `▲` to expand
- Click `↺` to re-run detection

### 3. Predicate Filter

- Type `status == 'error'` in the Predicate Filter input
- Click the `≡` (tune) icon to open the structured builder
- Add a rule: `status` / `==` / `error`, click `+ Add`
- The input field updates to reflect the structured rule
- Click Data Preview's load button — rows should filter

### 4. Data Masking

- Enable the Masking toggle
- Click PII AUTO-DETECTION ENABLED — masking table pre-fills with candidate PII fields
- Add a manual rule: click `+ ADD ROW`, pick `attributes.ip_address` + `Hash`
- Rules are shown in the FIELD ID / ACTION table

### 5. Sampling Strategy

- Enable the Sampling toggle
- Select "Stratified Sampling" from the METHOD dropdown
- Enter `region_id` as TARGET COLUMN and `0.10` as RATIO/SIZE
- The description updates: "Maintains population subgroup proportions…"

### 6. Export with all options

- Select a datasink from the "Select Sink" dropdown
- Click the large blue **Export** button
- Navigate to the Jobs tab — the new job appears with status PENDING → RUNNING → COMPLETED
- For local-zip/local-jsonl sinks: a Download link appears when complete

### 7. Webhook Configuration

- Enter `https://webhook.site/your-id` in the WEBHOOK URL field
- Toggle "RUN ON COMPLETION" on
- Click **Test Webhook** — a 200 OK should appear in webhook.site
- Run an export — after completion, the webhook fires automatically

---

## Running tests

```bash
# Unit tests (masking/sampling logic)
uv run pytest tests/unit/test_masking.py tests/unit/test_sampling.py -v

# Integration tests (export job with masking/sampling fields)
uv run pytest tests/integration/test_export_jobs.py -v

# E2E Playwright tests (full browser flow)
uv run pytest tests/e2e/test_browser_redesign.py --headed -v
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Schema discovery section never appears | Check `GET /api/v1/connections/{id}/schema` returns 200 |
| Total rows always shows 0 | Adapter's `count()` method may not support the `time_field` param yet |
| PII auto-detect returns empty | Check that `GET /api/v1/connections/{id}/pii-fields` is registered in `main.py` |
| Masking rules not applied in export | Verify migration 0003 ran (`alembic current`) and worker was restarted |
| Webhook not firing | Check `webhook_enabled=true` in the job record via `GET /api/v1/export-jobs/{id}` |
