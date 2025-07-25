# /app/modules/telegram/middlewares.py
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
import redis.asyncio as redis

class DbSessionMiddleware(BaseMiddleware):
    """
    Этот middleware будет передавать в хендлеры клиент Redis.
    """
    # Возвращаем конструктор
    def __init__(self, redis_client: redis.Redis):
        super().__init__()
        self.redis_client = redis_client

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Просто кладем наш клиент в data
        data["redis_client"] = self.redis_client
        return await handler(event, data)