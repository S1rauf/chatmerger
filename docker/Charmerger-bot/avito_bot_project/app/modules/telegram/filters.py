# /app/modules/telegram/filters.py
import logging
import json
from typing import Union, Dict

from aiogram.filters import BaseFilter
from aiogram.types import Message
import redis.asyncio as redis

logger = logging.getLogger(__name__)

class HasAvitoContextFilter(BaseFilter):
    async def __call__(self, message: Message, redis_client: redis.Redis) -> Union[bool, Dict[str, dict]]:
        if not message.reply_to_message:
            return False

        context_key = f"tg_context:{message.reply_to_message.message_id}"
        context_data_json = await redis_client.get(context_key)

        if context_data_json:
            context_data = json.loads(context_data_json)
            logger.info(f"AVITO_CONTEXT_FILTER: Checking context for reply: {context_data}")
            # Проверяем флаг can_reply, который мы передаем из forwarder'a
            if context_data.get("can_reply") != 'true':
                # Если прав нет, не пропускаем сообщение и можно уведомить пользователя
                logger.warning(f"AVITO_CONTEXT_FILTER: Reply DENIED for user. can_reply is '{context_data.get('can_reply')}'")
                try:
                    await message.reply("У вас нет прав для ответа на это сообщение.")
                except TelegramBadRequest:
                    pass
                return False
            logger.info("AVITO_CONTEXT_FILTER: Reply ALLOWED.")
            return {"avito_context": context_data}
        else:
            logger.warning("AVITO_CONTEXT_FILTER: No context found in Redis.")
        
        # Если ключ не найден, фильтр не пропускает обновление
        return False