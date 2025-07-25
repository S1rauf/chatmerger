# /app/db_models.py
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    ForeignKey, DateTime, UUID, String, Boolean, Integer, Text, JSON, Float, BigInteger
)
from sqlalchemy.orm import declarative_base, relationship, Mapped, mapped_column
from sqlalchemy.sql import func
# Импортируем Enum из нашего модуля billing
from modules.billing.enums import TariffPlan

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    username: Mapped[Optional[str]] = mapped_column(String(100))
    balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # --- Поля для тарифов ---
    tariff_plan: Mapped[TariffPlan] = mapped_column(String(50), default=TariffPlan.START, nullable=False)
    tariff_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_tariff_plan: Mapped[Optional[TariffPlan]] = mapped_column(String(50), nullable=True)
    # --- Поля для настроек ---
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/Moscow", nullable=False)
    has_agreed_to_terms: Mapped[bool] = mapped_column(Boolean, default=False)
    # --- Связи ---
    avito_accounts: Mapped[List["AvitoAccount"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    transactions: Mapped[List["Transaction"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    templates: Mapped[List["Template"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    notes: Mapped[List["ChatNote"]] = relationship(back_populates="author")
    # Связь с правилами пересылки, где этот пользователь является владельцем
    owned_forwarding_rules: Mapped[List["ForwardingRule"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class AvitoAccount(Base):
    __tablename__ = "avito_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[Optional[str]] = mapped_column(String(100))
    avito_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    encrypted_oauth_token: Mapped[str] = mapped_column(String(512), nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(String(512), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # --- Поле для кэширования ---
    chats_count_cache: Mapped[int] = mapped_column(Integer, default=0)
    # --- Связи ---
    user: Mapped["User"] = relationship(back_populates="avito_accounts")
    autoreply_rules: Mapped[List["AutoReplyRule"]] = relationship(back_populates="account", cascade="all, delete-orphan")
 
# --- МОДЕЛЬ ДЛЯ ПРАВИЛ АВТООТВЕТОВ ---
class AutoReplyRule(Base):
    __tablename__ = "auto_reply_rules"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[int] = mapped_column(ForeignKey("avito_accounts.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False) # например, 'contains_any', 'always', 'exact'
    trigger_keywords: Mapped[Optional[List[str]]] = mapped_column(JSON) # Список ключевых слов
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=3600, comment="Кулдаун в секундах для этого правила в этом чате")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    account: Mapped["AvitoAccount"] = relationship(back_populates="autoreply_rules")


# --- НОВАЯ МОДЕЛЬ ДЛЯ ПРАВИЛ ПЕРЕСЫЛКИ ---
class ForwardingRule(Base):
    __tablename__ = "forwarding_rules"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Поле target_telegram_id будет заполняться ПОСЛЕ принятия приглашения
    target_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    custom_rule_name: Mapped[str] = mapped_column(String(100)) # Это имя помощника, например, "Менеджер Василий"
    invite_password: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Статус приглашения (is_accepted больше не нужен, его заменяет наличие target_telegram_id)
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, default=lambda: uuid.uuid4().hex)
    permissions: Mapped[Dict[str, Any]] = mapped_column(JSON, default=lambda: {"can_reply": False, "allowed_accounts": None})
    # --- Связи ---
    owner: Mapped["User"] = relationship(back_populates="owned_forwarding_rules")
  

class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped["User"] = relationship(back_populates="transactions")

class Template(Base):
    __tablename__ = "templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True) # User ID - не Optional
    user: Mapped["User"] = relationship(back_populates="templates")

class ChatNote(Base):
    __tablename__ = "chat_notes"
    
    # ---!!! ИЗМЕНЯЕМ ПЕРВИЧНЫЙ КЛЮЧ !!!---
    account_id: Mapped[int] = mapped_column(ForeignKey("avito_accounts.id", ondelete="CASCADE"), primary_key=True)
    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True) 
    # ------------------------------------

    text: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())
    
    author: Mapped["User"] = relationship(back_populates="notes")
    
class MessageLog(Base):
    __tablename__ = "message_logs"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[int] = mapped_column(ForeignKey("avito_accounts.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, index=True) # 'in' (входящее) или 'out' (исходящее)
    is_autoreply: Mapped[bool] = mapped_column(Boolean, default=False)
    trigger_name: Mapped[Optional[str]] = mapped_column(String(100)) # Имя автоответа или шаблона
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)