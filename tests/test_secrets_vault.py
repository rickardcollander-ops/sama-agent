"""
Tests for shared.secrets_vault.

Round-trip is the critical contract: encrypted → decrypted must equal
original, and the format must be stable enough that ciphertext written
today still decrypts a year from now.
"""

import base64
import os

import pytest

from shared import secrets_vault


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    key = os.urandom(32)
    monkeypatch.setenv("MASTER_KMS_KEY", base64.b64encode(key).decode())
    return key


class TestEncryptionRoundTrip:
    def test_round_trip_preserves_plaintext(self, master_key: bytes) -> None:
        original = {"openai_api_key": "sk-abc123", "google_api_key": "AIza..."}
        blob = secrets_vault.encrypt_payload(original)
        assert blob is not None
        assert blob.startswith("v1$")
        decrypted = secrets_vault.decrypt_payload(blob)
        assert decrypted == original

    def test_two_encryptions_produce_different_ciphertext(self, master_key: bytes) -> None:
        # Per-row DEK + random nonce → identical plaintext must yield different blobs.
        payload = {"k": "same"}
        a = secrets_vault.encrypt_payload(payload)
        b = secrets_vault.encrypt_payload(payload)
        assert a != b
        assert secrets_vault.decrypt_payload(a) == payload
        assert secrets_vault.decrypt_payload(b) == payload

    def test_decrypt_with_wrong_master_key_fails_silently(
        self, master_key: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blob = secrets_vault.encrypt_payload({"k": "v"})
        # Swap the master key.
        monkeypatch.setenv("MASTER_KMS_KEY", base64.b64encode(os.urandom(32)).decode())
        assert secrets_vault.decrypt_payload(blob) is None

    def test_malformed_blob_returns_none(self, master_key: bytes) -> None:
        assert secrets_vault.decrypt_payload("not-a-valid-blob") is None
        assert secrets_vault.decrypt_payload("") is None
        assert secrets_vault.decrypt_payload("v1$junk$junk$junk$junk") is None

    def test_encryption_unavailable_without_master_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MASTER_KMS_KEY", raising=False)
        assert secrets_vault.encryption_available() is False
        assert secrets_vault.encrypt_payload({"k": "v"}) is None
        assert secrets_vault.decrypt_payload("v1$x$y$z$w") is None

    def test_invalid_master_key_length_disables_encryption(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 16 bytes is too short — secretbox needs 32.
        monkeypatch.setenv("MASTER_KMS_KEY", base64.b64encode(b"x" * 16).decode())
        assert secrets_vault.encryption_available() is False


class TestSplitSecrets:
    def test_splits_by_field_name_suffix(self) -> None:
        non, sec = secrets_vault.split_secrets({
            "brand_name": "Successifier",
            "openai_api_key": "sk-x",
            "google_refresh_token": "r-x",
            "client_secret": "cs-x",
            "competitors": ["a", "b"],
        })
        assert non == {"brand_name": "Successifier", "competitors": ["a", "b"]}
        assert sec == {
            "openai_api_key": "sk-x",
            "google_refresh_token": "r-x",
            "client_secret": "cs-x",
        }

    def test_unknown_fields_treated_as_non_secret(self) -> None:
        non, sec = secrets_vault.split_secrets({"foo": 1, "bar": "baz"})
        assert non == {"foo": 1, "bar": "baz"}
        assert sec == {}

    def test_empty_input(self) -> None:
        assert secrets_vault.split_secrets({}) == ({}, {})


class TestMergeWithSecrets:
    def test_secrets_override_non_secrets(self) -> None:
        merged = secrets_vault.merge_with_secrets({"k": "old"}, {"k": "new"})
        assert merged == {"k": "new"}

    def test_none_secrets_passes_through(self) -> None:
        merged = secrets_vault.merge_with_secrets({"k": "v"}, None)
        assert merged == {"k": "v"}
