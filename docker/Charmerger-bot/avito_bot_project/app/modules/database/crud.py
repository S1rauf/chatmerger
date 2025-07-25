# /app/modules/database/crud.py

import logging
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, and_, select, desc, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
# Импорты из нашего проекта
from db_models import User, Transaction, Template, AutoReplyRule, AvitoAccount, MessageLog, ChatNote, ForwardingRule
from shared.database import get_session
from shared.security import encrypt_token
from modules.billing.enums import TariffPlan
import uuid

logger = logging.getLogger(__name__)

# ===================================================================
# Функции для работы с пользователями (User)
# ===================================================================

async def get_or_create_user(
    telegram_id: int, 
    username: Optional[str],
    first_name: Optional[str] = None,
    # --- НОВЫЕ ПАРАМЕТРЫ ---
    with_accounts: bool = False,
    with_tariff: bool = False  # <--- Добавляем флаг для загрузки тарифа
) -> User:
    """
    Находит пользователя или создает нового.
    - with_accounts=True: подгружает связанные Avito-аккаунты.
    - with_tariff=True: подгружает связанный тариф.
    """
    async with get_session() as session:
        query = select(User).where(User.telegram_id == telegram_id)
        
        # --- ИЗМЕНЕНИЯ ЗДЕСЬ ---
        if with_accounts:
            # "Жадно" загружаем связанные аккаунты
            query = query.options(selectinload(User.avito_accounts))
            
        result = await session.execute(query)
        user = result.scalar_one_or_none()
        
        if user is None:
            # ... (логика создания нового пользователя без изменений)
            logger.info(f"Creating new user with telegram_id: {telegram_id}")
            new_user = User(telegram_id=telegram_id, username=username, first_name=first_name)
            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)
            # Если нужны были связанные данные, они будут корректно None или []
            if with_accounts:
                new_user.avito_accounts = []
            if with_tariff:
                # `refresh` не подгрузит `tariff`, поэтому мы должны сделать это вручную
                # после коммита или просто вернуть объект как есть. В данном случае
                # у нового юзера тарифа все равно нет, так что все ок.
                pass 
            return new_user
        else:
            # ... (логика обновления username без изменений)
            needs_commit = False
            if username is not None and user.username != username:
                user.username = username
                needs_commit = True
            if first_name is not None and user.first_name != first_name:
                user.first_name = first_name
                needs_commit = True
            
            if needs_commit:
                logger.info(f"Updating data for user {telegram_id}")
                await session.commit()
                
            return user

# ===================================================================
# Функции для работы с аккаунтами Avito (AvitoAccount)
# ===================================================================

async def add_or_update_avito_account(
    session: AsyncSession, # <--- ПРИНИМАЕМ СЕССИЮ КАК АРГУМЕНТ
    user_id: int, 
    avito_user_id: int, 
    tokens_data: dict
) -> AvitoAccount:
    """Добавляет новый или обновляет существующий аккаунт Avito для пользователя в рамках переданной сессии."""
    
    # Убираем `async with get_session() as session:`, так как сессия уже передана.
    
    result = await session.execute(
        select(AvitoAccount).where(AvitoAccount.avito_user_id == avito_user_id)
    )
    account = result.scalar_one_or_none()
    
    encrypted_access = encrypt_token(tokens_data['access_token'])
    encrypted_refresh = encrypt_token(tokens_data['refresh_token'])
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens_data['expires_in'])
    
    if account:
        logger.info(f"Updating existing Avito account {avito_user_id} for user {user_id}")
        account.user_id = user_id
        account.encrypted_oauth_token = encrypted_access
        account.encrypted_refresh_token = encrypted_refresh
        account.expires_at = expires_at
        account.is_active = True
    else:
        logger.info(f"Creating new Avito account {avito_user_id} for user {user_id}")
        account = AvitoAccount(
            user_id=user_id,
            avito_user_id=avito_user_id,
            encrypted_oauth_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            expires_at=expires_at,
            is_active=True
        )
        session.add(account)
        
    # Коммит будет выполнен в вызывающей функции (в `root`), здесь только добавляем в сессию
    await session.flush() # flush, чтобы получить ID и другие данные до коммита
    await session.refresh(account)
    return account

async def get_user_avito_accounts(telegram_id: int) -> List[AvitoAccount]:
    """Находит все Avito-аккаунты пользователя. Использует selectinload для оптимизации."""
    async with get_session() as session:
        # Эта функция теперь не нужна, так как get_or_create_user делает то же самое,
        # но для консистентности оставим ее с оптимизацией.
        result = await session.execute(
            select(AvitoAccount)
            .join(User)
            .where(User.telegram_id == telegram_id)
            .options(selectinload(AvitoAccount.user)) # Подгружаем связанного пользователя
        )
        return result.scalars().all()

async def get_avito_account_by_id(account_id: int) -> Optional[AvitoAccount]:
    """Находит один аккаунт Avito по его внутреннему ID."""
    async with get_session() as session:
        return await session.get(AvitoAccount, account_id)

async def set_avito_account_alias(account_id: int, alias: str) -> Optional[AvitoAccount]:
    """Устанавливает псевдоним для аккаунта Avito."""
    async with get_session() as session:
        account = await session.get(AvitoAccount, account_id)
        if account:
            account.alias = alias
            await session.commit()
            return account
        return None

async def toggle_avito_account_active_status(account_id: int, is_active: bool) -> Optional[AvitoAccount]:
    """Изменяет статус активности аккаунта Avito."""
    async with get_session() as session:
        account = await session.get(AvitoAccount, account_id)
        if account:
            account.is_active = is_active
            await session.commit()
            return account
        return None

async def delete_avito_account(account_id: int) -> bool:
    """Полностью удаляет аккаунт Avito из базы данных."""
    async with get_session() as session:
        account = await session.get(AvitoAccount, account_id)
        if account:
            await session.delete(account)
            await session.commit()
            return True
        return False

# ===================================================================
# Функции для работы с заметками к чатам (ChatNote)
# ===================================================================

async def get_note_for_chat(account_id: int, chat_id: str) -> Optional[ChatNote]:
    """Находит заметку для чата, подгружая информацию об авторе."""
    async with get_session() as session:
        result = await session.execute(
            select(ChatNote)
            .where(and_(ChatNote.account_id == account_id, ChatNote.chat_id == chat_id))
            .options(selectinload(ChatNote.author)) # Подгружаем связанного пользователя-автора
        )
        return result.scalar_one_or_none()

async def upsert_note_for_chat(account_id: int, chat_id: str, text: str, author_id: int) -> Optional[ChatNote]:
    """Создает или обновляет заметку для конкретного автора. Если текст пустой - удаляет."""
    if not text.strip():
        # Логика удаления теперь должна быть явной
        await delete_note_for_chat(account_id, chat_id, author_id) # <--- Передаем author_id
        return None

    async with get_session() as session:
        stmt = insert(ChatNote).values(
            account_id=account_id, chat_id=chat_id, text=text, author_id=author_id
        )
        # ---!!! ИЗМЕНЯЕМ УСЛОВИЕ КОНФЛИКТА !!!---
        stmt = stmt.on_conflict_do_update(
            index_elements=['account_id', 'chat_id', 'author_id'], # <-- Теперь 3 поля
            set_={'text': stmt.excluded.text} # Обновляем только текст
        ).returning(ChatNote)
        # ------------------------------------------
        
        result = await session.execute(stmt)
        await session.commit()
        upserted_note = result.scalar_one()
        logger.info(f"Upserted note for chat {chat_id} by user {author_id}")
        return upserted_note

async def delete_note_for_chat(account_id: int, chat_id: str, author_id: int) -> bool: # <--- Добавляем author_id
    """Удаляет заметку конкретного автора для конкретного чата."""
    async with get_session() as session:
        result = await session.execute(
            select(ChatNote).where(and_(
                ChatNote.account_id == account_id, 
                ChatNote.chat_id == chat_id,
                ChatNote.author_id == author_id # <--- Уточняем, чью заметку удалять
            ))
        )
        note_to_delete = result.scalar_one_or_none()
        if note_to_delete:
            await session.delete(note_to_delete)
            await session.commit()
            logger.info(f"Deleted note for chat {chat_id} by author {author_id}")
            return True
        return False

# ===================================================================
# Функции для статистики и логов (MessageLog)
# ===================================================================

async def get_account_stats(account_id: int) -> dict:
    """Считает статистику по сообщениям для аккаунта за сегодня и за неделю."""
    async with get_session() as session:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())

        today_stats_query = (
            select(MessageLog.direction, func.count(MessageLog.id))
            .where(and_(MessageLog.account_id == account_id, MessageLog.timestamp >= today_start))
            .group_by(MessageLog.direction)
        )
        week_stats_query = (
            select(MessageLog.direction, func.count(MessageLog.id))
            .where(and_(MessageLog.account_id == account_id, MessageLog.timestamp >= week_start))
            .group_by(MessageLog.direction)
        )

        today_res = await session.execute(today_stats_query)
        week_res = await session.execute(week_stats_query)
        stats = {"today": {"incoming": 0, "outgoing": 0}, "week": {"incoming": 0, "outgoing": 0}}
        for direction, count in today_res.all():
            stats["today"][direction] = count
        for direction, count in week_res.all():
            stats["week"][direction] = count
        return stats

# ===================================================================
# Функции для начальной загрузки (Tariff, Template)
# ===================================================================

async def update_user_tariff(
    session: AsyncSession, 
    user_id: int, 
    new_plan: TariffPlan, 
    expires_at: Optional[datetime]
) -> Optional[User]:
    """Обновляет ТЕКУЩИЙ тарифный план и дату окончания."""
    user = await session.get(User, user_id)
    if user:
        user.tariff_plan = new_plan
        user.tariff_expires_at = expires_at
        user.next_tariff_plan = None # Сбрасываем запланированный даунгрейд
    return user

async def create_template(session: AsyncSession, template_data: dict) -> Template:
    """Создает новый шаблон, если его еще нет."""
    result = await session.execute(select(Template).where(and_(Template.name == template_data['name'], Template.user_id == None)))
    if result.scalar_one_or_none():
        logger.info(f"Standard template '{template_data['name']}' already exists. Skipping.")
        return
    new_template = Template(**template_data)
    session.add(new_template)
    await session.flush()
    return new_template

async def get_last_outgoing_messages(account_id: int, chat_id: str, limit: int = 3) -> List[MessageLog]:
    async with get_session() as session:
        result = await session.execute(
            select(MessageLog)
            .where(
                and_(
                    MessageLog.account_id == account_id,
                    MessageLog.chat_id == chat_id,
                    MessageLog.direction == 'outgoing',
                    # Теперь `or_` будет работать
                    or_(
                        MessageLog.is_autoreply == True,
                        MessageLog.trigger_name != None
                    )
                )
            )
            .order_by(desc(MessageLog.timestamp))
            .limit(limit)
        )
        return result.scalars().all()

async def get_user_by_id(session: AsyncSession, user_id: int) -> Optional[User]:
    """Находит пользователя по его внутреннему ID."""
    return await session.get(User, user_id)

async def create_transaction_and_update_balance(
    session: AsyncSession,
    user_id: int,
    amount: float,
    description: str
) -> Optional[Transaction]:
    """
    Создает транзакцию и атомарно обновляет баланс пользователя.
    amount может быть положительным (пополнение) или отрицательным (списание).
    """
    # 1. Получаем пользователя с блокировкой для обновления (FOR UPDATE)
    # Это предотвращает "гонки состояний", когда два процесса одновременно меняют баланс
    result = await session.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    user = result.scalar_one_or_none()

    if not user:
        logger.error(f"Attempted to create transaction for non-existent user_id: {user_id}")
        return None

    # 2. Рассчитываем новый баланс
    original_balance = user.balance
    new_balance = original_balance + amount
    
    # Можно добавить проверку, чтобы баланс не ушел в минус, если бизнес-логика этого не допускает
    # if new_balance < 0:
    #     raise ValueError("Balance cannot be negative.")

    # 3. Обновляем баланс пользователя
    user.balance = new_balance
    
    # 4. Создаем запись о транзакции
    new_transaction = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        amount=amount,
        balance_after=new_balance,
        description=description,
    )
    session.add(new_transaction)
    
    # Сессия будет закоммичена в get_session(), если не было ошибок
    logger.info(f"Transaction created for user {user_id}. Amount: {amount}. Balance before: {original_balance}, after: {new_balance}")
    
    return new_transaction

async def schedule_user_downgrade(
    session: AsyncSession, 
    user_id: int, 
    next_plan: TariffPlan
) -> Optional[User]:
    """Планирует даунгрейд на следующий период."""
    user = await session.get(User, user_id)
    if user:
        user.next_tariff_plan = next_plan
    return user

async def get_user_transactions(session: AsyncSession, user_id: int, limit: int = 20) -> List[Transaction]:
    """Возвращает последние транзакции пользователя."""
    result = await session.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(desc(Transaction.timestamp))
        .limit(limit)
    )
    return result.scalars().all()

async def update_user_timezone(session: AsyncSession, user_id: int, new_timezone: str) -> Optional[User]:
    """
    Обновляет часовой пояс пользователя в базе данных.
    """
    # Получаем пользователя по его primary key
    user = await session.get(User, user_id)
    
    if user:
        # Присваиваем новое значение полю timezone
        user.timezone = new_timezone
        logger.info(f"Timezone for user {user_id} updated to '{new_timezone}'.")
        # Коммит произойдет автоматически при выходе из контекстного менеджера `get_session`
    else:
        logger.warning(f"Attempted to update timezone for non-existent user_id: {user_id}")
        
    return user


async def full_user_reset(session: AsyncSession, user_id: int) -> bool:
    """
    Полностью удаляет пользователя и все связанные с ним данные.
    Предполагается, что в моделях настроено каскадное удаление (ondelete="CASCADE").
    """
    user = await session.get(User, user_id)
    
    if user:
        # SQLAlchemy автоматически удалит связанные записи в других таблицах
        # (AvitoAccount, Transaction, Template, ChatNote и т.д.),
        # если в моделях в ForeignKey указано ondelete="CASCADE".
        await session.delete(user)
        logger.warning(f"FULL RESET: All data for user_id {user_id} is being deleted.")
        return True
        
    logger.warning(f"Attempted to perform full reset for non-existent user_id: {user_id}")
    return False

async def get_user_templates(session: AsyncSession, user_id: int) -> List[Template]:
    """Возвращает все шаблоны, созданные пользователем."""
    result = await session.execute(
        select(Template).where(Template.user_id == user_id).order_by(Template.name)
    )
    return result.scalars().all()

async def create_user_template(session: AsyncSession, user_id: int, name: str, text: str) -> Template:
    """Создает новый шаблон для пользователя."""
    new_template = Template(user_id=user_id, name=name, text=text)
    session.add(new_template)
    await session.flush() # Чтобы получить ID до коммита
    return new_template

async def update_user_template(session: AsyncSession, template_id: int, user_id: int, name: str, text: str) -> Optional[Template]:
    """Обновляет существующий шаблон, проверяя, что он принадлежит пользователю."""
    template = await session.get(Template, template_id)
    if template and template.user_id == user_id:
        template.name = name
        template.text = text
        return template
    return None

async def delete_user_template(session: AsyncSession, template_id: int, user_id: int) -> bool:
    """Удаляет шаблон, проверяя, что он принадлежит пользователю."""
    template = await session.get(Template, template_id)
    if template and template.user_id == user_id:
        await session.delete(template)
        return True
    return False
    
async def get_user_autoreplies(session: AsyncSession, user_id: int) -> List[AutoReplyRule]:
    """Возвращает все правила автоответов пользователя, связанные через Avito-аккаунты."""
    result = await session.execute(
        select(AutoReplyRule)
        .join(AutoReplyRule.account)
        .where(AvitoAccount.user_id == user_id)
        .order_by(AutoReplyRule.name)
    )
    return result.scalars().all()

# === CRUD ДЛЯ АВТООТВЕТОВ (Пример) ===
async def get_autoreplies_for_account(session: AsyncSession, account_id: int, user_id: int) -> List[AutoReplyRule]:
    """Возвращает правила автоответов для конкретного аккаунта, проверяя права владельца."""
    result = await session.execute(
        select(AutoReplyRule)
        .join(AutoReplyRule.account)
        .where(AutoReplyRule.account_id == account_id, AvitoAccount.user_id == user_id)
        .order_by(AutoReplyRule.name)
    )
    return result.scalars().all()

async def create_autoreply_rule(session: AsyncSession, account_id: int, data: dict) -> AutoReplyRule:
    """Создает новое правило автоответа."""
    new_rule = AutoReplyRule(
        id=uuid.uuid4(),
        account_id=account_id,
        name=data['name'],
        trigger_type=data['match_type'],
        trigger_keywords=data.get('trigger_keywords'),
        reply_text=data['reply_text'],
        delay_seconds=data.get('delay_seconds', 0),
        cooldown_seconds=data.get('cooldown_seconds', 3600),
        is_active=data.get('is_active', True)
    )
    session.add(new_rule)
    await session.flush()
    return new_rule

async def update_autoreply_rule(session: AsyncSession, rule_id: uuid.UUID, data: dict) -> Optional[AutoReplyRule]:
    """Обновляет существующее правило."""
    rule = await session.get(AutoReplyRule, rule_id)
    if rule:
        rule.name = data['name']
        rule.trigger_type = data['match_type']
        rule.trigger_keywords = data.get('trigger_keywords')
        rule.reply_text = data['reply_text']
        rule.delay_seconds = data.get('delay_seconds', 0)
        rule.is_active = data.get('is_active', True)
    return rule

async def delete_autoreply_rule(session: AsyncSession, rule_id: uuid.UUID) -> bool:
    """Удаляет правило."""
    rule = await session.get(AutoReplyRule, rule_id)
    if rule:
        await session.delete(rule)
        return True
    return False

# Также нам понадобится функция для проверки прав доступа
async def check_account_ownership(session: AsyncSession, account_id: int, user_id: int) -> bool:
    """Проверяет, принадлежит ли Avito-аккаунт указанному пользователю."""
    account = await session.get(AvitoAccount, account_id)
    return account is not None and account.user_id == user_id


# === CRUD ДЛЯ ПРАВИЛ ПЕРЕСЫЛКИ (Пример) ===
async def create_forwarding_rule(
    session: AsyncSession, owner_id: int, rule_name: str, password: Optional[str]
) -> ForwardingRule:
    """Создает 'слот' для помощника (правило) с паролем."""
    new_rule = ForwardingRule(
        owner_id=owner_id,
        custom_rule_name=rule_name,
        invite_password=password
    )
    session.add(new_rule)
    return new_rule

async def get_forwarding_rules_for_owner(session: AsyncSession, owner_id: int) -> List[ForwardingRule]:
    """Возвращает правила, созданные владельцем."""
    result = await session.execute(
        select(ForwardingRule)
        .where(ForwardingRule.owner_id == owner_id)
        # Мы больше не можем подгрузить source_account напрямую,
        # так как эта связь теперь неявная (через JSON поле permissions).
        # Информацию об аккаунтах нужно будет получать отдельно.
    )
    return result.scalars().all()

async def get_forwarding_rule_by_invite_code(session: AsyncSession, invite_code: str) -> Optional[ForwardingRule]:
    """Находит правило по коду-приглашению."""
    result = await session.execute(
        select(ForwardingRule).where(ForwardingRule.invite_code == invite_code)
    )
    return result.scalar_one_or_none()

async def accept_forwarding_rule(session: AsyncSession, rule_id: uuid.UUID) -> Optional[ForwardingRule]:
    """Помечает правило как принятое."""
    rule = await session.get(ForwardingRule, rule_id)
    if rule:
        rule.is_accepted = True
    return rule
    
async def delete_forwarding_rule(session: AsyncSession, rule_id: uuid.UUID, owner_id: int) -> bool:
    """Удаляет правило, проверяя права владельца."""
    rule = await session.get(ForwardingRule, rule_id)
    if rule and rule.owner_id == owner_id:
        await session.delete(rule)
        return True
    return False

async def accept_forwarding_invite(
    session: AsyncSession, rule_id: uuid.UUID, target_telegram_id: int
) -> Optional[ForwardingRule]:
    """Привязывает помощника к правилу."""
    rule = await session.get(ForwardingRule, rule_id)
    if rule and rule.target_telegram_id is None: # Можно принять только "пустой" инвайт
        rule.target_telegram_id = target_telegram_id
        return rule
    return None

async def update_forwarding_permissions(
    session: AsyncSession, 
    rule_id: uuid.UUID, 
    owner_id: int, 
    new_permissions: dict
) -> Optional[ForwardingRule]:
    """Обновляет JSON-поле с правами для правила."""
    rule = await session.get(ForwardingRule, rule_id)
    if rule and rule.owner_id == owner_id:
        current_permissions = rule.permissions or {}
        current_permissions.update(new_permissions)
        rule.permissions = current_permissions
        # Сообщаем SQLAlchemy, что JSON-поле было изменено,
        # чтобы он гарантированно сохранил его в БД.
        flag_modified(rule, "permissions")
        return rule
    return None

# === CRUD ДЛЯ АДМИН-ПАНЕЛИ ===

async def get_all_users_for_admin(session: AsyncSession, limit: int = 100, offset: int = 0) -> List[User]:
    """Возвращает список всех пользователей с пагинацией."""
    result = await session.execute(
        select(User).order_by(desc(User.created_at)).limit(limit).offset(offset)
    )
    return result.scalars().all()

async def admin_update_user_tariff(
    session: AsyncSession, 
    user_id: int, 
    new_plan: TariffPlan, 
    new_expires_at: Optional[datetime]
) -> Optional[User]:
    """Административная функция для смены тарифа пользователю."""
    user = await session.get(User, user_id)
    if user:
        user.tariff_plan = new_plan
        user.tariff_expires_at = new_expires_at
        logger.info(f"ADMIN ACTION: Tariff for user {user_id} changed to {new_plan.value}")
    return user

async def get_all_notes_for_chat(session: AsyncSession, account_id: int, chat_id: str) -> List[ChatNote]:
    """Возвращает все заметки для чата, сразу подгружая авторов."""
    result = await session.execute(
        select(ChatNote)
        .where(ChatNote.account_id == account_id, ChatNote.chat_id == chat_id)
        .options(selectinload(ChatNote.author)) # "Жадно" грузим авторов
    )
    return result.scalars().all()