# /app/modules/webapp/security.py
import hmac
import hashlib
from urllib.parse import unquote
import json
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from shared.config import settings
from modules.database.crud import get_or_create_user
from db_models import User

def _validate_telegram_data(init_data: str) -> Optional[dict]:
    """Валидирует initData, полученные от Telegram."""
    try:
        # Разбираем строку на параметры
        params = dict(p.split('=', 1) for p in init_data.split('&'))
        # Хэш, присланный Telegram
        received_hash = params.pop('hash')
        
        # Формируем строку для проверки
        data_check_string = "\n".join(f"{k}={unquote(v)}" for k, v in sorted(params.items()))
        
        # Секретный ключ, созданный из токена бота
        secret_key = hmac.new("WebAppData".encode(), settings.telegram_bot_token.encode(), hashlib.sha256).digest()
        # Наш рассчитанный хэш
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if calculated_hash == received_hash:
            # Если хэши совпадают, данные подлинные. Возвращаем данные пользователя.
            return json.loads(unquote(params['user']))
        return None
    except Exception:
        return None

async def get_current_webapp_user(request: Request) -> User:
    """
    Зависимость FastAPI для защиты эндпоинтов WebApp.
    Проверяет заголовок X-Telegram-Init-Data.
    """
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-Telegram-Init-Data header")

    user_data = _validate_telegram_data(init_data)
    if not user_data:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Telegram InitData")

    # Получаем пользователя и сразу просим загрузить его тариф
    user = await get_or_create_user(
        telegram_id=user_data['id'],
        username=user_data.get('username'),
        first_name=user_data.get('first_name')
    )
    return user

async def get_admin_user(current_user: User = Depends(get_current_webapp_user)) -> User:
    """
    Зависимость, которая проверяет, является ли текущий пользователь админом.
    Пропускает дальше, только если ID совпадают.
    """
    if not settings.telegram_admin_id or current_user.telegram_id != settings.telegram_admin_id:
        raise HTTPException(status_code=403, detail="Доступ запрещен: требуются права администратора.")
    return current_user