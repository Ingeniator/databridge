# Open-Source Analogs for Databridge

Databridge is a connection management + data browsing + export pipeline service for S3, ClickHouse, Trino, and similar sources, with an embedded SPA UI.

## Closest overall match

- **[Airbyte](https://github.com/airbytehq/airbyte)** — connector catalog, credential management, data sync pipelines with a UI. Heavier (JVM-based), but very similar concept.

## Per-feature breakdown

| Databridge feature | OSS equivalent |
|---|---|
| Connection management + credential encryption | [Airbyte](https://github.com/airbytehq/airbyte) or [Meltano](https://github.com/meltano/meltano) |
| Browse/preview S3, ClickHouse, Trino | [Metabase](https://github.com/metabase/metabase), [Apache Superset](https://github.com/apache/superset) |
| Data export jobs with Redis worker | [Prefect](https://github.com/PrefectHQ/prefect), [Dagster](https://github.com/dagster-io/dagster) |
| Langfuse traces browsing | Langfuse itself has a built-in UI |
| Dataset sink uploads | [DVC](https://github.com/iterative/dvc) for versioned datasets |

## Lightweight alternatives

Closer to databridge's footprint (single Python service + SPA):

- **[Datasette](https://github.com/simonw/datasette)** — browse and query data sources via a lightweight Python server
- **[Steampipe](https://github.com/turbot/steampipe)** — query cloud APIs/DBs via SQL with a connection registry

## Summary

Databridge is essentially a slimmed-down Airbyte focused on AI pipeline data sources (ClickHouse, Trino, S3, Langfuse). Nothing in OSS matches the exact combination, which is likely why it exists as a custom service.
