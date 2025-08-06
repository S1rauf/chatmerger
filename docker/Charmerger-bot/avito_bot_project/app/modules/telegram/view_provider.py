import logging
import json
from typing import Optional, Dict, Any
import asyncio
import redis.asyncio as redis
from db_models import User, AvitoAccount
from modules.database.crud import get_all_notes_for_chat
from modules.avito.client import AvitoAPIClient
from .view_models import ChatViewModel
from shared.database import get_session

logger = logging.getLogger(__name__)
VIEW_TTL_SECONDS = 60 * 60 * 24 * 3  # 3 дня
VIEW_KEY_TPL = "chat_view:{account_id}:{chat_id}"

async def rehydrate_view_model(
    redis_client: redis.Redis,
    account: AvitoAccount,
    chat_id: str
) -> Optional[ChatViewModel]:
    """
    Загружает "фоновую" информацию о чате: данные участников, объявления, заметки,
    а также подписчиков и лог ответов из предыдущей версии модели в Redis.
    НЕ ЗАГРУЖАЕТ информацию о последнем сообщении.
    """
    view_key = VIEW_KEY_TPL.format(account_id=account.id, chat_id=chat_id)
    logger.info(f"Rehydrating base model for {view_key}.")

    try:
        api_client = AvitoAPIClient(account)
        chat_info = await api_client.get_chat_info(chat_id)

        interlocutor = next(
            (user for user in chat_info.get("users", []) if str(user.get("id")) != str(account.avito_user_id)),
            {}
        )
        item_context = chat_info.get("context", {}).get("value", {})
        
        async with get_session() as session:
            db_notes = await get_all_notes_for_chat(session, account.id, chat_id)
        
        notes_dict = {
            str(note.author.telegram_id): {
                "author_name": note.author.first_name or note.author.username or f"ID {note.author.telegram_id}",
                "text": note.text,
                "timestamp": int(note.updated_at.timestamp())
            } for note in db_notes if note.author
        }

        # Формируем базовую модель БЕЗ информации о последнем сообщении
        base_model: ChatViewModel = {
            "view_version": 13,
            "account_id": account.id, "account_alias": account.alias, "chat_id": chat_id,
            "interlocutor_name": interlocutor.get("name", "Собеседник"),
            "interlocutor_id": interlocutor.get("id"),
            "is_blocked": interlocutor.get("blocked", False),
            "item_title": item_context.get("title", "Объявление"),
            "item_price_string": item_context.get("price_string"),
            "item_url": item_context.get("url"),
            "notes": notes_dict,
            "subscribers": {},
            "action_log": []
        }

        # Сливаем со старой моделью, чтобы не потерять подписчиков и лог ответов
        current_model_json = await redis_client.get(view_key)
        if current_model_json:
            current_model = json.loads(current_model_json)
            base_model["subscribers"] = current_model.get("subscribers", {})
            base_model["action_log"] = current_model.get("action_log", [])
        
        return base_model

    except Exception as e:
        logger.error(f"Failed to rehydrate view model for {view_key}: {e}", exc_info=True)
        return None

async def subscribe_user_to_view(
    redis_client: redis.Redis,
    view_key: str,
    telegram_id: int,
    message_id: int
):
    """Добавляет пользователя и ID его сообщения в подписчики общей модели."""
    model_json = await redis_client.get(view_key)
    if not model_json:
        # Если модели нет, то подписываться не на что.
        # Этого не должно происходить, если мы всегда сначала создаем модель.
        logger.warning(f"Cannot subscribe to non-existent view: {view_key}")
        return

    model: ChatViewModel = json.loads(model_json)
    
    # Получаем словарь подписчиков, если его нет - создаем
    subscribers = model.setdefault("subscribers", {})
    
    # Если пользователь уже подписан с другим сообщением, нужно найти и удалить старую подписку
    # Этого сценария нужно избегать, но на всякий случай обработаем
    for sub_tg_id, sub_msg_id in list(subscribers.items()):
        if int(sub_tg_id) == telegram_id and sub_msg_id != message_id:
            # Нашли старую подписку этого же юзера, но с другим message_id
            # Это значит, что он открыл карточку в новом сообщении, не закрыв старую.
            # Для простоты можно просто перезаписать.
            pass # В данном случае просто перезапись ниже решит проблему

    # Добавляем или перезаписываем подписку
    subscribers[str(telegram_id)] = message_id
    
    # Сохраняем обновленную модель
    await redis_client.set(view_key, json.dumps(model), keepttl=True)
    logger.info(f"User {telegram_id} subscribed to {view_key} with message {message_id}")

async def unsubscribe_user_from_view(redis_client: redis.Redis, view_key: str, telegram_id: int):
    """Удаляет пользователя из подписчиков."""
    model_json = await redis_client.get(view_key)
    if model_json:
        model: ChatViewModel = json.loads(model_json)
        # Безопасно удаляем подписчика, если он есть
        if str(telegram_id) in model.get("subscribers", {}):
            del model["subscribers"][str(telegram_id)]
            await redis_client.set(view_key, json.dumps(model), keepttl=True)
            logger.info(f"User {telegram_id} unsubscribed from {view_key}")
