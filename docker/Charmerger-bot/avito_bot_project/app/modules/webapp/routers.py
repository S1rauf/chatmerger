# /app/modules/webapp/routers.py
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
# Импорты для аутентификации и бизнес-логики
from .security import get_current_webapp_user
from modules.database import crud
from db_models import User, Template, AutoReplyRule, ForwardingRule, AvitoAccount
from shared.config import settings
from modules.billing.service import billing_service
from modules.billing.config import TARIFF_CONFIG
from modules.billing.enums import TariffPlan
from shared.database import get_session
from modules.wallet.service import wallet_service
from shared.config import POPULAR_TIMEZONES_PYTZ
from modules.billing.exceptions import TariffLimitReachedError
from .security import get_admin_user 
from shared.config import (
    settings, 
    POPULAR_TIMEZONES_PYTZ, 
    USER_AGREEMENT_INTRO_TEXT, 
    USER_AGREEMENT_FULL_TEXT
)

from modules.avito.client import AvitoAPIClient
import uuid

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="modules/webapp/templates")
# --- Настройка ---
router = APIRouter( tags=["WebApp"])

class AdminUserDataUpdate(BaseModel):
    tariff_plan: TariffPlan
    balance: float
    expires_at: Optional[str] = None
    add_to_balance: float = 0.0

class AdminTariffUpdateData(BaseModel):
    user_id: int
    new_plan: TariffPlan
    new_expires_at: Optional[datetime] = None

class TemplateData(BaseModel):
    name: str
    text: str

class AutoReplyData(BaseModel):
    name: str
    match_type: str
    trigger_keywords: Optional[List[str]] = None
    reply_text: str
    delay_seconds: int = 0
    cooldown_seconds: int = 3600
    is_active: bool = True

class ForwardingRuleData(BaseModel):
    custom_rule_name: str
    invite_password: Optional[str] = None

class PermissionsData(BaseModel):
    can_reply: bool
    allowed_accounts: Optional[List[int]] = None

# Добавляем обработчик главной страницы
@router.get("/panel", response_class=HTMLResponse, include_in_schema=False)
async def get_webapp_index(request: Request):
    logger.critical("!!!!!! WEBAPP INDEX HANDLER TRIGGERED !!!!!!")
    return templates.TemplateResponse(
        "index.html", 
        {
            "request": request, 
            "webAppPathPrefix": "/panel",
            "settings": settings 
        }
    )

# ==========================================================
# === API Эндпоинты
# ==========================================================

@router.get("/panel/api/main-status", tags=["WebApp API"])
async def api_get_main_status(current_user: User = Depends(get_current_webapp_user)):
    """Возвращает основной статус для пользователя."""
    auth_url = f"{settings.webapp_base_url}/connect/avito?user_id={current_user.id}"
    # Предполагаем, что у User есть связь .tariff. Если нет, нужна доп. логика.
    user_tariff_plan = billing_service.get_user_tariff_plan(current_user)
    tariff_name = TARIFF_CONFIG[user_tariff_plan]['name_readable']
    tariff_expires_at_display = "" # По умолчанию пустая строка
    if current_user.tariff_expires_at:
        # Форматируем дату в читаемый вид
        expires_date = current_user.tariff_expires_at.strftime("%d.%m.%Y")
        tariff_expires_at_display = f"до {expires_date}"
    elif user_tariff_plan != TariffPlan.START:
        # Если это платный, но бессрочный тариф (маловероятно, но возможно)
        tariff_expires_at_display = "бессрочно"
    # Для бесплатного тарифа "Старт" мы не показываем ничего, строка останется пустой

    full_terms_text = f"{USER_AGREEMENT_INTRO_TEXT}\n\n{USER_AGREEMENT_FULL_TEXT}"

    return {
        "current_tariff_display": tariff_name,
        "tariff_expires_at_display": tariff_expires_at_display,
        "user_balance_rub_str": f"{current_user.balance:.2f} ₽",
        "auth_url": auth_url,
        "has_agreed_to_terms": current_user.has_agreed_to_terms,
        "terms_text": full_terms_text
    }
# --- эндпоинт для принятия соглашения ---
@router.post("/panel/api/user/accept-terms", tags=["WebApp API"])
async def api_accept_terms(current_user: User = Depends(get_current_webapp_user)):
    if not current_user.has_agreed_to_terms:
        async with get_session() as session:
        
            session.add(current_user)
            current_user.has_agreed_to_terms = True
            await session.commit()
            logger.info(f"User {current_user.telegram_id} has agreed to terms via WebApp.")
    return {"success": True}

@router.get("/panel/api/avito-accounts", response_model=List[dict], tags=["WebApp API"])
async def api_get_avito_accounts(current_user: User = Depends(get_current_webapp_user)):
    accounts = await crud.get_user_avito_accounts(current_user.telegram_id)
    return [{
        "id": acc.id,
        "custom_alias": acc.alias,
        "avito_profile_name": f"Профиль {acc.avito_user_id}",
        "avito_user_id": acc.avito_user_id,
        "is_active_tg_setting": acc.is_active,
        "token_status_text": "Активен" if acc.is_active else "Требуется авторизация",
        "token_status_class": "status-ok" if acc.is_active else "status-error",
        "chats_count": acc.chats_count_cache 
    } for acc in accounts]

@router.put("/panel/api/avito-accounts/{account_id}/alias", tags=["WebApp API"])
async def api_set_account_alias(account_id: int, alias_data: dict, current_user: User = Depends(get_current_webapp_user)):
    """Устанавливает псевдоним для аккаунта."""
    alias = alias_data.get("alias")
    updated_account = await crud.set_avito_account_alias(account_id, alias)
    if not updated_account:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    return {"success": True}

@router.delete("/panel/api/avito-accounts/{account_id}", tags=["WebApp API"])
async def api_delete_avito_account(account_id: int, current_user: User = Depends(get_current_webapp_user)):
    """Отключает (удаляет) аккаунт."""
    success = await crud.delete_avito_account(account_id)
    if not success:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    return {"success": True}

@router.get("/panel/api/templates", response_model=List[dict])
async def api_get_user_templates(current_user: User = Depends(get_current_webapp_user)):
    async with get_session() as session:
        templates = await crud.get_user_templates(session, current_user.id)
        return [{"id": t.id, "name": t.name, "text": t.text} for t in templates]

@router.post("/panel/api/templates", response_model=dict)
async def api_create_template(template_data: TemplateData, current_user: User = Depends(get_current_webapp_user)):
    async with get_session() as session:
        try:
            # Вызываем проверку перед созданием
            await billing_service.check_template_limit(current_user, session)
        except TariffLimitReachedError as e:
            # Если лимит достигнут, возвращаем ошибку 403 Forbidden
            raise HTTPException(status_code=403, detail=str(e))

        new_template = await crud.create_user_template(session, current_user.id, template_data.name, template_data.text)
        await session.commit() # Явно коммитим, так как crud не делает этого сам
        return {"success": True, "id": new_template.id}

@router.put("/panel/api/templates/{template_id}", response_model=dict)
async def api_update_template(template_id: int, template_data: TemplateData, current_user: User = Depends(get_current_webapp_user)):
    async with get_session() as session:
        updated = await crud.update_user_template(session, template_id, current_user.id, template_data.name, template_data.text)
    if not updated:
        raise HTTPException(status_code=404, detail="Шаблон не найден или у вас нет прав на его редактирование.")
    return {"success": True}

@router.delete("/panel/api/templates/{template_id}", response_model=dict)
async def api_delete_template(template_id: int, current_user: User = Depends(get_current_webapp_user)):
    async with get_session() as session:
        deleted = await crud.delete_user_template(session, template_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Шаблон не найден или у вас нет прав на его удаление.")
    return {"success": True}

@router.get("/panel/api/autoreplies", response_model=List[dict])
async def api_get_user_autoreplies(current_user: User = Depends(get_current_webapp_user)):
    # Для автоответов и пересылки нужна логика выбора аккаунта,
    # давайте пока сделаем заглушку, чтобы фронтенд не выдавал 404.
    # В будущем здесь будет запрос в БД.
    logger.info(f"Пользователь {current_user.id} запросил автоответы. Возвращаю заглушку.")
    return [ ]

@router.get("/panel/api/forwarding-rules", response_model=List[dict])
async def api_get_forwarding_rules(current_user: User = Depends(get_current_webapp_user)):
    async with get_session() as session:
        rules = await crud.get_forwarding_rules_for_owner(session, current_user.id)
    
    response = []
    for rule in rules:
        target_display_name = f"Пользователь {rule.target_telegram_id}" if rule.target_telegram_id else "Приглашение не принято"
        permissions = rule.permissions or {}
        allowed_accounts = permissions.get("allowed_accounts")
        
        source_display_name = "Все аккаунты"
        if allowed_accounts and isinstance(allowed_accounts, list):
            source_display_name = f"Выбрано аккаунтов: {len(allowed_accounts)}"

        response.append({
            "id": str(rule.id),
            "custom_rule_name": rule.custom_rule_name,
            "target_tg_user_display_name": target_display_name,
            "source_avito_account_display_name": source_display_name,
            "can_reply": permissions.get("can_reply", False),
            "target_user_accepted": rule.target_telegram_id is not None,
            "permissions": permissions
        })
    return response

@router.post("/panel/api/forwarding-rules", response_model=dict)
async def api_create_forwarding_rule(data: ForwardingRuleData, current_user: User = Depends(get_current_webapp_user)):
    async with get_session() as session:
        try:
            await billing_service.check_forwarding_rules_limit(current_user, session)
        except TariffLimitReachedError as e:
            raise HTTPException(status_code=403, detail=str(e))
    
        new_rule = await crud.create_forwarding_rule(
            session=session, 
            owner_id=current_user.id, 
            rule_name=data.custom_rule_name, 
            password=data.invite_password
        )
        await session.commit()
        await session.refresh(new_rule)
    
    invite_link = f"https://t.me/{settings.telegram_bot_username}?start=fw_accept_{new_rule.invite_code}"
    
    return {"success": True, "invite_link": invite_link}

@router.delete("/panel/api/forwarding-rules/{rule_id}", response_model=dict)
async def api_delete_forwarding_rule(rule_id: uuid.UUID, current_user: User = Depends(get_current_webapp_user)):
    async with get_session() as session:
        deleted = await crud.delete_forwarding_rule(session, rule_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=403, detail="Правило не найдено или у вас нет прав.")
    return {"success": True}


@router.get("/panel/api/tariffs", response_model=List[dict])
async def api_get_tariffs(current_user: User = Depends(get_current_webapp_user)):
    """
    Возвращает список всех доступных тарифных планов с ПОЛНОЙ информацией для WebApp.
    """
    available_tariffs = []
    user_current_plan = billing_service.get_user_tariff_plan(current_user)
    
    for plan_enum, config_data in TARIFF_CONFIG.items():
        # Теперь мы передаем полный список фич, а не просто текст
        features_for_frontend = []
        for feature_html in config_data.get('features_html', []):
            is_active = "✅" in feature_html
            # Очищаем текст от HTML-тегов и иконок для чистого отображения
            clean_text = feature_html.replace("✅", "").replace("❌", "").replace("<b>", "").replace("</b>", "").strip()
            features_for_frontend.append({
                "text": clean_text,
                "is_active": is_active
            })

        available_tariffs.append({
            "id": plan_enum.value,
            "name": config_data['name_readable'],
            "price_rub": config_data['price_rub'],
            "duration_days": config_data.get('duration_days'),
            "description": config_data['description_short'],
            "features": features_for_frontend, 
            "is_current": plan_enum == user_current_plan,
        })
        
    return available_tariffs

@router.post("/panel/api/tariffs/purchase")
async def api_purchase_tariff(
    data: dict, 
    request: Request,
    current_user: User = Depends(get_current_webapp_user)
):
    """Обрабатывает покупку тарифа из WebApp."""
    tariff_id = data.get('tariff_id')
    if not tariff_id:
        raise HTTPException(status_code=400, detail="tariff_id is required")

    try:
        tariff_plan = TariffPlan(tariff_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tariff_id")

    # 1. Получаем redis_client из состояния приложения
    redis_client = request.app.state.redis
    
    # 2. Передаем его в сервис
    try:
        # Передаем все три аргумента
        await billing_service.purchase_tariff(current_user, tariff_plan, redis_client)
        return {"success": True, "message": "Тариф успешно приобретен!"}
    
    except (InsufficientFundsError, BillingError) as e:
        # Обрабатываем ошибки биллинга и возвращаем их на фронтенд
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        # Логируем и возвращаем общую ошибку
        logger.error("Критическая ошибка при покупке тарифа через WebApp", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при обработке покупки.")

@router.get("/panel/api/wallet")
async def api_get_wallet_info(request: Request, current_user: User = Depends(get_current_webapp_user)):
    """Возвращает информацию о кошельке: баланс и историю транзакций."""
    
    # Получаем redis_client из состояния приложения
    redis_client = request.app.state.redis
    
    # 1. Получаем баланс через сервис
    balance = await wallet_service.get_balance(current_user.id, redis_client)
    
    # 2. Получаем историю транзакций через CRUD-функцию
    transactions_list = []
    async with get_session() as session:
        db_transactions = await crud.get_user_transactions(session, user_id=current_user.id, limit=30)
        for tx in db_transactions:
            
            # --- ЛОГИКА ФОРМАТИРОВАНИЯ ОПИСАНИЯ ---
            short_description = "Неизвестная операция"
            full_description = tx.description # Сохраняем полное описание для title

            if "Пополнение через Telegram Payments" in tx.description:
                short_description = "Пополнение (Telegram)"
            elif "Оплата тарифа" in tx.description:
                # Можно даже извлечь название тарифа
                try:
                    # Ищем текст в кавычках
                    tariff_name = tx.description.split('«')[1].split('»')[0]
                    short_description = f"Оплата тарифа «{tariff_name}»"
                except IndexError:
                    short_description = "Оплата тарифа"
            
            transactions_list.append({
                "id": str(tx.id), # Добавим ID для будущих нужд
                "created_at_display": tx.timestamp.strftime("%d.%m.%Y %H:%M"),
                "description": short_description, # <-- Используем короткое описание
                "full_description": full_description, # <-- Передаем полное для подсказок
                "amount_rub_str": f"+{tx.amount:.2f} ₽" if tx.amount > 0 else f"{tx.amount:.2f} ₽",
                "balance_after_rub_str": f"{tx.balance_after:.2f} ₽"
            })

    return {
        "current_balance_rub_str": f"{balance:.2f} ₽",
        "transactions": transactions_list
    }

@router.get("/panel/api/user/settings")
async def api_get_user_settings(current_user: User = Depends(get_current_webapp_user)):
    """Возвращает текущие настройки пользователя и доступные опции."""
    current_timezone = current_user.timezone 
    
    return {
        "timezone": current_timezone,
        "available_timezones": POPULAR_TIMEZONES_PYTZ
    }
@router.post("/panel/api/user/settings/timezone")
async def api_save_user_timezone(
    data: dict,
    current_user: User = Depends(get_current_webapp_user)
):
    """Сохраняет новый часовой пояс пользователя."""
    new_timezone = data.get("timezone")
    if not new_timezone or new_timezone not in POPULAR_TIMEZONES_PYTZ:
        raise HTTPException(status_code=400, detail="Invalid timezone selected.")
        
    async with get_session() as session:
        await crud.update_user_timezone(session, current_user.id, new_timezone)
    
    return {"success": True, "message": "Часовой пояс успешно сохранен!"}

# --- POST-ЭНДПОИНТ ДЛЯ ПОЛНОГО СБРОСА ---
@router.post("/panel/api/avito-accounts/full-reset")
async def api_full_user_reset(current_user: User = Depends(get_current_webapp_user)):
    """
    Выполняет полный сброс аккаунта пользователя. Опасная операция!
    """
    async with get_session() as session:
        success = await crud.full_user_reset(session, current_user.id)
    
    if not success:
        raise HTTPException(status_code=404, detail="User not found for reset.")
    
    # TODO: Здесь можно добавить отправку прощального сообщения в Telegram
    # через Redis-очередь, чтобы уведомить пользователя, что все удалено.
    
    return {"success": True}

@router.post("/panel/api/avito-accounts/{account_id}/sync-chats", response_model=dict)
async def api_sync_avito_chats(account_id: int, current_user: User = Depends(get_current_webapp_user)):
    async with get_session() as session:
        acc = await session.get(AvitoAccount, account_id)
        if not acc or acc.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Доступ запрещен")
        
        try:
            api_client = AvitoAPIClient(acc)
            chats_data = await api_client.get_chats(limit=1) 
            chats_count = chats_data.get("total", 0)
            
            # Сохраняем кэшированное значение в БД
            acc.chats_count_cache = chats_count
            await session.commit()
            
            return {"success": True, "message": f"Синхронизировано чатов: {chats_count}", "chats_count": chats_count}
        except Exception as e:
            logger.error(f"Failed to sync chats for account {account_id}: {e}")
            raise HTTPException(status_code=500, detail="Ошибка синхронизации с Avito API.")

@router.get("/panel/api/avito-accounts/{account_id}/autoreplies", response_model=List[dict])
async def api_get_account_autoreplies(account_id: int, current_user: User = Depends(get_current_webapp_user)):
    logger.info(f"[WebApp API] Пользователь {current_user.id} получает автоответы для учетной записи {account_id}.")
    async with get_session() as session:
        # Проверяем, что пользователь имеет доступ к этому аккаунту
        if not await crud.check_account_ownership(session, account_id, current_user.id):
            raise HTTPException(status_code=403, detail="Доступ запрещен")
        
        # Получаем реальные правила из БД
        rules = await crud.get_autoreplies_for_account(session, account_id, current_user.id)
        
        # Формируем ответ
        return [{
            "id": str(r.id),
            "name": r.name,
            "is_active": r.is_active,
            "keywords_list": r.trigger_keywords or [],
            "reply_text": r.reply_text,
            "match_type": r.trigger_type,
            "delay_seconds": r.delay_seconds
        } for r in rules]

@router.post("/panel/api/avito-accounts/{account_id}/autoreplies", response_model=dict)
async def api_create_account_autoreply(account_id: int, data: AutoReplyData, current_user: User = Depends(get_current_webapp_user)):
    async with get_session() as session:
        try:
            await billing_service.check_autoreply_rules_limit(current_user, session)
        except TariffLimitReachedError as e:
            raise HTTPException(status_code=403, detail=str(e))

        if not await crud.check_account_ownership(session, account_id, current_user.id):
            raise HTTPException(status_code=403, detail="Доступ запрещен")
        
        new_rule = await crud.create_autoreply_rule(session, account_id, data.model_dump())
        await session.commit()
        return {"success": True, "id": str(new_rule.id)}

@router.put("/panel/api/autoreplies/{rule_id}", response_model=dict)
async def api_update_account_autoreply(rule_id: uuid.UUID, data: AutoReplyData, current_user: User = Depends(get_current_webapp_user)):
    logger.info(f"[WebApp API] Пользователь {current_user.id} обновляет правило автоответа {rule_id}.")
    async with get_session() as session:
        # Здесь нужна более сложная проверка прав, т.к. мы знаем только ID правила
        rule = await session.get(AutoReplyRule, rule_id)
        if not rule or not await crud.check_account_ownership(session, rule.account_id, current_user.id):
            raise HTTPException(status_code=403, detail="Доступ запрещен или правило не найдено")
            
        await crud.update_autoreply_rule(session, rule_id, data.model_dump())
    return {"success": True}

@router.delete("/panel/api/autoreplies/{rule_id}", response_model=dict)
async def api_delete_account_autoreply(rule_id: uuid.UUID, current_user: User = Depends(get_current_webapp_user)):
    logger.info(f"[WebApp API] Пользователь {current_user.id} удаляет правило автоответа {rule_id}.")
    async with get_session() as session:
        rule = await session.get(AutoReplyRule, rule_id)
        if not rule or not await crud.check_account_ownership(session, rule.account_id, current_user.id):
            raise HTTPException(status_code=403, detail="Доступ запрещен или правило не найдено")
        
        await crud.delete_autoreply_rule(session, rule_id)
    return {"success": True}

@router.put("/panel/api/forwarding-rules/{rule_id}/permissions", response_model=dict)
async def api_update_forwarding_permissions(
    rule_id: uuid.UUID,
    data: PermissionsData,
    current_user: User = Depends(get_current_webapp_user)
):
    """Обновляет права доступа для конкретного правила пересылки."""
    logger.info(f"[WebApp API] Пользователь {current_user.id} обновляет разрешения для правила {rule_id}")
    
    async with get_session() as session:
        # Проверяем, что правило существует и принадлежит текущему пользователю
        rule = await session.get(ForwardingRule, rule_id)
        if not rule or rule.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Правило не найдено или у вас нет прав.")
            
        # Вызываем CRUD-функцию для обновления
        await crud.update_forwarding_permissions(
            session=session,
            rule_id=rule_id,
            owner_id=current_user.id, # Доп. проверка на всякий случай
            new_permissions=data.model_dump()
        )
    
    return {"success": True, "message": "Права доступа успешно обновлены."}

# ==========================================================
# === API ДЛЯ АДМИН-ПАНЕЛИ
# ==========================================================
@router.get("/panel/api/admin/users", response_model=List[dict])
async def api_admin_get_users(admin: User = Depends(get_admin_user)):
    """Возвращает список всех пользователей (только для админа)."""
    logger.info(f"ADMIN_PANEL: Администратор {admin.telegram_id} запросил список пользователей.")
    
    async with get_session() as session:
        users = await crud.get_all_users_for_admin(session)
    
    logger.info(f"ADMIN_PANEL: Найдено Найдено {len(users)} пользователей в БД.")
    
    response = []
    for user in users:
        full_name_parts = []
        if user.first_name:
            full_name_parts.append(user.first_name)
        if user.username:
            full_name_parts.append(f"(@{user.username})")
        
        full_name = " ".join(full_name_parts)
        if not full_name:
            full_name = f"User ID: {user.telegram_id}"

        response.append({
            "id": user.id,
            "telegram_id": user.telegram_id,
            "full_name": full_name,
            "tariff_plan": user.tariff_plan, # <- ИСПРАВЛЕНО
            "expires_at": user.tariff_expires_at.strftime("%Y-%m-%d") if user.tariff_expires_at else "—",
            "balance": user.balance,
            "created_at": user.created_at.strftime("%d.%m.%Y")
        })
        
    logger.info(f"ADMIN_PANEL: Возвращаем ответ JSON с {len(response)} записи пользователей.")
    return response

@router.get("/panel/api/admin/users/{user_id}/transactions", response_model=List[dict])
async def api_admin_get_user_transactions(user_id: int, admin: User = Depends(get_admin_user)):
    """Возвращает историю транзакций конкретного пользователя (только для админа)."""
    logger.info(f"ADMIN_PANEL: Администратор запросил транзакции для user_id: {user_id}")
    
    async with get_session() as session:
        transactions = await crud.get_user_transactions(session, user_id=user_id, limit=50)
    
    logger.info(f"ADMIN_PANEL: crud.get_user_transactions возвращен {len(transactions)} транзакции из БД.")
    
    response = []
    for tx in transactions:
        short_description = tx.description
        if "Пополнение через Telegram Payments" in tx.description:
            short_description = "Пополнение (Telegram)"
        elif "Оплата тарифа" in tx.description:
            try:
                tariff_name = tx.description.split('«')[1].split('»')[0]
                short_description = f"Оплата тарифа «{tariff_name}»"
            except IndexError:
                short_description = "Оплата тарифа"

        response.append({
            "timestamp": tx.timestamp.strftime("%d.%m.%Y %H:%M"),
            "description": short_description,
            "full_description": tx.description,
            "amount": tx.amount,
            "balance_after": tx.balance_after
        })
    
    logger.info(f"ADMIN_PANEL: Возвращаем ответ JSON с {len(response)} записи транзакций.")
    return response

@router.post("/panel/api/admin/users/update-tariff", response_model=dict)
async def api_admin_update_user_tariff(data: AdminTariffUpdateData, admin: User = Depends(get_admin_user)):
    """Обновляет тариф пользователя (только для админа)."""
    async with get_session() as session:
        updated_user = await crud.admin_update_user_tariff(
            session, data.user_id, data.new_plan, data.new_expires_at
        )
    if not updated_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден.")
    return {"success": True}

@router.get("/panel/api/admin/users/{user_id}/transactions", response_model=List[dict])
async def api_admin_get_user_transactions(user_id: int, admin: User = Depends(get_admin_user)):
    """Возвращает историю транзакций конкретного пользователя (только для админа)."""
    async with get_session() as session:
        transactions = await crud.get_user_transactions(session, user_id=user_id, limit=50)
    
    response = []
    for tx in transactions:
        response.append({
            "timestamp": tx.timestamp.strftime("%d.%m.%Y %H:%M"),
            "description": tx.description,
            "amount": tx.amount,
            "balance_after": tx.balance_after
        })
    return response

# --- ПОЛУЧЕНИЕ ДЕТАЛЕЙ ОДНОГО ПОЛЬЗОВАТЕЛЯ ---
@router.get("/panel/api/admin/users/{user_id}", response_model=dict)
async def api_admin_get_user_details(user_id: int, admin: User = Depends(get_admin_user)):
    """Возвращает детальную информацию о пользователе для формы редактирования."""
    async with get_session() as session:
        user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
        
    # Собираем список всех возможных тарифов для выпадающего списка
    available_tariffs = [{"id": plan.value, "name": config["name_readable"]} for plan, config in TARIFF_CONFIG.items()]

    return {
        "id": user.id,
        "full_name": user.first_name or f"ID: {user.telegram_id}",
        "balance": user.balance,
        "tariff_plan": user.tariff_plan,
        "expires_at": user.tariff_expires_at.strftime("%Y-%m-%d") if user.tariff_expires_at else None,
        "available_tariffs": available_tariffs
    }

# --- СОХРАНЕНИЕ ИЗМЕНЕНИЙ ---
@router.post("/panel/api/admin/users/{user_id}", response_model=dict)
async def api_admin_update_user(user_id: int, data: AdminUserDataUpdate, admin: User = Depends(get_admin_user)):
    """Обновляет данные пользователя (тариф, баланс, срок)."""
    async with get_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        # Обновляем тариф
        user.tariff_plan = data.tariff_plan
        
        # Обновляем дату окончания
        if data.expires_at:
            try:
                # Преобразуем строку в datetime. Добавляем время, чтобы было timezone-aware.
                user.tariff_expires_at = datetime.fromisoformat(data.expires_at + "T00:00:00").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                user.tariff_expires_at = None
        else:
            user.tariff_expires_at = None

        # Обрабатываем изменение баланса
        if data.add_to_balance != 0:
            # Используем наш wallet_service для корректного создания транзакции
            description = f"Корректировка баланса администратором ({'начисление' if data.add_to_balance > 0 else 'списание'})"
            # Для wallet_service не нужен redis_client, если мы не используем кэш
            await crud.create_transaction_and_update_balance(session, user.id, data.add_to_balance, description)
        else:
            # Если админ просто меняет баланс вручную
            user.balance = data.balance
            
        await session.commit()
    
    logger.info(f"ADMIN_PANEL: Admin {admin.telegram_id} обновленные данные для пользователя {user_id}.")
    return {"success": True, "message": "Данные пользователя успешно обновлены."}