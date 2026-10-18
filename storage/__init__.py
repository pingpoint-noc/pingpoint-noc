"""Storage package factory and exports for Mini-NOC."""

from storage.repository import StorageRepository
from storage.sqlite_backend import SQLiteRepository


def create_repository(backend="sqlite", db_path="data/mini_noc.db"):
    """Instantiate the configured storage repository backend.

    Allows swapping SQLite for another database engine in the future without
    changing collectors or the FastAPI service layer.
    """
    if backend == "sqlite":
        return SQLiteRepository(db_path)
    raise ValueError(f"Unsupported storage backend: {backend}")


__all__ = [
    "StorageRepository",
    "SQLiteRepository",
    "create_repository",
]
