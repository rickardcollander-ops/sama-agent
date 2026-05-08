"""
Tests for shared.safe_http SSRF guard.

The IP allowlist is the security boundary; if these tests don't catch
private/loopback/metadata addresses, the prod scrapers don't either.
"""

from unittest.mock import patch

import pytest

from shared.safe_http import UnsafeURLError, _is_safe_ip, assert_safe_url


class TestIsSafeIP:
    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",       # Google DNS — public
            "1.1.1.1",       # Cloudflare — public
            "93.184.216.34", # example.com
            "2606:4700:4700::1111",  # public IPv6
        ],
    )
    def test_public_ips_allowed(self, ip: str) -> None:
        assert _is_safe_ip(ip) is True

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.255.255.255",
            "::1",
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.1.1",
            "169.254.169.254",  # AWS / GCP metadata service
            "0.0.0.0",
            "224.0.0.1",   # multicast
            "fc00::1",     # IPv6 private
            "fe80::1",     # IPv6 link-local
        ],
    )
    def test_private_and_metadata_ips_blocked(self, ip: str) -> None:
        assert _is_safe_ip(ip) is False

    def test_garbage_input_blocked(self) -> None:
        assert _is_safe_ip("not-an-ip") is False
        assert _is_safe_ip("") is False


class TestAssertSafeUrl:
    def test_blocks_disallowed_scheme(self) -> None:
        with pytest.raises(UnsafeURLError, match="scheme"):
            assert_safe_url("file:///etc/passwd")
        with pytest.raises(UnsafeURLError, match="scheme"):
            assert_safe_url("gopher://example.com/")

    def test_blocks_ip_literal_in_private_space(self) -> None:
        with pytest.raises(UnsafeURLError, match="private"):
            assert_safe_url("http://10.0.0.1/")
        with pytest.raises(UnsafeURLError, match="private"):
            assert_safe_url("http://169.254.169.254/latest/meta-data/")

    def test_missing_host_blocked(self) -> None:
        with pytest.raises(UnsafeURLError, match="host"):
            assert_safe_url("https:///path-only")

    def test_blocks_dns_pointing_at_private(self) -> None:
        # A hostname that resolves to a private IP must be blocked, not just
        # IP-literal targets.
        with patch("shared.safe_http._resolve", return_value=["10.0.0.5"]):
            with pytest.raises(UnsafeURLError, match="private/loopback"):
                assert_safe_url("https://attacker.example.com/")

    def test_allows_public_dns(self) -> None:
        with patch("shared.safe_http._resolve", return_value=["8.8.8.8"]):
            assert_safe_url("https://google-dns.example.com/")
            # No exception means OK.

    def test_dns_failure_treated_as_unsafe(self) -> None:
        import socket
        with patch("shared.safe_http.socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            with pytest.raises(UnsafeURLError, match="DNS"):
                assert_safe_url("https://nonexistent.invalid/")
