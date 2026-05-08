"""
Envelope encryption for secrets stored in user_settings.

Design
------
- A single 32-byte master key, ``MASTER_KMS_KEY`` (base64 in env), is the root
  of trust. Lose this and ciphertexts are unrecoverable; leak it and every
  per-tenant secret is exposed.
- Each row has a per-row 32-byte data-encryption-key (DEK). The DEK encrypts
  the plaintext payload; the master key encrypts the DEK ("envelope"). Stored
  ciphertext = ``v1$<base64 nonce_master>$<base64 wrapped_dek>$<base64 nonce_data>$<base64 ciphertext>``.
- Algorithm: NaCl secretbox (XSalsa20 + Poly1305). 24-byte nonces, AEAD.
- Versioning: prefix ``v1$`` so we can roll the algorithm later without
  breaking deployed rows.

The plaintext column ``settings`` (JSONB) and the ciphertext column
``settings_encrypted`` (TEXT) coexist during the migration window. The
``READ_ENCRYPTED_ONLY`` env flag flips the read path to ciphertext once
backfill is complete; afterwards the plaintext column is dropped.

Only the secret-shaped fields are encrypted: ``*_api_key``, ``*_token``,
``*_secret``, ``client_secret``. Brand voice / domain / queries stay as
plaintext for fast read paths.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_VERSION_PREFIX = "v1"
_SECRET_FIELD_SUFFIXES = ("_api_key", "_token", "_secret", "_credentials")
_SECRET_FIELD_NAMES = {"client_secret", "private_key", "refresh_token"}


def _is_secret_field(name: str) -> bool:
    if name in _SECRET_FIELD_NAMES:
        return True
    return any(name.endswith(s) for s in _SECRET_FIELD_SUFFIXES)


def _master_key() -> Optional[bytes]:
    raw = os.getenv("MASTER_KMS_KEY", "").strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw)
    except Exception:
        logger.warning("MASTER_KMS_KEY is set but not valid base64; encryption disabled")
        return None
    if len(key) != 32:
        logger.warning("MASTER_KMS_KEY must decode to 32 bytes; encryption disabled")
        return None
    return key


def encryption_available() -> bool:
    return _master_key() is not None


def _seal(plaintext: bytes, key: bytes) -> Tuple[bytes, bytes]:
    """Return (nonce, ciphertext) using NaCl secretbox."""
    from nacl import secret, utils  # imported lazily so local tests w/o pynacl still load

    box = secret.SecretBox(key)
    nonce = utils.random(secret.SecretBox.NONCE_SIZE)
    ct = box.encrypt(plaintext, nonce).ciphertext  # secretbox prepends MAC
    return nonce, ct


def _open(nonce: bytes, ciphertext: bytes, key: bytes) -> bytes:
    from nacl import secret

    box = secret.SecretBox(key)
    return box.decrypt(ciphertext, nonce)


def encrypt_payload(payload: Dict[str, Any]) -> Optional[str]:
    """Encrypt a JSON-serialisable dict. Returns None if encryption unavailable."""
    master = _master_key()
    if master is None:
        return None
    from nacl import utils

    dek = utils.random(32)
    nonce_data, ct_data = _seal(json.dumps(payload, ensure_ascii=False).encode("utf-8"), dek)
    nonce_master, ct_dek = _seal(dek, master)
    parts = [
        _VERSION_PREFIX,
        base64.b64encode(nonce_master).decode(),
        base64.b64encode(ct_dek).decode(),
        base64.b64encode(nonce_data).decode(),
        base64.b64encode(ct_data).decode(),
    ]
    return "$".join(parts)


def decrypt_payload(blob: str) -> Optional[Dict[str, Any]]:
    """Inverse of ``encrypt_payload``. Returns None if blob is malformed or
    decryption fails — never raise; the caller falls back to plaintext."""
    if not blob:
        return None
    master = _master_key()
    if master is None:
        return None
    try:
        version, nm, cd, nd, ctd = blob.split("$", 4)
        if version != _VERSION_PREFIX:
            logger.warning("unknown vault version %s", version)
            return None
        nonce_master = base64.b64decode(nm)
        ct_dek = base64.b64decode(cd)
        nonce_data = base64.b64decode(nd)
        ct_data = base64.b64decode(ctd)
        dek = _open(nonce_master, ct_dek, master)
        try:
            plaintext = _open(nonce_data, ct_data, dek)
        finally:
            # Best-effort scrub. CPython doesn't guarantee zeroing but we try.
            dek = b"\x00" * len(dek)
        return json.loads(plaintext.decode("utf-8"))
    except Exception as e:
        logger.warning("decrypt_payload failed: %s", e)
        return None


def split_secrets(settings: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Split ``settings`` into (non_secret, secret) halves based on field name."""
    secrets: Dict[str, Any] = {}
    non_secrets: Dict[str, Any] = {}
    for k, v in settings.items():
        if _is_secret_field(k):
            secrets[k] = v
        else:
            non_secrets[k] = v
    return non_secrets, secrets


def merge_with_secrets(
    non_secrets: Dict[str, Any],
    decrypted_secrets: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    out = dict(non_secrets)
    if decrypted_secrets:
        out.update(decrypted_secrets)
    return out
