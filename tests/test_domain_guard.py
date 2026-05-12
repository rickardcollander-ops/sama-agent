"""
Unit coverage for the cross-tenant domain guard.

These tests cover:

* The host normaliser and comparator in ``shared.domain``. They mirror the
  dashboard's ``lib/domain.ts`` so the read filter behaves identically on
  both sides of the wire.
* ``_expected_domain`` helpers in the site-audit and analysis routes — they
  read tenant config the same way and must return ``""`` when the tenant
  hasn't configured a domain (the guard is deliberately skipped in that
  case so legacy tenants don't blank out).
"""

from types import SimpleNamespace

import pytest

from shared.domain import normalize_host, same_domain


# ── normalize_host ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("example.com", "example.com"),
        ("Example.COM", "example.com"),
        ("  example.com  ", "example.com"),
        ("https://example.com", "example.com"),
        ("HTTPS://Example.com/", "example.com"),
        ("http://example.com/path?q=1", "example.com"),
        ("https://www.example.com", "example.com"),
        ("www.example.com", "example.com"),
        ("example.com/", "example.com"),
        ("example.com/path", "example.com"),
        ("example.com?utm=foo", "example.com"),
        ("example.com#frag", "example.com"),
        ("user:pw@example.com", "example.com"),
        ("https://www.cbrb.se/audit?id=1", "cbrb.se"),
    ],
)
def test_normalize_host_strips_noise(value, expected):
    assert normalize_host(value) == expected


@pytest.mark.parametrize("value", ["", None, "   ", 123, {}, []])
def test_normalize_host_returns_empty_for_falsy_or_non_string(value):
    assert normalize_host(value) == ""


# ── same_domain ──────────────────────────────────────────────────────────────


def test_same_domain_matches_after_normalisation():
    assert same_domain("https://www.example.com/x", "EXAMPLE.com")
    assert same_domain("cbrb.se", "www.cbrb.se")


def test_same_domain_rejects_different_hosts():
    assert not same_domain("cbrb.se", "successifier.se")
    assert not same_domain("https://cbrb.se", "https://api.cbrb.se")  # subdomain mismatch


def test_same_domain_returns_false_when_either_side_empty():
    # Callers branch on the configured domain themselves; an unset side must
    # never silently match anything.
    assert not same_domain("", "example.com")
    assert not same_domain("example.com", "")
    assert not same_domain(None, None)


# ── Route helpers: tenant config → expected host ─────────────────────────────


def test_site_audit_expected_domain_matches_tenant_config():
    from api.routes.site_audit import _expected_domain
    cfg = SimpleNamespace(domain="https://WWW.Example.com/")
    assert _expected_domain(cfg) == "example.com"


def test_site_audit_expected_domain_blank_when_unset():
    from api.routes.site_audit import _expected_domain
    assert _expected_domain(SimpleNamespace(domain="")) == ""
    assert _expected_domain(SimpleNamespace(domain=None)) == ""
    assert _expected_domain(SimpleNamespace()) == ""


def test_analysis_expected_domain_matches_tenant_config():
    from api.routes.analysis import _expected_domain
    cfg = SimpleNamespace(domain="cbrb.se")
    assert _expected_domain(cfg) == "cbrb.se"


def test_analysis_expected_domain_blank_when_unset():
    from api.routes.analysis import _expected_domain
    assert _expected_domain(SimpleNamespace(domain="")) == ""
    assert _expected_domain(SimpleNamespace(domain=None)) == ""
