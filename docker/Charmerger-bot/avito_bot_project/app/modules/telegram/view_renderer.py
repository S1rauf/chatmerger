import logging
import json
import html
from datetime import datetime
from typing import Optional

# --- ИЗМЕНЕНИЕ: Добавляем pytz и select из sqlalchemy ---
import pytz
from sqlalchemy import select

from aiogram import Bot, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ParseMode
import redis.asyncio as redis

from .view_models import ChatViewModel
from db_models import User
# --- ИЗМЕНЕНИЕ: Убираем get_or_create_user, он нам тут не нужен ---
from .view_provider import unsubscribe_user_from_view
# --- ИЗМЕНЕНИЕ: Импортируем get_session для запроса к БД ---
from shared.database import get_session

logger = logging.getLogger(__name__)


class ViewRenderer:
    def __init__(self, bot: Bot, redis_client: redis.Redis):
        self.bot = bot
        self.redis = redis_client

    def _build_keyboard(self, model: ChatViewModel, for_telegram_id: int) -> InlineKeyboardBuilder:
        """Собирает клавиатуру на основе модели для конкретного пользователя."""
        builder = InlineKeyboardBuilder()
        account_id = model['account_id']
        chat_id = model['chat_id']
        
        is_read_flag = model.get('is_last_message_read', True) # Получаем флаг, по умолчанию True
        
        # ---!!! ОТЛАДОЧНЫЙ ЛОГ №5 !!!---
        logger.critical(
            f"[DEBUG-READ-STATUS] BUILD_KEYBOARD for chat {chat_id} (user: {for_telegram_id}): "
            f"Building button based on is_last_message_read = {is_read_flag}"
        )

        # Логика генерации кнопки на основе флага
        if not is_read_flag:
            # Кнопка "не прочитано"
            builder.button(text="Прочитано ✔️", callback_data=f"chat:mark_read:{account_id}:{chat_id}")
        else:
            # Кнопка "прочитано", но чтобы она не была бесполезной,
            # можно сделать ее неактивной или просто показывать другой текст.
            # callback="chat:noop" - это "no operation", пустая кнопка.
            builder.button(text="Прочитано ✅", callback_data="chat:noop")

        if model.get('is_blocked'):
            builder.button(text="Разблокировать 🔓", callback_data=f"chat:unblock:{account_id}:{chat_id}:{model.get('interlocutor_id')}")
        else:
            builder.button(text="Заблокировать 🔒", callback_data=f"chat:block:{account_id}:{chat_id}:{model.get('interlocutor_id')}")
        
        builder.button(text="Шаблоны 📄", callback_data=f"chat:templates:{account_id}:{chat_id}")
        builder.button(text="Ред. заметку ✏️", callback_data=f"chat:edit_note:{account_id}:{chat_id}")

        builder.adjust(2, 2)
        return builder

    def _build_text(self, model: ChatViewModel, user_timezone_str: str) -> str:
        try:
            user_tz = pytz.timezone(user_timezone_str)
        except pytz.UnknownTimeZoneError:
            user_tz = pytz.timezone("Europe/Moscow")

        # --- Блок 1: Шапка (без изменений) ---
        account_display_name = html.escape(model.get('account_alias') or f"Аккаунт ID {model.get('account_id')}")
        header_text = f"<b>Сообщение с «{account_display_name}»</b>"
        interlocutor_name = html.escape(model.get('interlocutor_name', 'Собеседник'))
        item_title = html.escape(model.get("item_title", "Объявление"))
        item_price = html.escape(model.get("item_price_string", ""))
        header_parts = [header_text, f"👤 <b>От:</b> {interlocutor_name}"]
        if ts := model.get('last_client_message_timestamp'):
            dt_utc = datetime.fromtimestamp(ts, tz=pytz.utc)
            dt_local = dt_utc.astimezone(user_tz)
            header_parts.append(f"⏰ {dt_local.strftime('%d.%m.%Y %H:%M')} ({dt_local.tzname()})")
        item_url = model.get("item_url")
        item_link = f"<a href='{item_url}'>{item_title}</a>" if item_url else item_title
        price_part = f" | <b>{item_price}</b>" if item_price else ""
        header_parts.append(f"📦 <b>Объявление:</b> {item_link}{price_part}")
        header_block = "\n".join(header_parts)

        # --- Блок 2: Сообщение клиента (ИСПРАВЛЕННАЯ ЛОГИКА) ---
        attachment_info = model.get('last_client_message_attachment')
        client_text = model.get('last_client_message_text')
        
        message_content_html = ""

        if attachment_info:
            attachment_type_display = html.escape(attachment_info.get('type', 'вложение'))
            message_content_html = f"<i><b>Получено новое {attachment_type_display}</b> (см. сообщение выше).</i>"
            # Проверяем, есть ли осмысленная подпись (не просто плейсхолдер)
            if client_text and not client_text.startswith(f"[{attachment_type_display.capitalize()}]"):
                message_content_html += f"\n\n<b>Подпись:</b> <blockquote>{html.escape(client_text)}</blockquote>"
        elif client_text:
            message_content_html = f"<blockquote>{html.escape(client_text)}</blockquote>"
        else:
            message_content_html = "<i>(Ожидание нового сообщения от клиента...)</i>"

        message_block = f"💬 <b>Сообщение клиента:</b>\n{message_content_html}"

        # --- Блок 3: Логика обработки action_log (ИСПРАВЛЕННАЯ ВЕРСИЯ) ---
        manual_reply_block = ""
        other_actions_block = ""
        if action_log := model.get('action_log'):
            # Сортируем все записи по времени
            sorted_log = sorted(action_log, key=lambda x: x.get('timestamp', 0), reverse=True)
            
            manual_parts = []
            other_parts = []

            for entry in sorted_log:
                author = html.escape(entry.get("author_name", "..."))
                text = html.escape(entry.get("text", "..."))
                entry_type = entry.get("type")
                
                if entry_type in ["manual_reply", "image_reply"]:
                    dt_utc = datetime.fromtimestamp(entry.get('timestamp', 0), tz=pytz.utc)
                    dt_local = dt_utc.astimezone(user_tz)
                    time_str = dt_local.strftime('%d.%m %H:%M')
                    prefix = f"<b>Ответ от {author}</b> ({time_str}):"
                    manual_parts.append(f"{prefix}\n<blockquote>{text}</blockquote>")

                elif entry_type == "template_reply":
                    tpl_name = html.escape(entry.get("template_name", "..."))
                    prefix = f"📄 <b>Шаблон «{tpl_name}»</b> ({author}):"
                    other_parts.append(f"{prefix}\n<blockquote>{text}</blockquote>")

                elif entry_type == "auto_reply":
                    rule_name = html.escape(entry.get("rule_name", "..."))
                    prefix = f"🤖 <b>Автоответ «{rule_name}»:</b>"
                    other_parts.append(f"{prefix}\n<blockquote>{text}</blockquote>")

            if manual_parts:
                all_manual_text = "\n\n".join(manual_parts)
                manual_reply_block = (f'✍️ <b>Ваши ответы ({len(manual_parts)}):</b>\n<blockquote expandable>{all_manual_text}</blockquote>')
            
            if other_parts:
                other_actions_block = "\n\n".join(other_parts)

        # --- Блок 4: Заметки (ВОССТАНОВЛЕННЫЙ КОД) ---
        notes_block = ""
        if notes := model.get('notes'):
            sorted_notes = sorted(notes.values(), key=lambda x: x.get('timestamp', 0), reverse=True)
            if sorted_notes:
                notes_html_parts = []
                for note in sorted_notes:
                    author = html.escape(note.get('author_name', '...'))
                    text = html.escape(note.get('text', '...'))
                    dt_utc = datetime.fromtimestamp(note.get('timestamp', 0), tz=pytz.utc)
                    dt_local = dt_utc.astimezone(user_tz)
                    date_str = dt_local.strftime('%d.%m.%y %H:%M')
                    notes_html_parts.append(f"<b>От {author}</b> ({date_str})\n{text}")
                all_notes_text = "\n\n".join(notes_html_parts)
                notes_block = (f'📌 <b>Заметки к чату ({len(sorted_notes)}):</b>\n<blockquote expandable>{all_notes_text}</blockquote>')
            
        # --- Финальная Сборка ---
        final_parts = [header_block, message_block]
        if other_actions_block: final_parts.append(other_actions_block)
        if manual_reply_block: final_parts.append(manual_reply_block)
        if notes_block: final_parts.append(notes_block)
        return "\n\n".join(final_parts)

    async def render_new_card(self, model: ChatViewModel, user: User) -> Optional[types.Message]:
        """Отправляет новую карточку конкретному пользователю."""
        telegram_chat_id = user.telegram_id
        
        # Передаем часовой пояс пользователя в _build_text
        text = self._build_text(model, user.timezone)
        keyboard = self._build_keyboard(model, telegram_chat_id).as_markup()
        
        try:
            return await self.bot.send_message(
                chat_id=telegram_chat_id, text=text, reply_markup=keyboard,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"RENDERER: Error sending new card to {telegram_chat_id}: {e}", exc_info=True)
            return None

    async def update_all_subscribers(self, view_key: str, model: ChatViewModel):
        """Обновляет сообщения у всех подписчиков, загружая их таймзоны."""
        subscribers = model.get("subscribers", {})
        if not subscribers:
            logger.info(f"RENDERER: No subscribers for {view_key}. Nothing to update.")
            return
        
        logger.info(f"RENDERER: Updating cards for {len(subscribers)} subscribers of {view_key}")
        
        # 1. Собираем ID всех подписчиков
        subscriber_ids = [int(tg_id) for tg_id in subscribers.keys()]
        if not subscriber_ids: return

        # 2. Загружаем всех пользователей одним запросом к БД
        async with get_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id.in_(subscriber_ids))
            )
            users_map = {user.telegram_id: user for user in result.scalars().all()}
        
        # 3. Проходимся по подписчикам и рендерим для каждого
        for tg_id_str, msg_id in list(subscribers.items()):
            tg_id = int(tg_id_str)
            user = users_map.get(tg_id)
            if not user:
                logger.warning(f"RENDERER: User {tg_id} not found in DB, skipping update.")
                continue

            # Передаем часовой пояс конкретного пользователя в _build_text
            text = self._build_text(model, user.timezone)
            keyboard = self._build_keyboard(model, tg_id).as_markup()
            
            try:
                await self.bot.edit_message_text(
                    text=text, chat_id=tg_id, message_id=msg_id, reply_markup=keyboard,
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
            except TelegramBadRequest as e:
                if "message to edit not found" in e.message or "message can't be edited" in e.message:
                    logger.warning(f"RENDERER: Message {msg_id} for user {tg_id} is gone. Unsubscribing.")
                    await unsubscribe_user_from_view(self.redis, view_key, tg_id)
                elif "message is not modified" not in str(e):
                    pass # Игнорируем эту неопасную ошибку
                else:
                    logger.warning(f"RENDERER: Failed to edit for user {tg_id}: {e.message}")
            except Exception as e:
                logger.error(f"RENDERER: Unexpected error updating for {tg_id}:{msg_id}: {e}", exc_info=True)