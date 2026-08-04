"""通用代理检测 — 自动适配各种 Steam 加速器。

支持的代理模式：
  auto    — 自动检测（系统代理 → 常见端口探测 → 直连）
  manual  — 使用 STEAM_PROXY_URL 手动配置
  hosts   — hosts 模式（STEAM_HOSTS_OVERRIDE）
  none    — 不使用代理，直连

自动检测顺序：
  1. Windows 系统代理（注册表 / 环境变量）
  2. 常见加速器端口探测（Steam++ / UU / 通用 HTTP 代理）
  3. 降级为直连
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any
from urllib.parse import urlparse, urlunsplit

logger = logging.getLogger(__name__)

# 常见加速器/代理端口
COMMON_PROXY_PORTS = [
    # Steam++ (Watt Toolkit) 常见端口
    27015, 26561, 15275, 15782,
    # 通用 HTTP 代理端口
    7890,   # Clash
    10808,  # V2Ray
    1080,   # SOCKS
    8080,   # 通用
    33210,  # 网易UU
    8118,   # Privoxy
]


def _is_loopback_host(host: str | None) -> bool:
    """判断主机是否为本地回环地址。

    仅本地回环代理（Steam++/Clash 等监听在 127.0.0.1 的加速器）才允许关闭
    TLS 证书校验；远程代理必须保持校验，否则 steamLoginSecure cookie 与
    私密库存数据可被中间人截获。
    """
    if not host:
        return False
    h = host.strip().strip("[]").lower()
    # 完整匹配（无端口）
    if h in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return True
    # "127.0.0.1:443" 这类 host:port 格式（hosts_override 常见）
    if h.count(":") == 1:
        host_part = h.rsplit(":", 1)[0]
        return host_part in ("127.0.0.1", "localhost", "0.0.0.0")
    return False


def _need_verify(proxy_url: str | None, hosts_override: str | None) -> bool:
    """是否必须启用 TLS 校验：默认校验，仅本地回环代理可关闭。"""
    if hosts_override:
        return not _is_loopback_host(hosts_override)
    if proxy_url:
        try:
            host = urlparse(proxy_url if "://" in proxy_url else f"//{proxy_url}").hostname
        except ValueError:
            host = None
        if host is None:
            return True  # 解析失败一律要求校验
        return not _is_loopback_host(host)
    return True


def _check_port(host: str, port: int, timeout: float = 0.5) -> bool:
    """检查端口是否开放。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _redact_url(url: str) -> str:
    """从 URL 中移除用户名密码，避免日志泄露代理凭据。

    例: http://user:pass@127.0.0.1:8080 -> http://127.0.0.1:8080
    无凭据的 URL 原样返回；无 scheme 的 host:port 保持原样。
    """
    try:
        has_scheme = "://" in url
        parts = urlparse(url if has_scheme else f"//{url}")
        if not parts.username and not parts.password:
            return url
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        if not has_scheme:
            return host
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    except ValueError:
        return url


def _get_windows_system_proxy() -> str | None:
    """从 Windows 注册表读取系统代理设置。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if not enable:
            winreg.CloseKey(key)
            return None
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        winreg.CloseKey(key)
        if server and ":" in server:
            proxy_url = f"http://{server}"
            logger.info("检测到 Windows 系统代理: %s", _redact_url(proxy_url))
            return proxy_url
        elif server:
            logger.info("检测到 Windows 系统代理: %s (无端口，跳过)", server)
            return None
    except Exception:
        pass
    return None


def _get_env_proxy() -> str | None:
    """从环境变量读取代理。"""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(var, "").strip()
        if val:
            logger.info("检测到环境变量代理: %s=%s", var, _redact_url(val))
            return val
    return None


def _probe_common_ports() -> str | None:
    """探测常见加速器端口。"""
    for port in COMMON_PROXY_PORTS:
        if _check_port("127.0.0.1", port):
            url = f"http://127.0.0.1:{port}"
            logger.info("探测到本地代理端口 %d, 使用 %s", port, url)
            return url
    return None


def _probe_hosts_override() -> str | None:
    """检测 hosts 模式：steamcommunity.com 是否解析到 127.0.0.1。"""
    try:
        ip = socket.gethostbyname("steamcommunity.com")
        if ip in ("127.0.0.1", "0.0.0.0"):
            # 检查 443 端口是否有服务
            if _check_port("127.0.0.1", 443):
                logger.info("检测到 hosts 模式: steamcommunity.com → %s:443", ip)
                return "127.0.0.1:443"
    except socket.gaierror:
        pass
    return None


def detect_proxy(mode: str = "auto") -> dict[str, Any]:
    """根据模式检测代理配置，返回 httpx 客户端参数。

    Args:
        mode: "auto" | "manual" | "hosts" | "none"

    Returns:
        {
            "proxy": "http://..." | None,
            "hosts_override": "ip:port" | None,
            "verify": bool,
            "headers_extra": {"Host": "..."} | {},
        }
    """
    result: dict[str, Any] = {
        "proxy": None,
        "hosts_override": None,
        "verify": True,
        "headers_extra": {},
    }

    if mode == "none":
        logger.info("代理模式: none (直连)")
        return result

    if mode == "hosts":
        # 手动 hosts 模式，由配置文件指定
        return result

    if mode == "manual":
        # 手动代理模式，由配置文件指定
        return result

    # === auto 模式 ===
    logger.info("代理模式: auto, 开始自动检测...")

    # 1. 环境变量（优先级最高）
    proxy = _get_env_proxy()
    if proxy:
        result["proxy"] = proxy
        result["verify"] = _need_verify(proxy, None)
        logger.info("自动检测结果: 环境变量代理 %s", proxy)
        return result

    # 2. Windows 系统代理
    proxy = _get_windows_system_proxy()
    if proxy:
        result["proxy"] = proxy
        result["verify"] = _need_verify(proxy, None)
        logger.info("自动检测结果: 系统代理 %s", proxy)
        return result

    # 3. hosts 模式检测
    hosts = _probe_hosts_override()
    if hosts:
        result["hosts_override"] = hosts
        result["verify"] = _need_verify(None, hosts)
        result["headers_extra"]["Host"] = "steamcommunity.com"
        logger.info("自动检测结果: hosts 模式 → %s", hosts)
        return result

    # 4. 常见端口探测
    proxy = _probe_common_ports()
    if proxy:
        result["proxy"] = proxy
        result["verify"] = _need_verify(proxy, None)
        logger.info("自动检测结果: 端口探测代理 %s", proxy)
        return result

    # 5. 降级为直连
    logger.info("自动检测结果: 未发现代理，直连模式")
    return result


def resolve_proxy_config(
    proxy_mode: str,
    proxy_url: str,
    hosts_override: str,
) -> dict[str, Any]:
    """综合配置解析代理参数。

    优先级：manual/hosts 配置 > auto 检测 > 直连
    """
    if proxy_mode == "manual" and proxy_url:
        return {
            "proxy": proxy_url,
            "hosts_override": None,
            "verify": _need_verify(proxy_url, None),
            "headers_extra": {},
        }

    if proxy_mode == "hosts" and hosts_override:
        return {
            "proxy": None,
            "hosts_override": hosts_override,
            "verify": _need_verify(None, hosts_override),
            "headers_extra": {"Host": "steamcommunity.com"},
        }

    if proxy_mode == "auto":
        # 先检查是否有手动配置
        if proxy_url:
            return {
                "proxy": proxy_url,
                "hosts_override": None,
                "verify": _need_verify(proxy_url, None),
                "headers_extra": {},
            }
        if hosts_override:
            return {
                "proxy": None,
                "hosts_override": hosts_override,
                "verify": _need_verify(None, hosts_override),
                "headers_extra": {"Host": "steamcommunity.com"},
            }
        # 自动检测
        return detect_proxy("auto")

    # mode == "none" 或未识别
    return detect_proxy("none")
