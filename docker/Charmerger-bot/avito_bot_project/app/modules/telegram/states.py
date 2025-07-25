# /app/modules/telegram/states.py
from aiogram.fsm.state import State, StatesGroup

class RenameAvitoAccount(StatesGroup):
    """Состояния для процесса переименования аккаунта Avito."""
    waiting_for_new_alias = State()


class EditChatNote(StatesGroup):
    """Состояния для процесса редактирования заметки к чату."""
    waiting_for_note_text = State()


class CreateTemplate(StatesGroup):
    waiting_for_name = State()
    waiting_for_text = State()

class AcceptInvite(StatesGroup):
    waiting_for_password = State()

class TermsAgreement(StatesGroup):
    """Состояние ожидания принятия пользовательского соглашения."""
    waiting_for_agreement = State()

class ContactAdmin(StatesGroup):
    """Состояние для отправки сообщения администратору."""
    waiting_for_message = State()
    waiting_for_admin_reply = State()