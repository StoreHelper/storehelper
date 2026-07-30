from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


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
