# src/modules/avito/messaging.py
from .client import AvitoAPIClient
from typing import Optional

class AvitoMessaging:
    def __init__(self, client: AvitoAPIClient):
        self.client = client
        self.base_url = f"/messenger/v1/accounts/{client.account.avito_user_id}"

    async def send_text_message(self, chat_id: str, text: str) -> dict:
        """Отправляет текстовое сообщение в указанный чат."""
        headers = await self.client.get_auth_headers()
        payload = {"message": {"text": text}, "type": "text"}
        
        response = await self.client.http_client.post(
            f"{self.base_url}/chats/{chat_id}/messages",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        return response.json()

    async def upload_image(self, image_bytes: bytes) -> dict:
        """Загружает изображение на серверы Avito и возвращает его ID."""
        headers = await self.client.get_auth_headers()
        response = await self.client.http_client.post(
            f"/messenger/v1/accounts/{self.client.account.avito_user_id}/images",
            headers=headers,
            content=image_bytes
        )
        response.raise_for_status()
        return response.json()

    async def send_image_message(self, chat_id: str, image_id: str, text: Optional[str] = None):
        """Отправляет сообщение с ранее загруженным изображением."""
        headers = await self.client.get_auth_headers()
        payload = {"message": {"image_id": image_id}, "type": "image"}
        if text:
            payload["message"]["text"] = text

        response = await self.client.http_client.post(
            f"{self.base_url}/chats/{chat_id}/messages",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        return response.json()