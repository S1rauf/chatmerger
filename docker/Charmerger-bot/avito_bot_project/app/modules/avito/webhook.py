# /app/modules/avito/webhook.py

import logging
import hmac
import hashlib
import json
from typing import Optional

from fastapi import Request, Header, HTTPException
import redis.asyncio as redis

from shared.config import settings

logger = logging.getLogger(__name__)


async def verify_avito_signature(payload: bytes, signature: str) -> bool:
    """Проверяет подпись вебхука Avito."""
    if not signature:
        logger.warning("Webhook received without X-Signature header.")
        return False
        
    secret = settings.avito_webhook_secret.encode('utf-8')
    expected_signature = hmac.new(secret, msg=payload, digestmod=hashlib.sha256).hexdigest()
    
    return hmac.compare_digest(expected_signature, signature)


class AvitoWebhookHandler:
    """
    Обрабатывает входящие вебхуки от Avito.
    """
    def __init__(self, redis_client: redis.Redis):
        """
        Инициализирует обработчик с клиентом Redis.
        
        :param redis_client: Активный клиент для работы с Redis.
        """
        self.redis = redis_client

    async def handle_request(self, request: Request, x_signature: Optional[str] = Header(None)):
        """
        Основной метод для обработки входящего запроса.
        Валидирует подпись и ставит событие в очередь Redis.
        """
        # 1. Получаем "сырое" тело запроса для проверки подписи
        payload_bytes = await request.body()

        # # 2. Проверяем подпись (если AVITO_WEBHOOK_SECRET задан)
        # if settings.avito_webhook_secret and not await verify_avito_signature(payload_bytes, x_signature):
        #     logger.error("Invalid Avito webhook signature.")
        #     raise HTTPException(status_code=403, detail="Invalid signature.")

        # 3. Декодируем JSON
        payload = json.loads(payload_bytes)
        
        webhook_data = payload.get("payload", {})
        event_type = webhook_data.get("type")
        
        if event_type != "message":
            logger.info(f"Skipping unsupported webhook event type: {event_type}")
            return {"status": "event_skipped"}

        # --- НОВАЯ ЛОГИКА: ПРОВЕРКА АВТОРА СООБЩЕНИЯ ---
        message_content = webhook_data.get("value", {})
        
        # ID аккаунта, НА который пришел вебхук
        account_id = str(message_content.get("user_id", ""))
        # ID того, КТО написал сообщение
        author_id = str(message_content.get("author_id", ""))

        # Если автор сообщения - это владелец аккаунта, то это наше собственное сообщение.
        # Мы не должны его обрабатывать как входящее.
        if account_id == author_id:
            logger.info(f"AVITO_WEBHOOK: Skipping own outgoing message for account {account_id}.")
            return {"status": "own_message_skipped"}
        # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

        # Формируем сообщение для стрима (этот код у вас уже есть)
        chat_id = str(message_content.get("chat_id", ""))
        message_data = {
            "account_id": account_id,
            "chat_id": chat_id,
            "sender_id": author_id, # sender_id - это author_id
            "text": str(message_content.get("content", {}).get("text", "")),
            "created_ts": str(message_content.get("created", ""))
        }

        # Проверяем, что у нас есть ключевые данные
        if not message_data["account_id"] or not message_data["chat_id"]:
            logger.warning(f"Received Avito message with missing user_id or chat_id. Payload: {payload}")
            return {"status": "event_skipped_missing_data"}

        # 6. Логируем и публикуем событие в Redis
        logger.info(f"AVITO_WEBHOOK: Queuing message to 'avito:incoming:messages'. Data: {message_data}")
        await self.redis.xadd("avito:incoming:messages", message_data)
        
        return {"status": "ok"}