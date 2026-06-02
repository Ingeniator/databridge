"""Failing unit test stubs — AuthContext role mapping and header parsing."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException


def _make_request(group_id=None, x_role=None, authorization=None, path="/test"):
    req = MagicMock()
    req.url.path = path
    headers = {}
    if group_id is not None:
        headers["X-Group-ID"] = group_id
    if x_role is not None:
        headers["X-Role"] = x_role
    if authorization is not None:
        headers["Authorization"] = authorization
    req.headers = headers
    return req


@pytest.mark.asyncio
async def test_group_id_split_org_user():
    from databridge.auth import get_auth
    with patch("databridge.auth.get_settings") as mock_settings:
        mock_settings.return_value.server.debug = False
        result = await get_auth(_make_request(group_id="acme/alice"))
    assert result.org_id == "acme"
    assert result.user_id == "alice"
    assert result.public_key == "acme/alice"


@pytest.mark.asyncio
async def test_super_admin_role():
    from databridge.auth import get_auth
    with patch("databridge.auth.get_settings") as mock_settings:
        mock_settings.return_value.server.debug = False
        result = await get_auth(_make_request(group_id="acme/alice", x_role="SUPER_ADMIN"))
    assert result.role == "super_admin"
    assert result.is_org_admin is True


@pytest.mark.asyncio
async def test_org_admin_role():
    from databridge.auth import get_auth
    with patch("databridge.auth.get_settings") as mock_settings:
        mock_settings.return_value.server.debug = False
        result = await get_auth(_make_request(group_id="acme/alice", x_role="ORG_ADMIN"))
    assert result.role == "org_admin"
    assert result.is_org_admin is True


@pytest.mark.asyncio
async def test_user_role_absent_x_role():
    from databridge.auth import get_auth
    with patch("databridge.auth.get_settings") as mock_settings:
        mock_settings.return_value.server.debug = False
        result = await get_auth(_make_request(group_id="acme/alice"))
    assert result.role == "user"
    assert result.is_org_admin is False


@pytest.mark.asyncio
async def test_backwards_compat_public_key():
    """Legacy field public_key equals full raw X-Group-ID value."""
    from databridge.auth import get_auth
    with patch("databridge.auth.get_settings") as mock_settings:
        mock_settings.return_value.server.debug = False
        result = await get_auth(_make_request(group_id="acme/alice"))
    assert result.public_key == "acme/alice"


@pytest.mark.asyncio
async def test_debug_fallback():
    from databridge.auth import get_auth
    with patch("databridge.auth.get_settings") as mock_settings:
        mock_settings.return_value.server.debug = True
        result = await get_auth(_make_request())
    assert result.org_id == "dev"
    assert result.user_id == "dev"
    assert result.role == "super_admin"
