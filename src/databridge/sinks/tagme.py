from __future__ import annotations

import json
import re
import time

import httpx

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink

_TIMEOUT = 30.0
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
_TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
_ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
_EXPIRY_LEEWAY_SECONDS = 30
_LIST_PAGE_SIZE = 100


class _TagmeTokenExchangeAuth:
    """Keycloak token-exchange auth shared by all Tagme-backed sinks.

    The worker's own confidential client (client_id/client_secret) fetches
    its service token, then exchanges it for one asserting
    `requested_subject=user_id`, scoped to `org_id` via the configured org
    header. The exchanged token is what carries trust to Tagme — the export
    job never needs the user's own browser-session token, so nothing here
    can expire out from under a long-running export.

    Mixed into BaseSink subclasses; expects `self._config` (set by
    BaseSink.__init__) to carry `token_url`/`client_id`/`client_secret`.
    Requires the Tagme-side Keycloak client to have token-exchange
    permission granted to Databridge's service account.
    """

    def __init__(self) -> None:
        self._org_id: str = ""
        self._user_id: str = ""
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def set_actor(self, org_id: str, user_id: str) -> None:
        if (org_id, user_id) != (self._org_id, self._user_id):
            self._token = None
            self._token_expires_at = 0.0
        self._org_id = org_id
        self._user_id = user_id

    async def _service_token(self, client: httpx.AsyncClient) -> str:
        r = await client.post(
            self._config.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]

    async def _exchange_token(self, client: httpx.AsyncClient) -> dict:
        if not self._user_id:
            raise RuntimeError(f"{type(self).__name__}.set_actor() must be called before use")
        service_token = await self._service_token(client)
        data = {
            "grant_type": _TOKEN_EXCHANGE_GRANT,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "subject_token": service_token,
            "subject_token_type": _ACCESS_TOKEN_TYPE,
            "requested_subject": self._user_id,
        }
        if self._config.audience:
            data["audience"] = self._config.audience
        headers = {}
        if self._config.org_header and self._org_id:
            headers[self._config.org_header] = self._org_id
        r = await client.post(self._config.token_url, data=data, headers=headers)
        r.raise_for_status()
        return r.json()

    async def _auth_headers(self) -> dict:
        if self._token is None or time.monotonic() >= self._token_expires_at:
            async with httpx.AsyncClient(timeout=10.0) as client:
                payload = await self._exchange_token(client)
            self._token = payload["access_token"]
            expires_in = payload.get("expires_in", 60)
            self._token_expires_at = time.monotonic() + max(expires_in - _EXPIRY_LEEWAY_SECONDS, 0)
        return {"Authorization": f"Bearer {self._token}"}


class TagmeDatasetSink(_TagmeTokenExchangeAuth, BaseSink):
    """Real Tagme datasets API (/api/v0/datasets, matches the Tagme OpenAPI spec)."""

    def __init__(self, config: DatasinkConfig) -> None:
        BaseSink.__init__(self, config)
        _TagmeTokenExchangeAuth.__init__(self)
        self._url = config.url.rstrip("/")
        self._dataset_ids: dict[str, str] = {}  # name → uuid

    async def ping(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self._url}/health")
            r.raise_for_status()

    async def list_datasets(self, query: str = "") -> list[dict[str, str]]:
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{self._url}/api/v0/datasets",
                params={"query": query, "page": 1, "size": _LIST_PAGE_SIZE},
                headers=headers,
            )
            r.raise_for_status()
            return [
                {"name": d["name"], "uid": d["id"]}
                for d in r.json().get("items", [])
                if d.get("id")
            ]

    async def create_dataset(self, name_or_id: str) -> None:
        if _UUID_RE.match(name_or_id):
            self._dataset_ids[name_or_id] = name_or_id
            self.external_id = name_or_id
            return

        existing = await self.list_datasets(query=name_or_id)
        match = next((d for d in existing if d["name"] == name_or_id), None)
        if match:
            self._dataset_ids[name_or_id] = match["uid"]
            self.external_id = match["uid"]
            return

        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{self._url}/api/v0/datasets",
                json={"name": name_or_id, "access": self._config.dataset_access},
                headers=headers,
            )
            r.raise_for_status()
            dataset_id = r.json()["id"]
        self._dataset_ids[name_or_id] = dataset_id
        self.external_id = dataset_id

    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> str:
        if dataset not in self._dataset_ids:
            await self.create_dataset(dataset)
        dataset_id = self._dataset_ids[dataset]
        headers = await self._auth_headers()
        fname = filename or "record.json"
        content = json.dumps(record).encode()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{self._url}/api/v0/datasets/{dataset_id}/files",
                files={"file": (fname, content, "application/json")},
                headers=headers,
            )
            r.raise_for_status()
        return record.get("source_url", "")

    async def finalise(self) -> None:
        pass


class TagmeAnnotatorSink(_TagmeTokenExchangeAuth, BaseSink):
    """Uploads export data into one new task in an existing Tagme markup
    project that the user picks — no project, pool, or task-config creation.

    The project must already exist in Tagme (`create_dataset` looks it up by
    uid or exact name and fails if it isn't found). One task per export job
    is created with only `project_id` set; every other task option
    (overlap, pricing, skip strategy, ...) is left to Tagme's own defaults.

    Records are buffered in memory and written in a single
    `PUT .../tasks/{id}/payload` call at `finalise()`, since Tagme's payload
    endpoint replaces the whole task payload rather than appending — this
    also means the export is bounded by Tagme's documented 15MB payload cap.
    """

    def __init__(self, config: DatasinkConfig) -> None:
        BaseSink.__init__(self, config)
        _TagmeTokenExchangeAuth.__init__(self)
        self._url = config.url.rstrip("/")
        self._project_id: str | None = None
        self._task_id: str | None = None
        self._records: list[dict] = []

    async def ping(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self._url}/health")
            r.raise_for_status()

    async def list_datasets(self) -> list[dict[str, str]]:
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{self._url}/api/v0/markup_project", headers=headers)
            r.raise_for_status()
            return [
                {"name": p["name"], "uid": p["uid"]}
                for p in r.json().get("items", [])
                if p.get("name") and p.get("uid")
            ]

    async def create_dataset(self, project_id_or_name: str) -> None:
        projects = await self.list_datasets()
        if _UUID_RE.match(project_id_or_name):
            project = next((p for p in projects if p["uid"] == project_id_or_name), None)
        else:
            project = next((p for p in projects if p["name"] == project_id_or_name), None)
        if project is None:
            raise RuntimeError(f"markup project {project_id_or_name!r} not found")
        self._project_id = project["uid"]

        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{self._url}/api/v0/tasks",
                json={"project_id": self._project_id},
                headers=headers,
            )
            r.raise_for_status()
            self._task_id = r.json()["uid"]
        self.external_id = self._task_id

    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> str:
        if self._task_id is None:
            raise RuntimeError("create_dataset must be called before post_file")
        self._records.append(record)
        return record.get("source_url", "")

    async def finalise(self) -> None:
        if self._task_id is None:
            return
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.put(
                f"{self._url}/api/v0/tasks/{self._task_id}/payload",
                json={"payload": {"entities": self._records}},
                headers=headers,
            )
            r.raise_for_status()
            r = await client.post(f"{self._url}/api/v0/tasks/{self._task_id}/start", headers=headers)
            r.raise_for_status()
