"""Xiaomi protocol-required MD5 digests and RSA PKCS#1 v1.5 encryption."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from storehelper.credentials.models import XiaomiApiCredential

_HASH_CHUNK_SIZE = 1024 * 1024


class XiaomiAuth:
    """Build encrypted Xiaomi SIG values without retaining plaintext signatures."""

    def __init__(self, credential: XiaomiApiCredential) -> None:
        self._credential = credential
        certificate = x509.load_pem_x509_certificate(
            credential.public_key_certificate.get_secret_value().encode("utf-8")
        )
        public_key = certificate.public_key()
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise TypeError("Xiaomi public certificate must contain an RSA key.")
        self._public_key = public_key

    def __repr__(self) -> str:
        return f"XiaomiAuth(username={self._credential.username!r})"

    @staticmethod
    def md5_text(value: str) -> str:
        return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()

    @staticmethod
    def md5_file(path: Path) -> str:
        digest = hashlib.md5(usedforsecurity=False)
        with path.open("rb") as source:
            while chunk := source.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()

    def signature(
        self,
        request_data: str,
        *,
        files: Sequence[tuple[str, Path]] = (),
    ) -> str:
        parts = [{"name": "RequestData", "hash": self.md5_text(request_data)}]
        parts.extend({"name": name, "hash": self.md5_file(path)} for name, path in files)
        plaintext = json.dumps(
            {
                "sig": parts,
                "password": self._credential.api_secret.get_secret_value(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        chunk_size = self._public_key.key_size // 8 - 11
        encrypted = b"".join(
            self._public_key.encrypt(
                plaintext[offset : offset + chunk_size],
                padding.PKCS1v15(),
            )
            for offset in range(0, len(plaintext), chunk_size)
        )
        return encrypted.hex()
