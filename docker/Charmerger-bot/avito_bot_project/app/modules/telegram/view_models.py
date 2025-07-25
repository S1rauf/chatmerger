# /app/modules/telegram/view_models.py

from typing import TypedDict, Optional, List, Dict, Any, Literal

ActionType = Literal["manual_reply", "template_reply", "auto_reply"]

class ActionHistoryItem(TypedDict):
    type: str
    name: str
    text: str

class ActionLogItem(TypedDict, total=False):
    """Модель для одного элемента в логе действий."""
    type: ActionType      # 'manual_reply', 'template_reply', 'auto_reply'
    author_name: str      # Имя того, кто совершил действие
    text: str             # Текст ответа
    template_name: str    # Имя шаблона (только для type='template_reply')
    rule_name: str        # Имя правила (только для type='auto_reply')
    timestamp: int        # Время действия

class ChatViewModel(TypedDict, total=False):
    """
    Типизированный словарь для хранения ОБЩЕГО состояния карточки чата в Redis.
    """
    # Идентификаторы
    view_version: int
    account_id: int
    chat_id: str
    account_alias: Optional[str]
    
    # Словарь подписчиков: { "telegram_id": message_id }
    subscribers: Dict[str, int]

    # Данные для отображения
    interlocutor_name: str
    interlocutor_id: int
    is_blocked: bool
    item_title: str
    last_message_text: Optional[str]
    last_message_direction: Optional[str]
    is_last_message_read: bool
    last_message_timestamp: Optional[int]

    # Данные объявления
    item_price_string: Optional[str]
    item_url: Optional[str]

    # Заметки от разных пользователей
    # { "telegram_id": {"author_name": "...", "text": "...", "timestamp": ...} }
    notes: Dict[str, Dict[str, Any]]
    
    # История действий
    action_history: Optional[List[ActionHistoryItem]]