"""Signature verification for Sparkle-like update protocol.

Provides Ed25519 verification (via PyNaCl if available) with graceful
fallback to SHA-256 HMAC using only stdlib.  All verification is
pure-function: no I/O beyond reading the file whose checksum is checked.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── Ed25519 (optional) └─────────────────────────────────────

try:
    from nacl.signing import VerifyKey  # type: ignore[import-untyped]
    from nacl.exceptions import BadSignatureError  # type: ignore[import-untyped]

    _NACL_AVAILABLE = True
except ImportError:
    _NACL_AVAILABLE = False


def verify_ed25519(
    data: bytes,
    signature: bytes,
    public_key: bytes,
) -> bool:
    """Verify *data* against an Ed25519 *signature* with *public_key*.

    Returns ``True`` on success, ``False`` on any failure (bad sig,
    missing library, wrong key length).  Never raises.
    """
    if not _NACL_AVAILABLE:
        log.debug("PyNaCl not installed — Ed25519 verification skipped")
        return False
    try:
        verify_key = VerifyKey(public_key)
        verify_key.verify(data, signature)
        return True
    except (BadSignatureError, Exception) as exc:
        log.debug("Ed25519 verification failed: %s", exc)
        return False


# ── SHA-256 fallback ─────────────────────────────────────────

def verify_sha256(data: bytes, expected_hex: str) -> bool:
    """Compare SHA-256 digest of *data* against *expected_hex*.

    Uses ``hmac.compare_digest`` for constant-time comparison.
    """
    actual = hashlib.sha256(data).hexdigest()
    return hmac.compare_digest(actual, expected_hex.strip().lower())


def verify_file_sha256(file_path: str, expected_hex: str) -> bool:
    """Stream-hash *file_path* and compare to *expected_hex*.

    Reads in 64 KiB chunks to keep memory bounded for large archives.
    """
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                h.update(chunk)
    except OSError as exc:
        log.debug("Failed to read %s for checksum: %s", file_path, exc)
        return False
    return hmac.compare_digest(h.hexdigest(), expected_hex.strip().lower())
