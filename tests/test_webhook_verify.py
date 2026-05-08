"""Tests for shared.webhook_verify."""

import hashlib
import hmac
import time

from shared.webhook_verify import (
    verify_github,
    verify_hmac_sha256,
    verify_slack,
    verify_stripe,
)


SECRET = "shh-keep-it-secret"


def _sha256(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestHmacSha256:
    def test_valid_signature_passes(self) -> None:
        body = b'{"ok": true}'
        sig = _sha256(SECRET, body)
        assert verify_hmac_sha256(secret=SECRET, body=body, signature=sig)

    def test_prefix_stripped_before_compare(self) -> None:
        body = b"x"
        sig = "sha256=" + _sha256(SECRET, body)
        assert verify_hmac_sha256(secret=SECRET, body=body, signature=sig, prefix="sha256=")

    def test_wrong_signature_rejected(self) -> None:
        assert not verify_hmac_sha256(secret=SECRET, body=b"x", signature="00")

    def test_empty_secret_or_signature_rejected(self) -> None:
        assert not verify_hmac_sha256(secret="", body=b"x", signature="00")
        assert not verify_hmac_sha256(secret=SECRET, body=b"x", signature="")


class TestStripe:
    def test_valid_signature_passes(self) -> None:
        body = b"payload"
        ts = str(int(time.time()))
        signed = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        header = f"t={ts},v1={signed}"
        assert verify_stripe(secret=SECRET, body=body, signature_header=header)

    def test_old_timestamp_rejected_outside_tolerance(self) -> None:
        body = b"x"
        ts = str(int(time.time()) - 1000)  # 1000s ago > 300s default tolerance
        signed = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        header = f"t={ts},v1={signed}"
        assert not verify_stripe(secret=SECRET, body=body, signature_header=header)

    def test_malformed_header_rejected(self) -> None:
        assert not verify_stripe(secret=SECRET, body=b"x", signature_header="garbage")
        assert not verify_stripe(secret=SECRET, body=b"x", signature_header="")


class TestGitHub:
    def test_valid_signature_passes(self) -> None:
        body = b'{"action":"opened"}'
        sig = "sha256=" + _sha256(SECRET, body)
        assert verify_github(secret=SECRET, body=body, signature_header=sig)

    def test_missing_header_rejected(self) -> None:
        assert not verify_github(secret=SECRET, body=b"x", signature_header=None)


class TestSlack:
    def test_valid_signature_passes(self) -> None:
        body = b"payload"
        ts = str(int(time.time()))
        base = f"v0:{ts}:".encode() + body
        sig = "v0=" + hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()
        assert verify_slack(secret=SECRET, body=body, timestamp=ts, signature=sig)

    def test_replay_outside_tolerance_rejected(self) -> None:
        body = b"x"
        ts = str(int(time.time()) - 10_000)
        base = f"v0:{ts}:".encode() + body
        sig = "v0=" + hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()
        assert not verify_slack(secret=SECRET, body=body, timestamp=ts, signature=sig)
