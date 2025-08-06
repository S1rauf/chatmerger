# /app/modules/billing/config.py

from typing import Dict, Any, List
from .enums import TariffPlan

DEPOSIT_OPTIONS_KOP: List[int] = [10000, 30000, 50000, 100000, 200000]

TARIFF_CONFIG: Dict[TariffPlan, Dict[str, Any]] = {
    TariffPlan.START: {
        "name_readable": "Старт",
        "price_rub": 0,
        "duration_days": None,
        "description_short": "Базовый функционал для ознакомления.",
     
        "features_html": [
            "✅ 1 аккаунт Avito",
            "✅ До 10 исходящих ответов/день в Avito",
            "✅ 1 шаблон ответов",
            "✅ 1 правило автоответа",
            "✅ 1 заметка к чату (на аккаунт)",
            "✅ Ответы из Telegram",
            "❌ Пересылка сообщений",
            "❌ Расширенная аналитика",
            "❌ Приоритетная поддержка"
        ],
    
        "limits": {
            "avito_accounts": 1,
            "daily_outgoing_messages_tg_to_avito": 10,
            "templates": 1,
            "auto_reply_rules": 1,
            "forwarding_rules": 0,
            "chat_notes": 1,
            "analytics_access": "none",
            "can_reply_from_tg": True,
        }
    },
    TariffPlan.PRO: {
        "name_readable": "PRO",
        "price_rub": 290,
        "duration_days": 30,
        "description_short": "Для активных продавцов и небольших команд.",
   
        "features_html": [
            "✅ До 3 аккаунтов Avito",
            "✅ <b>Безлимитные</b> ответы в Avito",
            "✅ 15 шаблонов ответов",
            "✅ 5 правил автоответа",
            "✅ До 10 заметок к чатам (на аккаунт)",
            "✅ Пересылка сообщений 2 помощникам (только чтение)",
            "✅ Базовая аналитика",
        ],

        "limits": {
            "avito_accounts": 3,
            "daily_outgoing_messages_tg_to_avito": float('inf'),
            "templates": 15,
            "auto_reply_rules": 5,
            "forwarding_rules": 2,
            "forwarding_can_reply": False,
            "chat_notes": 10,
            "analytics_access": "basic",
            "can_reply_from_tg": True,
        }
    },
    TariffPlan.EXPERT: {
        "name_readable": "Эксперт",
        "price_rub": 790,
        "duration_days": 30,
        "description_short": "Максимум для профессионалов и агентств.",
   
        "features_html": [
            "✅ До 30 аккаунтов Avito",
            "✅ <b>Безлимитные</b> ответы в Avito",
            "✅ 50 шаблонов ответов",
            "✅ 50 правил автоответа",
            "✅ <b>Безлимитные</b> заметки к чатам",
            "✅ Пересылка сообщений 10 помощникам <b>с правом ответа</b>",
            "✅ Полная аналитика",
            "✅ Приоритетная поддержка"
        ],

        "limits": {
            "avito_accounts": 30,
            "daily_outgoing_messages_tg_to_avito": float('inf'),
            "templates": 50,
            "auto_reply_rules": 50,
            "forwarding_rules": 10,
            "forwarding_can_reply": True,
            "chat_notes": float('inf'),
            "analytics_access": "full",
            "can_reply_from_tg": True,
            "priority_support": True,
        }
    }
}