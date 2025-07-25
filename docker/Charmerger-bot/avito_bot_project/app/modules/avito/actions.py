# src/modules/avito/actions.py
import logging
from .client import AvitoAPIClient
from typing import List

logger = logging.getLogger(__name__)

class AvitoChatActions:
    def __init__(self, client: AvitoAPIClient):
        self.client = client
        self.base_url = f"/messenger/v1/accounts/{client.account.avito_user_id}"

    async def mark_as_read(self, chat_id: str):
        """Помечает все сообщения в чате как прочитанные."""
        logger.info(f"ДЕЙСТВИЯ: Отмечаю чат {chat_id} как прочитанный." )
        try:
            headers = await self.client.get_auth_headers()
            response = await self.client.http_client.post(
                f"{self.base_url}/chats/{chat_id}/read",
                headers=headers,
            )
            response.raise_for_status()
            logger.info(f"ДЕЙСТВИЯ: Чат {chat_id} отмечен как прочитанный.")
        except Exception as e:
            logger.warning(f"ДЕЙСТВИЯ: Не удалось отметить чат {chat_id} как прочитанный. Причина: {e}")

    async def block_chat(self, chat_id: str):
        """Блокирует чат (собеседника)."""
        headers = await self.client.get_auth_headers()
        response = await self.client.http_client.post(
            f"{self.base_url}/chats/{chat_id}/block",
            headers=headers,
        )
        response.raise_for_status()
