import asyncio
from typing import Any, Callable, Optional, TypeVar

from supabase import Client, create_client

from src.core.config import config

T = TypeVar("T")

_client: Optional[Client] = None


def get_db() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.supabase_url, config.supabase_service_key)
    return _client


async def run_sync(func: Callable[[], T]) -> T:
    """Запускает синхронную DB-операцию в пуле потоков."""
    return await asyncio.to_thread(func)
