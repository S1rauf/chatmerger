# src/modules/avito/client.py
import httpx
import logging
from typing import Optional, List
from datetime import datetime, timedelta, timezone

# --- ИЗМЕНЯЕМ ИМПОРТ ЗДЕСЬ ---
from db_models import AvitoAccount

# Остальные импорты, которые уже должны быть
from shared.config import settings
from shared.database import get_session
from shared.security import encrypt_token, decrypt_token
from shared.redis_client import redis_client
from shared.exceptions import AvitoAPIError

logger = logging.getLogger(__name__)

class AvitoAPIClient:
    """
    Базовый клиент, который умеет обновлять токены и предоставлять заголовки для авторизации.
    """
    BASE_URL = "https://api.avito.ru"
    TOKEN_URL = f"{BASE_URL}/token"

    def __init__(self, account: AvitoAccount):
        self.account = account
        self.http_client = httpx.AsyncClient(base_url=self.BASE_URL)

    async def _refresh_access_token(self):
        """Обновляет access_token с помощью refresh_token."""
        logger.info(f"Refreshing token for Avito account {self.account.id}")
        refresh_token = decrypt_token(self.account.encrypted_refresh_token)

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.avito_client_id,
            "client_secret": settings.avito_client_secret,
        }
        
        try:
            response = await self.http_client.post(self.TOKEN_URL, data=data)
            response.raise_for_status()
            new_tokens = response.json()

            # Обновляем данные в объекте и в базе
            self.account.encrypted_oauth_token = encrypt_token(new_tokens['access_token'])
            self.account.encrypted_refresh_token = encrypt_token(new_tokens['refresh_token'])
            # Используем now(timezone.utc) для консистентности
            self.account.expires_at = datetime.now(timezone.utc) + timedelta(seconds=new_tokens['expires_in'])

            async with get_session() as session:
                session.add(self.account)
                await session.commit()
            
            logger.info(f"Token for Avito account {self.account.id} refreshed successfully.")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in [400, 401]:
                # Деактивируем аккаунт
                self.account.is_active = False
                async with get_session() as session:
                    session.add(self.account)
                    await session.commit()
                
                # >>> КЛЮЧЕВОЕ ИЗМЕНЕНИЕ <<<
                # Публикуем событие о необходимости переавторизации
                await redis_client.xadd("system:notifications", {
                    "type": "reauth_needed",
                    "account_id": str(self.account.id),
                    "text": f"Требуется повторная авторизация для аккаунта Avito {self.account.avito_user_id}!"
                })
                logger.error(f"Token refresh failed for account {self.account.id}. It was deactivated and notification was queued.")
            raise

    async def get_auth_headers(self) -> dict:
        """
        Проверяет срок жизни токена, обновляет его если нужно, и возвращает заголовки.
        """
        # Проверяем, не истек ли токен (с запасом в 5 минут)
        if self.account.expires_at < datetime.now(timezone.utc) + timedelta(minutes=5):
            await self._refresh_access_token()

        access_token = decrypt_token(self.account.encrypted_oauth_token)
        logger.info(f"DEBUG: Using Access Token: {access_token}")
        return {"Authorization": f"Bearer {access_token}"}

    async def get_own_user_info(self) -> dict:
        """
        Получает информацию о владельце токена (аккаунта).
        Используется для получения avito_user_id после OAuth.
        """
        headers = await self.get_auth_headers()
        response = await self.http_client.get(
            "/core/v1/accounts/self",
            headers=headers
        )
        response.raise_for_status()
        return response.json()
    
    async def subscribe_to_webhook(self, webhook_url: str) -> dict:
        """
        Подписывает текущий аккаунт на получение вебхуков на указанный URL
        с использованием актуального эндпоинта v3.
        """
        logger.info(f"Subscribing account {self.account.id} to webhook URL: {webhook_url}")
        headers = await self.get_auth_headers()
        payload = {"url": webhook_url}
        
        # Используем правильный эндпоинт v3
        response = await self.http_client.post(
            "/messenger/v3/webhook",
            headers=headers,
            json=payload
        )
        
        response_data = response.json()
        
        # Обработка успешного ответа
        if response.status_code in (200, 201) and not response_data.get('error'):
            logger.info(f"Successfully subscribed account {self.account.id} to webhook.")
            return response_data
        
        # Если дошли сюда, значит что-то пошло не так. Логируем и вызываем ошибку.
        logger.error(
            f"Failed to subscribe to webhook for account {self.account.id}. "
            f"Status: {response.status_code}, Response: {response_data}"
        )
        response.raise_for_status() # Вызовет HTTPStatusError для 4xx/5xx статусов
        
        # Эта строка нужна на случай, если статус был 2xx, но не 200/201, 
        # и при этом не было ошибки в теле. Маловероятно, но покрывает все случаи.
        return response_data

    async def get_chat_info(self, chat_id: str) -> dict:
        """
        Получает информацию о чате, включая его участников (использует API v2).
        """
        logger.info(f"Requesting v2 chat info for chat_id: {chat_id}")
        headers = await self.get_auth_headers()
        
        url = f"/messenger/v2/accounts/{self.account.avito_user_id}/chats/{chat_id}"
        
        try:
            response = await self.http_client.get(url, headers=headers)
            response.raise_for_status()
            chat_data = response.json()
            
            logger.debug(f"Successfully retrieved chat info: {chat_data}")
            return chat_data
            
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP error {e.response.status_code} for chat {chat_id}"
            logger.error(f"{error_msg}: {e.response.text}")
            raise AvitoAPIError(error_msg) from e
            
        except Exception as e:
            logger.exception(f"Unexpected error getting chat info: {str(e)}")
            raise AvitoAPIError(f"Unexpected error getting chat info: {str(e)}") from e
    
    async def get_chats(self, limit: int = 100, offset: int = 0) -> dict:
        """
        Получает список чатов для текущего аккаунта с пагинацией, используя API v2,
        и ИМИТИРУЕТ ответ API v1, добавляя поле 'total' с общим количеством чатов.
        """
        logger.info(f"Requesting v2 chats for account {self.account.id} with limit={limit}, offset={offset}")
        headers = await self.get_auth_headers()
        
        # 1. Сначала делаем основной запрос с параметрами, которые пришли в функцию
        base_url = f"/messenger/v2/accounts/{self.account.avito_user_id}/chats"
        params = {"limit": limit, "offset": offset}
        response = await self.http_client.get(base_url, headers=headers, params=params)
        response.raise_for_status()
        main_response_data = response.json()
        
        # 2. Если пользователь запросил первую страницу (offset=0),
        #    и нам нужно узнать общее количество, то запускаем подсчет.
        #    Параметр `limit=1` от `api_sync_avito_chats` также попадет сюда.
        if offset == 0:
            total_count = 0
            current_offset = 0
            page_limit = 100
            
            while True:
                params_for_count = {"limit": page_limit, "offset": current_offset}
                count_response = await self.http_client.get(base_url, headers=headers, params=params_for_count)
                count_response.raise_for_status()
                count_data = count_response.json()
                
                chats_on_page = count_data.get("chats", [])
                num_on_page = len(chats_on_page)
                total_count += num_on_page
                
                # Если пришло меньше, чем мы запрашивали, значит, это последняя страница
                if num_on_page < page_limit:
                    break
                
                current_offset += page_limit
                
                # Ограничение, чтобы не уйти в бесконечный цикл, если что-то пойдет не так
                if current_offset >= 1000: # API v1 имел лимит в 1000
                    logger.warning(f"Chat count for account {self.account.id} exceeds 1000, stopping count.")
                    break
            
            # 3. Добавляем поле 'total' в основной ответ, который мы вернем
            main_response_data['total'] = total_count

        # Возвращаем основной ответ, который теперь содержит 'chats' с запрошенной страницы
        # и, возможно, 'total' (если offset был 0).
        return main_response_data


    async def mark_chat_as_read(self, chat_id: str):
        """Помечает чат как прочитанный (API v1)."""
        logger.info(f"Marking chat {chat_id} as read for account {self.account.id}")
        headers = await self.get_auth_headers()
        url = f"/messenger/v1/accounts/{self.account.avito_user_id}/chats/{chat_id}/read"
        
        try:
            response = await self.http_client.post(url, headers=headers)
            response.raise_for_status()
            logger.info(f"Successfully marked chat {chat_id} as read.")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error marking chat as read: {e.response.text}")
            raise AvitoAPIError(f"Failed to mark chat as read: {e.response.status_code}") from e

    async def block_user_in_chat(self, chat_id: str, user_to_block_id: int, item_id: Optional[int]):
        """Блокирует пользователя в контексте чата (API v2)."""
        logger.info(f"Blocking user {user_to_block_id} in chat {chat_id} for account {self.account.id}")
        headers = await self.get_auth_headers()
        url = f"/messenger/v2/accounts/{self.account.avito_user_id}/blacklist"
        
        # Собираем payload согласно документации
        payload = {
            "users": [{
                "user_id": user_to_block_id,
                "context": {"item_id": item_id, "reason_id": 4} # reason_id=4: "другая причина"
            }]
        }
        # Убираем item_id, если его нет
        if not item_id:
            del payload["users"][0]["context"]["item_id"]
            
        try:
            response = await self.http_client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            logger.info(f"Successfully blocked user {user_to_block_id}.")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error blocking user: {e.response.text}")
            raise AvitoAPIError(f"Failed to block user: {e.response.status_code}") from e

    async def unblock_user(self, blocked_user_id: int):
        """Разблокирует пользователя (API v2)."""
        logger.info(f"Unblocking user {blocked_user_id} for account {self.account.id}")
        headers = await self.get_auth_headers()
        url = f"/messenger/v2/accounts/{self.account.avito_user_id}/blacklist/{blocked_user_id}"
        
        try:
            response = await self.http_client.delete(url, headers=headers)
            response.raise_for_status()
            logger.info(f"Successfully unblocked user {blocked_user_id}.")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error unblocking user: {e.response.text}")
            raise AvitoAPIError(f"Failed to unblock user: {e.response.status_code}") from e
    
    async def get_messages(self, chat_id: str, limit: int = 25) -> dict:
        """
        Получает историю сообщений для конкретного чата, используя API v3.
        Сообщения отсортированы от новых к старым.
        """
        logger.info(f"Requesting v3 messages for chat_id: {chat_id} with limit={limit}")
        headers = await self.get_auth_headers()
        
        url = f"/messenger/v3/accounts/{self.account.avito_user_id}/chats/{chat_id}/messages/"
        params = {"limit": limit}
        
        try:
            response = await self.http_client.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP error {e.response.status_code} getting messages for chat {chat_id}"
            logger.error(f"{error_msg}: {e.response.text}")
            raise AvitoAPIError(error_msg) from e
    
    async def get_voice_files(self, voice_ids: List[str]) -> dict:
        """
        Получает временные ссылки на файлы голосовых сообщений по их ID, используя API v1.
        """
        logger.info(f"Requesting v1 voice files for voice_ids: {voice_ids}")
        headers = await self.get_auth_headers()
        
        # API ожидает ID как строку, разделенную запятыми
        params = {"voice_ids": ",".join(voice_ids)}
        
        url = f"/messenger/v1/accounts/{self.account.avito_user_id}/getVoiceFiles"
        
        try:
            response = await self.http_client.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP error {e.response.status_code} getting voice files"
            logger.error(f"{error_msg}: {e.response.text}")
            raise AvitoAPIError(error_msg) from e