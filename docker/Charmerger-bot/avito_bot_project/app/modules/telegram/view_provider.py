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
    chat_id: str,
    *,  # <-- Этот символ делает все следующие аргументы только именованными
    is_new_message: bool = False 
) -> Optional[ChatViewModel]:
    """
    Обновляет модель представления, запрашивая информацию о чате и 
    отдельно - самое последнее сообщение, чтобы гарантировать актуальность.
    При этом СОХРАНЯЕТ существующий `action_log`, подписчиков и статус прочтения из Redis.
    """
    view_key = VIEW_KEY_TPL.format(account_id=account.id, chat_id=chat_id)
    logger.info(f"Rehydrating/updating model for {view_key} with fresh last message.")

    try:
        api_client = AvitoAPIClient(account)
        
        # --- НОВАЯ ЛОГИКА: ДВА ПАРАЛЛЕЛЬНЫХ ЗАПРОСА ---
        # Запрашиваем информацию о чате и историю сообщений одновременно для скорости
        chat_info, messages_data = await asyncio.gather(
            api_client.get_chat_info(chat_id),
            api_client.get_messages(chat_id, limit=1) # Запрашиваем только 1 самое последнее сообщение
        )

        # ---!!! ФИНАЛЬНЫЙ ОТЛАДОЧНЫЙ ЛОГ !!!---
        # Мы хотим увидеть, что именно находится в chat_info.get("users", [])
        logger.critical(
            f"[DEBUG-INTERLOCUTOR] RAW DATA for chat {chat_id}: \n"
            f"My Avito User ID: {account.avito_user_id}\n"
            f"Users from API: {chat_info.get('users')}"
        )

        # 1. Обрабатываем общую информацию о чате
        interlocutor = {} # По умолчанию - пустой словарь
        users_from_api = chat_info.get("users", [])
        my_avito_id = account.avito_user_id

        for user_data in users_from_api:
            # Сравниваем, приведя оба ID к строкам, на всякий случай
            if str(user_data.get("id")) != str(my_avito_id):
                interlocutor = user_data
                break # Нашли собеседника, выходим из цикла

        # Логируем результат поиска
        logger.critical(
             f"[DEBUG-INTERLOCUTOR] Found interlocutor object: {interlocutor}"
        )

        item_context = chat_info.get("context", {}).get("value", {})
        
        # 2. Обрабатываем самое последнее сообщение из отдельного запроса
        messages_list = messages_data.get("messages", [])
        last_message = messages_list[0] if messages_list else {}
        
        # 3. Получаем свежие заметки из нашей БД
        async with get_session() as session:
            db_notes = await get_all_notes_for_chat(session, account.id, chat_id)
            logger.info(f"REHYDRATE for chat {chat_id}: Found {len(db_notes)} notes in DB.")
        
        notes_dict = {
            str(note.author.telegram_id): {
                "author_name": note.author.first_name or note.author.username or f"ID {note.author.telegram_id}",
                "text": note.text,
                "timestamp": int(note.updated_at.timestamp())
            } for note in db_notes if note.author
        }

        # 4. Формируем "базовую" модель из всех свежих данных
        base_model: ChatViewModel = {
            "view_version": 10, 
            "account_id": account.id, 
            "account_alias": account.alias, 
            "chat_id": chat_id,
            "interlocutor_name": interlocutor.get("name", "Собеседник"),
            "interlocutor_id": interlocutor.get("id"),
            "is_blocked": interlocutor.get("blocked", False),
            "item_title": item_context.get("title", "Объявление"),
            "item_price_string": item_context.get("price_string"),
            "item_url": item_context.get("url"),
            "notes": notes_dict,
            
            # --- Используем данные из `get_messages` ---
            "last_message_text": last_message.get("content", {}).get("text", "В этом чате пока нет сообщений."),
            "last_message_direction": last_message.get("direction"),
            "last_message_timestamp": last_message.get("created"),
            "is_last_message_read": last_message.get("is_read", True),
            
            # Поля, которые мы будем переносить из старой модели
            "subscribers": {},
            "action_log": []
        }

        # 5. Пытаемся получить ТЕКУЩУЮ модель из Redis, чтобы не потерять важные данные
        current_model_json = await redis_client.get(view_key)
        if current_model_json:
            logger.info(f"Found existing model for {view_key}. Merging data.")
            current_model = json.loads(current_model_json)
            # Переносим подписчиков, лог действий и, опционально, статус прочтения
            base_model["subscribers"] = current_model.get("subscribers", {})
            if not is_new_message:
                base_model["action_log"] = current_model.get("action_log", [])
            # Если в старой модели стоял флаг `False`, а новое сообщение его не изменило, сохраняем `False`
            if not current_model.get("is_last_message_read", True):
                 base_model["is_last_message_read"] = False

        # 6. Сохраняем итоговую, объединенную модель в Redis
        await redis_client.set(view_key, json.dumps(base_model), ex=VIEW_TTL_SECONDS)
        
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
