from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from storehelper.credentials.models import XiaomiApiCredential
from storehelper.stores.xiaomi.auth import XiaomiAuth


def _credential(certificate: str, *, secret: str = "api-secret") -> XiaomiApiCredential:
    return XiaomiApiCredential(
        username="developer@example.com",
        api_secret=secret,
        public_key_certificate=certificate,
    )


def _certificate(key: rsa.RSAPrivateKey) -> str:
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Xiaomi Test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _decrypt(ciphertext_hex: str, key: rsa.RSAPrivateKey) -> str:
    ciphertext = bytes.fromhex(ciphertext_hex)
    block_size = key.key_size // 8
    assert len(ciphertext) % block_size == 0
    plaintext = b"".join(
        key.decrypt(ciphertext[offset : offset + block_size], padding.PKCS1v15())
        for offset in range(0, len(ciphertext), block_size)
    )
    return plaintext.decode("utf-8")


def test_signature_hashes_exact_request_data_and_files_in_given_order(
    tmp_path: Path,
    rsa_private_key: str,
    rsa_public_certificate: str,
) -> None:
    request_data = '{"packageName":"com.example.wallet","userName":"开发者@example.com"}'
    apk = tmp_path / "wallet.apk"
    icon = tmp_path / "icon.png"
    apk.write_bytes(b"apk-bytes")
    icon.write_bytes(b"icon-bytes")
    private_key = serialization.load_pem_private_key(rsa_private_key.encode(), password=None)
    assert isinstance(private_key, rsa.RSAPrivateKey)

    encrypted = XiaomiAuth(_credential(rsa_public_certificate)).signature(
        request_data,
        files=(("apk", apk), ("icon", icon)),
    )
    plaintext = _decrypt(encrypted, private_key)

    assert plaintext == json.dumps(
        {
            "sig": [
                {
                    "name": "RequestData",
                    "hash": hashlib.md5(request_data.encode(), usedforsecurity=False).hexdigest(),
                },
                {
                    "name": "apk",
                    "hash": hashlib.md5(b"apk-bytes", usedforsecurity=False).hexdigest(),
                },
                {
                    "name": "icon",
                    "hash": hashlib.md5(b"icon-bytes", usedforsecurity=False).hexdigest(),
                },
            ],
            "password": "api-secret",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


@pytest.mark.parametrize("key_size", [1024, 2048])
def test_signature_uses_dynamic_rsa_chunks_and_lowercase_hex(key_size: int) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    auth = XiaomiAuth(_credential(_certificate(key), secret="s" * 300))

    encrypted = auth.signature('{"value":"' + "多语言" * 100 + '"}')

    assert re.fullmatch(r"[0-9a-f]+", encrypted)
    assert len(bytes.fromhex(encrypted)) > key_size // 8
    plaintext = json.loads(_decrypt(encrypted, key))
    assert plaintext["password"] == "s" * 300
    assert plaintext["sig"][0]["name"] == "RequestData"


def test_file_md5_is_streamed_in_fixed_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[int] = []

    class Stream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            if size < 0:
                raise AssertionError("unbounded read")
            return super().read(size)

    stream = Stream(b"x" * (2 * 1024 * 1024 + 17))

    class Context:
        def __enter__(self) -> Stream:
            return stream

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(Path, "open", lambda self, mode: Context())

    digest = XiaomiAuth.md5_file(Path("unused.apk"))

    assert digest == hashlib.md5(b"x" * (2 * 1024 * 1024 + 17), usedforsecurity=False).hexdigest()
    assert reads == [1024 * 1024, 1024 * 1024, 1024 * 1024, 1024 * 1024]


def test_auth_repr_never_exposes_certificate_or_api_secret(
    rsa_public_certificate: str,
) -> None:
    auth = XiaomiAuth(_credential(rsa_public_certificate, secret="sensitive-secret"))

    rendered = repr(auth)

    assert rendered == "XiaomiAuth(username='developer@example.com')"
    assert "sensitive-secret" not in rendered
    assert rsa_public_certificate not in rendered
