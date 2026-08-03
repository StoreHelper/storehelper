"""Public StoreHelper Python API."""

from importlib.metadata import PackageNotFoundError, version

from storehelper.domain.models import OperationResult, PublishRequest
from storehelper.publishing.service import Publisher

try:
    __version__ = version("storehelper")
except PackageNotFoundError:
    __version__ = "0.4.0"

__all__ = ["OperationResult", "PublishRequest", "Publisher", "__version__"]
