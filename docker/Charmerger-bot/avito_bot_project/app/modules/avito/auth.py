# /app/modules/avito/auth.py
import secrets
import httpx
from urllib.parse import urlencode
import redis.asyncio as redis 

from shared.config import settings

class AvitoOAuth:
    """
    Управляет процессом OAuth 2.0 авторизации для Avito.
    """
    AUTH_URL = "https://www.avito.ru/oauth"
    TOKEN_URL = "https://api.avito.ru/token"

    # ДОБАВЛЯЕМ КОНСТРУКТОР
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client 

    async def get_authorization_url(self, internal_user_id: int) -> str:
        """
        Генерирует уникальный URL для пользователя.
        """
        state = f"user_{internal_user_id}_{secrets.token_hex(12)}"
        await self.redis.set(f"avito:oauth:state:{state}", str(internal_user_id), ex=600)

        # 1. Задаем scope как одну строку с запятыми
        scopes = "messenger:read,messenger:write,user:read"

        params = {
            "response_type": "code",
            "client_id": settings.avito_client_id,
            "redirect_uri": settings.avito_redirect_uri,
            "scope": scopes, # Используем нашу строку
            "state": state,
        }
        
        # 2. Собираем URL вручную, чтобы избежать лишнего кодирования scope
        query_string = urlencode(params)
        
        return f"{self.AUTH_URL}?{query_string}"

    async def exchange_code_for_tokens(self, code: str, state: str) -> dict:
        """
        Обменивает авторизационный код на токены.
        """
        redis_key = f"avito:oauth:state:{state}"
        # ИСПОЛЬЗУЕМ КЛИЕНТ ИЗ КОНСТРУКТОРА
        internal_user_id = await self.redis.get(redis_key)
        if not internal_user_id:
            raise ValueError("Invalid or expired 'state' parameter. Authorization timed out.")
        
        # ИСПОЛЬЗУЕМ КЛИЕНТ ИЗ КОНСТРУКТОРА
        await self.redis.delete(redis_key)

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.avito_client_id,
            "client_secret": settings.avito_client_secret,
            "redirect_uri": settings.avito_redirect_uri,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.TOKEN_URL, data=data)
            response.raise_for_status()
            tokens = response.json()
            
            return {
                "internal_user_id": int(internal_user_id),
                "tokens_data": tokens
            }