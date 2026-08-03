from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID


@pytest.fixture(scope="session")
def rsa_private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


@pytest.fixture(scope="session")
def p256_private_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


@pytest.fixture(scope="session")
def rsa_public_certificate(rsa_private_key: str) -> str:
    key = serialization.load_pem_private_key(rsa_private_key.encode(), password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Xiaomi Test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


@pytest.fixture(scope="session")
def p256_public_certificate(p256_private_key: str) -> str:
    key = serialization.load_pem_private_key(p256_private_key.encode(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EC Test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


@pytest.fixture
def clean_huawei_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "STOREHELPER_HUAWEI_KEY_ID",
        "STOREHELPER_HUAWEI_SUB_ACCOUNT",
        "STOREHELPER_HUAWEI_PRIVATE_KEY",
        "STOREHELPER_HUAWEI_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def clean_apple_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "STOREHELPER_APPLE_KEY_TYPE",
        "STOREHELPER_APPLE_KEY_ID",
        "STOREHELPER_APPLE_ISSUER_ID",
        "STOREHELPER_APPLE_PRIVATE_KEY",
        "STOREHELPER_APPLE_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def clean_google_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "STOREHELPER_GOOGLE_PROJECT_ID",
        "STOREHELPER_GOOGLE_PRIVATE_KEY_ID",
        "STOREHELPER_GOOGLE_PRIVATE_KEY",
        "STOREHELPER_GOOGLE_CLIENT_EMAIL",
        "STOREHELPER_GOOGLE_TOKEN_URI",
        "STOREHELPER_GOOGLE_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def clean_xiaomi_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "STOREHELPER_XIAOMI_USERNAME",
        "STOREHELPER_XIAOMI_API_SECRET",
        "STOREHELPER_XIAOMI_PUBLIC_KEY_CERTIFICATE",
        "STOREHELPER_XIAOMI_TEST_ACCOUNTS_JSON",
        "STOREHELPER_XIAOMI_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def clean_oppo_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "STOREHELPER_OPPO_CLIENT_ID",
        "STOREHELPER_OPPO_CLIENT_SECRET",
        "STOREHELPER_OPPO_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def clean_vivo_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "STOREHELPER_VIVO_ACCESS_KEY",
        "STOREHELPER_VIVO_SECRET_KEY",
        "STOREHELPER_VIVO_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
