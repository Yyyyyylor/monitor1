"""代理配置测试 — TLS 校验仅对本地回环代理关闭（回归 M1）。"""

from __future__ import annotations

from src.crawler.proxy import _is_loopback_host, _need_verify, _redact_url, resolve_proxy_config


class TestRedactUrl:
    def test_strips_credentials(self) -> None:
        assert _redact_url("http://user:pass@127.0.0.1:8080") == "http://127.0.0.1:8080"
        assert _redact_url("http://user:pass@proxy.example.com:8080") == "http://proxy.example.com:8080"

    def test_plain_url_unchanged(self) -> None:
        assert _redact_url("http://127.0.0.1:7890") == "http://127.0.0.1:7890"
        assert _redact_url("127.0.0.1:7890") == "127.0.0.1:7890"

    def test_invalid_url_not_crash(self) -> None:
        assert _redact_url(":::") == ":::"


class TestIsLoopbackHost:
    def test_local_hosts(self) -> None:
        assert _is_loopback_host("127.0.0.1") is True
        assert _is_loopback_host("localhost") is True
        assert _is_loopback_host("::1") is True
        assert _is_loopback_host("0.0.0.0") is True
        assert _is_loopback_host("127.0.0.1:443") is True
        assert _is_loopback_host("localhost:27015") is True

    def test_remote_hosts(self) -> None:
        assert _is_loopback_host("steamcommunity.com") is False
        assert _is_loopback_host("192.168.1.5") is False
        assert _is_loopback_host("10.0.0.2:443") is False
        assert _is_loopback_host("") is False
        assert _is_loopback_host(None) is False


class TestNeedVerify:
    def test_local_proxy_disables_verify(self) -> None:
        assert _need_verify("http://127.0.0.1:7890", None) is False
        assert _need_verify("socks5://127.0.0.1:1080", None) is False
        assert _need_verify("http://user:pass@127.0.0.1:8080", None) is False
        assert _need_verify(None, "127.0.0.1:443") is False

    def test_remote_proxy_keeps_verify(self) -> None:
        assert _need_verify("http://proxy.example.com:8080", None) is True
        assert _need_verify("http://user:pass@proxy.example.com:8080", None) is True
        assert _need_verify(None, "10.0.0.2:443") is True
        assert _need_verify(None, None) is True


class TestResolveProxyConfig:
    def test_manual_local(self) -> None:
        cfg = resolve_proxy_config("manual", "http://127.0.0.1:27015", "")
        assert cfg["proxy"] == "http://127.0.0.1:27015"
        assert cfg["verify"] is False

    def test_manual_remote(self) -> None:
        cfg = resolve_proxy_config("manual", "http://proxy.example.com:8080", "")
        assert cfg["verify"] is True

    def test_hosts_local(self) -> None:
        cfg = resolve_proxy_config("hosts", "", "127.0.0.1:443")
        assert cfg["hosts_override"] == "127.0.0.1:443"
        assert cfg["verify"] is False

    def test_hosts_remote(self) -> None:
        cfg = resolve_proxy_config("hosts", "", "10.0.0.2:443")
        assert cfg["verify"] is True

    def test_auto_local_proxy(self) -> None:
        cfg = resolve_proxy_config("auto", "http://127.0.0.1:7890", "")
        assert cfg["verify"] is False

    def test_auto_remote_proxy(self) -> None:
        cfg = resolve_proxy_config("auto", "http://proxy.example.com:8080", "")
        assert cfg["verify"] is True

    def test_none_mode_keeps_verify(self) -> None:
        cfg = resolve_proxy_config("none", "", "")
        assert cfg["proxy"] is None
        assert cfg["verify"] is True
