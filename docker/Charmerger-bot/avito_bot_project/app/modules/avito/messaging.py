# /app/modules/avito/messaging.py
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
        
        # Формат multipart/form-data с полем 'uploadfile[]'
        files = {'uploadfile[]': ('image.jpg', image_bytes, 'image/jpeg')}
        
        # Правильный URL из документации: /uploadImages
        response = await self.client.http_client.post(
            f"/messenger/v1/accounts/{self.client.account.avito_user_id}/uploadImages",
            headers=headers,
            files=files
        )
        response.raise_for_status()
        return response.json()

    async def send_image_message(self, chat_id: str, image_id: str, text: Optional[str] = None):
        """
        Отправляет сообщение с ранее загруженным изображением.
        ВНИМАНИЕ: API v1 для отправки изображений по ID не поддерживает подписи (caption).
        Если `text` передан, он будет отправлен отдельным сообщением.
        """
        headers = await self.client.get_auth_headers()
        
        # 1. Отправляем изображение
        image_payload = {"image_id": image_id}
        image_url = f"{self.base_url}/chats/{chat_id}/messages/image"
        
        response = await self.client.http_client.post(
            image_url,
            headers=headers,
            json=image_payload
        )
        response.raise_for_status()
        
        # 2. Если была подпись, отправляем ее следующим сообщением
        if text:
            await self.send_text_message(chat_id, text)
            
        return response.json()