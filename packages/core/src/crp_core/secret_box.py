"""Encryption at rest for secrets users give the service (ADR 0026: projects' own AI keys).

AES-256-GCM with a key derived (HKDF-SHA256) from the server secret ``CRP_SECRETS_KEY`` or the
owner-only file ``CRP_SECRETS_KEY_FILE``. Each value is bound to its purpose and owner through
the associated data (for example the project and provider), so a ciphertext copied to another
row does not decrypt. Plain values are never logged or returned; a changed or lost server secret
makes stored values unreadable, and users then enter them again.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from crp_core.config import Settings
from crp_core.local_secrets import SecretFileError, read_secret_file

PREFIX = "v1."
MIN_SECRET_LENGTH = 32
_INFO = b"refactorx secret box v1"


class SecretBoxError(ValueError):
    """A value cannot be decrypted (wrong server secret, tampered or copied ciphertext)."""


class SecretBox:
    def __init__(self, server_secret: str) -> None:
        if len(server_secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"the server secret needs at least {MIN_SECRET_LENGTH} characters")
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(
            server_secret.encode("utf-8")
        )
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = os.urandom(12)
        sealed = self._aead.encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
        return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, token: str, context: str) -> str:
        if not token.startswith(PREFIX):
            raise SecretBoxError("unknown format")
        try:
            raw = base64.urlsafe_b64decode(token[len(PREFIX) :].encode("ascii"))
            opened = self._aead.decrypt(raw[:12], raw[12:], context.encode("utf-8"))
        except (InvalidTag, ValueError) as exc:
            raise SecretBoxError("the value cannot be decrypted") from exc
        return opened.decode("utf-8")


def secret_box(settings: Settings) -> SecretBox | None:
    """The server's box, or None when no usable server secret is configured."""
    if settings.secrets_key is not None and settings.secrets_key.get_secret_value().strip():
        value = settings.secrets_key.get_secret_value().strip()
    elif settings.secrets_key_file is not None:
        try:
            value = read_secret_file(settings.secrets_key_file, min_length=MIN_SECRET_LENGTH)
        except SecretFileError:
            return None
    else:
        return None
    try:
        return SecretBox(value)
    except ValueError:
        return None
