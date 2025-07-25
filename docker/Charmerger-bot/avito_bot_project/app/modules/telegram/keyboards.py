# /app/modules/telegram/keyboards.py

import logging
from typing import List, Optional
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram import types
from shared.config import settings
from db_models import User, Template
from modules.billing.config import TARIFF_CONFIG, DEPOSIT_OPTIONS_KOP
from modules.billing.enums import TariffPlan
from shared.config import SUPPORT_FAQ

logger = logging.getLogger(__name__)

# --- ГЛАВНАЯ КЛАВИАТУРА ТЕПЕРЬ INLINE ---
def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Создает и возвращает ИНЛАЙН-клавиатуру главного меню.
    """
    builder = InlineKeyboardBuilder()
    
    panel_url = f"{settings.webapp_base_url}/panel"
    logger.info(f"KEYBOARD_BUILDER: Creating Inline WebApp button with URL: {panel_url}")
    builder.button(text="👤 Мои Avito аккаунты", callback_data="navigate:accounts_list")
    builder.button(text="⚙️ Открыть Панель Управления", web_app=WebAppInfo(url=panel_url))
    builder.button(text="⭐ Тарифы и Кошелек", callback_data="navigate:wallet_and_tariffs") 
    builder.button(text="💬 Поддержка", callback_data="navigate:support")
    
    builder.adjust(1) # Каждая кнопка на новой строке
    
    return builder.as_markup()

def get_avito_accounts_menu(accounts: List["AvitoAccount"]) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру для управления Avito-аккаунтами.
    """
    builder = InlineKeyboardBuilder()
    
    # Схема расположения кнопок
    layout = []

    # 1. Добавляем кнопки для каждого существующего аккаунта
    if accounts:
        for acc in accounts:
            account_name = acc.alias or f"ID: {acc.avito_user_id}"
            status_icon = "🟢" if acc.is_active else "🔴"
            
            builder.button(text=f"➡️ {status_icon} {account_name}", callback_data=f"avito_acc:select:{acc.id}")
            builder.button(text="✏️ Псевдоним", callback_data=f"avito_acc:rename:{acc.id}")
            
            if acc.is_active:
                builder.button(text="🗑 Отключить", callback_data=f"avito_acc:disable:{acc.id}")
            else:
                builder.button(text="✅ Включить", callback_data=f"avito_acc:enable:{acc.id}")
            
            # Добавляем в нашу схему: 1 кнопка в первом ряду, 2 во втором
            layout.extend([1, 2])

    # 2. Всегда добавляем кнопки "Добавить" и "Назад"
    if not accounts:
        # Если аккаунтов нет, текст на кнопке другой
        builder.button(text="➕ Добавить первый аккаунт", callback_data="avito_acc:add_new")
    else:
        builder.button(text="➕ Добавить еще Avito аккаунт", callback_data="avito_acc:add_new")
        
    builder.button(text="⬅️ Назад в главное меню", callback_data="navigate:main_menu")

    # Добавляем в нашу схему: эти 2 кнопки будут каждая на новой строке
    layout.extend([1, 1])

    # 3. Применяем всю схему расположения разом
    builder.adjust(*layout)
    
    return builder.as_markup()


def get_single_account_menu(account: "AvitoAccount") -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру для управления ОДНИМ конкретным аккаунтом Avito.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Чаты", callback_data=f"account_actions:chats:{account.id}")
    builder.button(text="📊 Статистика", callback_data=f"account_actions:stats:{account.id}")
    builder.button(text="❌ Отвязать аккаунт", callback_data=f"account_actions:unbind_confirm:{account.id}")
    builder.button(text="⬅️ Назад к списку аккаунтов", callback_data="navigate:accounts_list")
    
    # Располагаем кнопки: по одной на строку, для наглядности
    builder.adjust(1)
    
    return builder.as_markup()

    
def build_chats_list_keyboard(
    chats: list, 
    account_id: int, 
    offset: int,
    limit: int
) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру со списком чатов и кнопками пагинации.
    """
    builder = InlineKeyboardBuilder()
    
    # Добавляем кнопку для каждого чата
    for chat in chats:
        interlocutor_name = "Неизвестно"
        for user in chat.get("users", []):
            if not user.get("is_self"):
                interlocutor_name = user.get("name", "Неизвестно")
                break
        
        item_title = chat.get("context", {}).get("value", {}).get("title", "Объявление")
        item_title_short = (item_title[:30] + '..') if len(item_title) > 30 else item_title
        
        builder.button(
            text=f"🗣️ {interlocutor_name} | {item_title_short}",
            callback_data=f"chat:show:{account_id}:{chat['id']}"
        )
    
    # --- Пагинация ---
    pagination_buttons = []
    if offset > 0:
        prev_offset = max(0, offset - limit)
        pagination_buttons.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"chats:list:{account_id}:{prev_offset}")
        )
        
    if len(chats) == limit:
        next_offset = offset + limit
        pagination_buttons.append(
            InlineKeyboardButton(text="Вперед ▶️", callback_data=f"chats:list:{account_id}:{next_offset}")
        )
        
    if pagination_buttons:
        builder.row(*pagination_buttons)
        
    # Кнопка "Назад" в меню аккаунта
    builder.button(
        text="⬅️ К меню аккаунта", 
        callback_data=f"avito_acc:select:{account_id}"
    )

    builder.adjust(1) # Каждый чат на новой строке
    return builder.as_markup()

def get_wallet_menu_keyboard(user: User) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру для меню "Кошелек и Тарифы".
    """
    builder = InlineKeyboardBuilder()

    # Добавляем кнопки
    builder.button(text="💳 Пополнить баланс", callback_data="wallet:deposit")
    builder.button(text="📈 Посмотреть/Сменить тариф", callback_data="navigate:tariffs_list")
    
    # Кнопка назад в главное меню
    builder.button(text="⬅️ Назад в главное меню", callback_data="navigate:main_menu")

    builder.adjust(1) # Каждая кнопка на новой строке

    return builder.as_markup()


from modules.billing.enums import TariffPlan
from modules.billing.config import TARIFF_CONFIG


def get_tariffs_list_keyboard(current_user_plan: TariffPlan) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру для списка тарифов.
    Предлагает кнопки для перехода на тарифы, которые не являются текущими.
    """
    builder = InlineKeyboardBuilder()

    # Проходим по всем тарифам и предлагаем перейти на те, что не являются текущими
    for plan_enum, config in TARIFF_CONFIG.items():
        # Не предлагаем перейти на текущий тариф и на бесплатный
        if plan_enum != current_user_plan and plan_enum != TariffPlan.START:
            button_text = f"🚀 Перейти на «{config['name_readable']}»"
            builder.button(text=button_text, callback_data=f"billing:purchase:{plan_enum.value}")
    
    # Всегда добавляем кнопку "Пополнить баланс"
    builder.button(text="💳 Пополнить баланс", callback_data="wallet:deposit")
    
    # Кнопка назад в меню кошелька
    builder.button(text="⬅️ Назад", callback_data="navigate:wallet_and_tariffs")

    builder.adjust(1) # Каждая кнопка на новой строке
    return builder.as_markup()

def get_deposit_options_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру с вариантами сумм для пополнения."""
    builder = InlineKeyboardBuilder()
    for amount_kop in DEPOSIT_OPTIONS_KOP:
        amount_rub = amount_kop / 100
        builder.button(text=f"{amount_rub:.0f} ₽", callback_data=f"deposit:amount:{amount_kop}")
    
    builder.button(text="⬅️ Назад", callback_data="navigate:wallet_and_tariffs")
    builder.adjust(2, 2, 1, 1) # Красиво располагаем кнопки
    return builder.as_markup()

def get_templates_for_chat_keyboard(templates: List[Template], account_id: int, chat_id: str) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру со списком шаблонов для отправки в чат.
    """
    builder = InlineKeyboardBuilder()
    
    if templates:
        for tpl in templates:
            tpl_name = (tpl.name[:25] + '..') if len(tpl.name) > 25 else tpl.name
            builder.button(
                text=f"📄 {tpl_name}", 
                callback_data=f"template:send:{tpl.id}:{account_id}:{chat_id}"
            )
        # Располагаем по 2 шаблона в ряд
        builder.adjust(2, repeat=True)
    else:
        # Если шаблонов нет, эта кнопка будет не нужна, но оставим для единообразия
        builder.button(text="У вас нет шаблонов. Создайте в WebApp.", callback_data="navigate:webapp_info")

    builder.row(types.InlineKeyboardButton(
        text="⬅️ Назад к чату", 
        callback_data=f"chat:show:{account_id}:{chat_id}"
    ))
    # ---

    return builder.as_markup()

def get_support_menu_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для меню поддержки с кнопками FAQ."""
    builder = InlineKeyboardBuilder()
    
    # Создаем кнопку для каждого вопроса из конфига
    for key, question_html in SUPPORT_FAQ.items():
        # Берем только текст вопроса (до первого \n) и убираем теги
        question_text = question_html.split('\n')[0].replace('<b>', '').replace('</b>', '').replace('❓ ', '')
        builder.button(text=f"❓ {question_text}", callback_data=f"support:faq:{key}")
        
    builder.button(text="✍️ Написать администратору", callback_data="support:contact_admin")
    builder.button(text="⬅️ В главное меню", callback_data="navigate:main_menu")
    
    builder.adjust(1) # Каждая кнопка на новой строке
    return builder.as_markup()