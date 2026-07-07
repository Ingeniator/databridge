# Tagme sinks

`src/databridge/sinks/tagme.py` implements two real (non-mock) sinks against
the Tagme datasets/annotation API. Both authenticate via Keycloak token
exchange instead of forwarding the end user's browser session token, so a
long-running export job is never at risk of its credential expiring mid-run.

## Sinks

### `tagme-dataset` — `TagmeDatasetSink`

Mirrors `dataset-mock`, but against the real Tagme datasets API:

- `POST /api/v0/datasets` — create a dataset (`name`, `access`)
- `GET /api/v0/datasets?query=...` — look up an existing dataset by name before creating a duplicate
- `POST /api/v0/datasets/{id}/files` — upload one record/file at a time

### `tagme-annotator` — `TagmeAnnotatorSink`

Uploads export data into **one new task in an existing markup project that
the user picks** — it does not create or configure projects, pools, or task
settings:

- `GET /api/v0/markup_project` — list projects (used both for the picker and to resolve the destination by uid/name)
- `create_dataset(project_id_or_name)` fails with `RuntimeError` if the project doesn't already exist — no project is ever created here
- `POST /api/v0/tasks` — opens exactly one task per export job, sending only `{"project_id": ...}`; every other task option (`overlap`, `price`, `skip_strategy`, deadlines, ...) is left to Tagme's own defaults
- `post_file()` buffers records in memory (no per-record HTTP call)
- `finalise()` writes everything in a single `PUT /api/v0/tasks/{id}/payload` (`{"payload": {"entities": [...]}}`) and then `POST /api/v0/tasks/{id}/start`

**Known limitation:** Tagme's payload endpoint replaces the whole task
payload in one call — there's no per-record append — and its docs cap that
request at 15MB. Because this sink buffers the whole export in memory and
writes it once, very large exports can hit that ceiling. Fine for the
"simple, no-config" version this was scoped to; revisit if it needs to
handle bigger datasets (e.g. chunking across multiple tasks).

Both sinks share their Keycloak auth logic via the `_TagmeTokenExchangeAuth`
mixin in the same file.

## Auth: Keycloak token exchange

The worker never touches the end user's browser JWT. Instead, per export job:

1. The worker calls `sink.set_actor(org_id, user_id)` right after
   constructing the sink, using identity already persisted on the export
   job row (`export_jobs.org_id` / `export_jobs.user_id`) — not anything
   read from a live request.
2. On first authenticated call, `_auth_headers()`:
   - fetches the Databridge service account's own token via
     `grant_type=client_credentials` (`client_id`/`client_secret` from
     `DatasinkConfig`);
   - exchanges that token via
     `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, passing
     `subject_token=<service token>`, `requested_subject=<user_id>`, and
     (if configured) `audience=<audience>`;
   - sends `org_id` as an HTTP header on the exchange call (`org_header`,
     default `Organization-Id`) — mirroring the header Tagme's own
     password-grant login already uses to select org context.
3. The resulting token is cached on the sink instance and re-exchanged once
   `expires_in` (minus a 30s leeway) elapses — so a job that outlives a
   single token's lifetime just re-exchanges instead of failing.
4. Calling `set_actor()` again with a different `(org_id, user_id)` pair
   invalidates the cached token immediately, so a reused sink instance can
   never leak one actor's token to another.

This is Keycloak's **impersonation-style internal token exchange**
(`requested_subject`), not the external/actor-token variant of RFC 8693 —
it requires the Tagme-side Keycloak realm to grant Databridge's service
account permission to mint tokens for arbitrary users. That permission is
not enabled by default; see the next section.

## Configuring token exchange in Keycloak

These steps apply to the classic Keycloak "internal-to-internal" token
exchange (permission-based, works with `requested_subject`), which is what
this code uses. Keycloak's admin console layout for this has shifted across
major versions (feature flag name, whether it's still "preview"), so treat
step numbers as approximate and confirm against the console for your
deployed version.

1. **Enable the token-exchange feature**, if it isn't already:
   - Server startup flag: `--features=token-exchange` (or
     `KC_FEATURES=token-exchange` env var), **or**
   - Older versions: `-Dkeycloak.profile.feature.token_exchange=enabled`.

2. **Create (or reuse) Databridge's service account client** — the
   `client_id`/`client_secret` used in `DatasinkConfig`:
   - Client type: confidential (`Client authentication` = ON).
   - `Service accounts roles` = ON, so it can do `grant_type=client_credentials`
     to obtain its own service token (used as `subject_token` in the exchange).

3. **Enable fine-grained permissions on the target ("audience") client** —
   the client that actually issues tokens Tagme's API accepts (e.g. the
   `tagme` client referenced by `DatasinkConfig.audience`):
   - Open that client → **Advanced** tab → **Permissions** → switch
     `Permissions` to *Enabled*. This creates an authorization permission
     scoped to `token-exchange` on that client.

4. **Grant Databridge's service account the `token-exchange` permission**:
   - Under the audience client's **Authorization → Permissions**, open the
     generated `token-exchange` permission.
   - Attach (or create) a **client policy** that matches Databridge's
     service account client (the one from step 2), and set the permission's
     decision strategy so that policy grants access.
   - Save. Databridge's service account can now request
     `requested_subject=<any user id>` tokens for this audience.

5. **Point `DatasinkConfig` at the right endpoints**:
   - `token_url` — the realm's token endpoint,
     `.../realms/<realm>/protocol/openid-connect/token`.
   - `client_id` / `client_secret` — Databridge's service account client
     from step 2.
   - `audience` — the target client id from step 3 (omit if the exchanged
     token doesn't need a different audience than the issuing realm's
     default).
   - `org_header` — leave as `Organization-Id` unless Tagme's org-scoping
     header changes.

6. **Verify with a manual exchange** before wiring up a real export job:

   ```bash
   # 1. service token
   SVC_TOKEN=$(curl -s -d grant_type=client_credentials \
     -d client_id=databridge-service -d client_secret=$SECRET \
     "$TOKEN_URL" | jq -r .access_token)

   # 2. exchange for a specific user
   curl -s -d grant_type=urn:ietf:params:oauth:grant-type:token-exchange \
     -d client_id=databridge-service -d client_secret=$SECRET \
     -d subject_token="$SVC_TOKEN" \
     -d subject_token_type=urn:ietf:params:oauth:token-type:access_token \
     -d requested_subject=<some-user-id> \
     -d audience=tagme \
     -H "Organization-Id: <org-id>" \
     "$TOKEN_URL" | jq .
   ```

   A `403`/`not_authorized` response here means step 4's policy isn't wired
   up correctly — check the permission's attached policies before touching
   Databridge code again.

## `DatasinkConfig` fields used by these sinks

| field | meaning |
|---|---|
| `url` | Tagme API base URL |
| `token_url` | Keycloak token endpoint |
| `client_id` / `client_secret` | Databridge's service account client |
| `audience` | target client id requested for the exchanged token (optional) |
| `org_header` | header carrying `org_id` on the exchange call (default `Organization-Id`) |
| `dataset_access` | `access` value sent on dataset creation — `tagme-dataset` only (`public`/`organization`/`task`/`private`) |

See `config.yaml.example` for a filled-in example of both sink types.
