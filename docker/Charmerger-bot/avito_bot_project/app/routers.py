# /app/routers.py
import logging
from typing import Optional
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import RedirectResponse, HTMLResponse
from aiogram.utils.keyboard import InlineKeyboardBuilder
# Импорты из нашего проекта
from modules.avito.auth import AvitoOAuth
from modules.avito.webhook import AvitoWebhookHandler
from modules.avito.client import AvitoAPIClient
from modules.telegram.bot import process_telegram_update, WEBHOOK_PATH, bot
from modules.database import crud
from shared.database import get_session
from db_models import User, AvitoAccount
from shared.security import encrypt_token
from shared.config import settings
from modules.user_actions.onboarding import check_and_send_terms_agreement

logger = logging.getLogger(__name__)

# Этот роутер будет зарегистрирован в main.py
api_router = APIRouter()

# --- Эндпоинт для старта OAuth-авторизации ---
@api_router.get("/connect/avito", tags=["Avito OAuth"])
async def get_avito_connect_url(request: Request, user_id: int):
    """
    Генерирует URL авторизации Avito и немедленно перенаправляет на него пользователя.
    """
    try:
        oauth_manager = AvitoOAuth(redis_client=request.app.state.redis)
        avito_auth_url = await oauth_manager.get_authorization_url(internal_user_id=user_id)
        return RedirectResponse(url=avito_auth_url)
    except Exception as e:
        logger.error(f"Ошибка генерации URL-адреса авторизации для пользователя {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not generate authorization URL.")

# --- Корневой эндпоинт, который также обрабатывает коллбэк ---
@api_router.get("/", tags=["Root"])
async def root(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None
):
    """
    Корневой эндпоинт. 
    - Если пришли code и state, обрабатывает OAuth коллбэк.
    - Иначе, просто проверяет, что сервис работает.
    """
    if code and state:
        logger.info("Обработка обратного вызова Avito OAuth на корневой конечной точке...")
        oauth_manager = AvitoOAuth(redis_client=request.app.state.redis)
        
        try:
            # 1. Обмениваем code на токены
            result = await oauth_manager.exchange_code_for_tokens(code=code, state=state)
            internal_user_id = result['internal_user_id']
            tokens_data = result['tokens_data']
            
            # 2. Получаем ID пользователя Avito через дополнительный запрос
            temp_account_obj = AvitoAccount(
                user_id=internal_user_id, avito_user_id=0,
                encrypted_oauth_token=encrypt_token(tokens_data['access_token']),
                encrypted_refresh_token=encrypt_token(tokens_data['refresh_token']),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=tokens_data['expires_in'])
            )
            api_client = AvitoAPIClient(account=temp_account_obj)
            user_info = await api_client.get_own_user_info()
            avito_user_id = user_info['id']
            logger.info(f"Идентификатор пользователя Avito успешно получен: {avito_user_id}")
            
            # 3. Сохраняем/обновляем аккаунт и отправляем соглашение в ОДНОЙ ТРАНЗАКЦИИ
            async with get_session() as session:
                # 3.1. Получаем пользователя, чтобы проверить его статус
                db_user = await session.get(User, internal_user_id)
                if not db_user:
                    raise HTTPException(status_code=404, detail="User not found during OAuth callback")
                
                # 3.2. Добавляем или обновляем Avito аккаунт
                db_account = await crud.add_or_update_avito_account(
                    session=session,
                    user_id=internal_user_id,
                    avito_user_id=avito_user_id,
                    tokens_data=tokens_data
                )

                # 3.3. Подписываемся на вебхуки
                final_api_client = AvitoAPIClient(account=db_account)
                avito_webhook_url = f"{settings.webapp_base_url}/webhook/avito"
                await final_api_client.subscribe_to_webhook(webhook_url=avito_webhook_url)
                
                # 3.4. Проверяем, нужно ли отправить Пользовательское Соглашение
                await check_and_send_terms_agreement(
                    user=db_user,
                    session=session,
                    redis_client=request.app.state.redis
                )
                
                # Коммитим все изменения в БД, сделанные в этой сессии
                await session.commit()

            # 4. Показываем пользователю красивую HTML-страницу об успехе
            html_content = """
            <html><head><title>Успешно!</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;background-color:#f0f2f5;margin:0}.card{background-color:white;padding:40px;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.1);text-align:center}h1{color:#2ecc71}p{color:#555}</style></head>
            <body><div class="card"><h1>✅ Успешно!</h1><p>Аккаунт Avito подключен. Можете закрыть эту вкладку и вернуться в Telegram.</p></div></body></html>
            """
            return HTMLResponse(content=html_content)

        except ValueError as e:
            logger.warning(f"Проверка состояния OAuth не удалась: {e}")
            error_html = "<html><body><h1>🤔 Ошибка авторизации</h1><p>Похоже, ваша сессия авторизации устарела...</p></body></html>"
            return HTMLResponse(content=error_html, status_code=400)
        except Exception as e:
            logger.error(f"Критическая ошибка обработки обратного вызова OAuth на корне: {e}", exc_info=True)
            error_html = "<html><body><h1>❌ Ошибка</h1><p>Произошла непредвиденная ошибка...</p></body></html>"
            return HTMLResponse(content=error_html, status_code=500)
    else:
        # Этот блок остается без изменений
        logger.info(
            f"ROOT_ENDPOINT_HIT: Получен запрос на URL: {request.url}. "
            f"Клиент: {request.client.host}:{request.client.port}. "
            f"Заголовки: {dict(request.headers)}"
        )
        return {"status": "ok", "service": "Avito Bot Project"}


# --- Эндпоинт для вебхуков Avito ---
@api_router.post("/webhook/avito", tags=["Avito Webhook"])
async def avito_webhook_endpoint(request: Request, x_signature: Optional[str] = Header(None)):
    try:
        # СОЗДАЕМ ОБРАБОТЧИК ЗДЕСЬ, ПО ТРЕБОВАНИЮ
        webhook_handler = AvitoWebhookHandler(redis_client=request.app.state.redis)
        await webhook_handler.handle_request(request, x_signature)
        return {"status": "ok"}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Обработка ошибок вебхука Avito: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process webhook.")


# --- Эндпоинт для вебхуков Telegram ---
@api_router.post(WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook_endpoint(update: dict):
    await process_telegram_update(update)
    return {"status": "ok"}
