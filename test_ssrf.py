import pytest

from main import UnsafeURLError, _assert_host_is_public, _is_blocked_ip


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",       # loopback
        "10.0.0.1",        # private (RFC1918)
        "172.16.0.5",      # private (RFC1918)
        "192.168.1.1",     # private (RFC1918)
        "169.254.169.254", # link-local / cloud metadata endpoint
        "0.0.0.0",         # unspecified
        "::1",             # IPv6 loopback
        "fc00::1",         # IPv6 unique local
        "fe80::1",         # IPv6 link-local
    ],
)
def test_blocks_private_and_internal_ips(ip):
    assert _is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "142.250.190.14"])
def test_allows_public_ips(ip):
    assert _is_blocked_ip(ip) is False


def test_rejects_invalid_ip_string():
    assert _is_blocked_ip("not-an-ip") is True


@pytest.mark.asyncio
async def test_assert_host_is_public_blocks_localhost():
    with pytest.raises(UnsafeURLError):
        await _assert_host_is_public("localhost")
