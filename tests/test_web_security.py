"""Web 管理端与导入边界的安全回归测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import json

from src.config import settings
from src.db.repository import _validate_import_data
from src.web import app as web_app
from scripts.migrate_web_password import migrate, set_password


def _request(*, scheme: str, remote: str, forwarded: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        scheme=scheme,
        remote=remote,
        host="example.test",
        headers={"X-Forwarded-Proto": forwarded} if forwarded else {},
    )


def test_password_hash_verification_and_no_generated_default(mocker) -> None:
    encoded = web_app.hash_password_for_storage("correct horse battery staple")
    assert web_app._verify_password_hash("correct horse battery staple", encoded)
    assert not web_app._verify_password_hash("wrong password", encoded)

    mocker.patch.object(settings, "web_password_hash", "")
    mocker.patch.object(settings, "web_password", "")
    mocker.patch.object(settings, "web_allow_legacy_plaintext_password", False)
    web_app._state.web_password_initialized = False
    assert not web_app._web_password_is_configured()

    mocker.patch.object(settings, "web_password", "legacy-secret")
    web_app._state.web_password_initialized = False
    assert not web_app._web_password_is_configured()


def test_password_migration_removes_plaintext(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("STEAM_IDS=76561198000000000\nWEB_PASSWORD=legacy-secret\n", encoding="utf-8")
    migrate(env_file)
    migrated = env_file.read_text(encoding="utf-8")
    assert "WEB_PASSWORD=legacy-secret" not in migrated
    assert "WEB_PASSWORD=\n" in migrated
    assert "WEB_PASSWORD_HASH=scrypt$" in migrated

    set_password(env_file, "replacement-secret")
    replacement_hash = next(line for line in env_file.read_text(encoding="utf-8").splitlines() if line.startswith("WEB_PASSWORD_HASH="))
    assert web_app._verify_password_hash("replacement-secret", replacement_hash.split("=", 1)[1])

def test_token_is_server_side_revocable(mocker) -> None:
    mocker.patch.object(settings, "web_password_hash", web_app.hash_password_for_storage("test-password"))
    mocker.patch.object(settings, "web_password", "")
    web_app._state.web_password_initialized = False
    web_app._state.active_sessions.clear()

    token = web_app._make_token()
    assert web_app._verify_token(token)
    web_app._revoke_token(token)
    assert not web_app._verify_token(token)


def test_remote_http_is_rejected_unless_https_or_loopback_override(mocker) -> None:
    mocker.patch.object(settings, "web_trust_proxy_headers", False)
    mocker.patch.object(settings, "web_allow_insecure_http", False)
    remote_http = _request(scheme="http", remote="203.0.113.5")
    assert not web_app._is_secure_transport(remote_http)
    assert not web_app._is_insecure_http_allowed(remote_http)

    mocker.patch.object(settings, "web_allow_insecure_http", True)
    assert not web_app._is_insecure_http_allowed(remote_http)
    assert web_app._is_insecure_http_allowed(_request(scheme="http", remote="127.0.0.1"))
    docker_bridge = _request(scheme="http", remote="172.17.0.1")
    docker_bridge.host = "127.0.0.1:8080"
    assert web_app._is_insecure_http_allowed(docker_bridge)
    assert web_app._is_secure_transport(_request(scheme="https", remote="203.0.113.5"))


def test_import_validation_rejects_xss_id_and_excessive_records(mocker) -> None:
    valid = {
        "app": "steam-cs2-inventory-monitor",
        "version": "1.0",
        "users": [{
            "steam_id": "76561198000000000",
            "nickname": "safe",
            "current_inventory": {"items": {}},
            "recent_changes": [],
            "archives": [],
        }],
    }
    assert _validate_import_data(valid) == valid["users"]

    invalid_id = {**valid, "users": [{**valid["users"][0], "steam_id": "x\"><script>alert(1)</script>"}]}
    try:
        _validate_import_data(invalid_id)
    except ValueError as exc:
        assert "steam_id" in str(exc)
    else:
        raise AssertionError("malicious Steam ID must be rejected")

    mocker.patch.object(settings, "web_max_import_users", 1)
    too_many = {**valid, "users": valid["users"] * 2}
    try:
        _validate_import_data(too_many)
    except ValueError as exc:
        assert "too many users" in str(exc)
    else:
        raise AssertionError("oversized import must be rejected")


def test_frontend_keeps_session_token_out_of_local_storage() -> None:
    content = (Path(__file__).parents[1] / "src" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert "localStorage.getItem('auth_token')" not in content
    assert "localStorage.setItem('auth_token'" not in content
    assert content.count("new WebSocket(`${proto}//${location.host}/api/ws`)") == 1
    assert "asset_id: ${esc(item.asset_id)}" in content


def test_inventory_images_use_bounded_viewport_aware_preloading() -> None:
    content = (Path(__file__).parents[1] / "src" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert '<link rel="preconnect" href="https://steamcommunity-a.akamaihd.net">' in content
    assert "const INVENTORY_IMAGE_CONCURRENCY = 6;" in content
    assert "const INVENTORY_EAGER_IMAGE_COUNT = 12;" in content
    assert "window.matchMedia('(max-width: 768px)').matches ? 4" in content
    assert "root: document.getElementById('content'), rootMargin: '720px 0px'" in content
    assert "img.setAttribute('fetchpriority', 'high');" in content
    assert "inventoryImageGeneration += 1;" in content
    assert "setTimeout(renderFilteredItems, 180)" in content
    assert 'data-src="${esc(imgUrl(item.icon_url))}"' in content
    assert 'data-src="${esc(imgUrl(representative.icon_url))}"' in content


async def test_login_returns_only_httponly_cookie_and_logout_revokes_it(mocker) -> None:
    from src.db import repository

    encoded = web_app.hash_password_for_storage("test-password")
    mocker.patch.object(settings, "web_password_hash", encoded)
    mocker.patch.object(settings, "web_password", "")
    web_app._state.web_password_initialized = False
    web_app._state.active_sessions.clear()
    mocker.patch.object(repository, "get_login_rate_limit", mocker.AsyncMock(return_value=(0, 0.0)))
    mocker.patch.object(repository, "clear_login_rate_limit", mocker.AsyncMock())

    class LoginRequest:
        scheme = "https"
        remote = "203.0.113.5"
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}

        async def json(self):
            return {"password": "test-password"}

    response = await web_app.api_login(LoginRequest())
    payload = json.loads(response.text)
    assert payload == {"ok": True}
    token = response.cookies["auth_token"].value
    assert web_app._verify_token(token)
    assert response.cookies["auth_token"]["httponly"]
    assert response.cookies["auth_token"]["secure"]

    logout_response = await web_app.api_logout(SimpleNamespace(cookies={"auth_token": token}))
    assert json.loads(logout_response.text) == {"ok": True}
    assert not web_app._verify_token(token)
