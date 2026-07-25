"""SSRF protection for the link resolver.

User-supplied links are fetched server-side, which is a classic SSRF vector
(e.g. a shortened link that redirects to http://169.254.169.254/ or to an
internal service on 10.x/172.16.x/192.168.x). This module provides the
building blocks used by linkcleaner.link_resolver to defend against that:
resolving the DNS name of a host ourselves and rejecting it if any resolved
IP is private, loopback, link-local, reserved, or multicast, before ever
making the request.
"""

import asyncio
import ipaddress
import socket

import httpx

ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(Exception):
    """Raised when a URL (or one of its redirect hops) is not safe to fetch."""


def is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def assert_host_is_public(host: str) -> None:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve host: {host}") from exc

    ips = {info[4][0] for info in infos}
    if not ips:
        raise UnsafeURLError(f"no IP addresses found for host: {host}")
    for ip in ips:
        if is_blocked_ip(ip):
            raise UnsafeURLError(f"blocked internal/private address for {host}: {ip}")


async def assert_url_is_safe(url: str) -> None:
    parsed = httpx.URL(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"blocked scheme: {parsed.scheme}")
    if not parsed.host:
        raise UnsafeURLError("URL has no host")
    await assert_host_is_public(parsed.host)
