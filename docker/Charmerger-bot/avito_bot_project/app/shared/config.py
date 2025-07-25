import logging
from typing import Optional, List, Dict
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===================================================================
# === КЛАСС НАСТРОЕК НА ОСНОВЕ Pydantic
# ===================================================================
class Settings(BaseSettings):
    """
    Класс для управления настройками приложения из переменных окружения.
    Pydantic автоматически считывает переменные из .env файла и валидирует их.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra='ignore'  # Игнорировать лишние переменные в .env, не описанные здесь
    )

    # --- Общие настройки ---
    # `Field(..., alias=...)` используется, если имя переменной в .env отличается от имени поля в классе
    domain: Optional[str] = Field(None, alias="DOMAIN")
    debug_mode: bool = Field(False, alias="DEBUG_MODE")

    # --- Telegram ---
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_bot_username: str = Field(..., alias="TELEGRAM_BOT_USERNAME") 
    telegram_admin_id: Optional[int] = Field(None, alias="TELEGRAM_ADMIN_ID")
    support_bot_username: str = Field("ChatMerger_SupportBot", alias="SUPPORT_BOT_USERNAME")

    # --- Avito ---
    avito_client_id: str = Field(..., alias="AVITO_CLIENT_ID")
    avito_client_secret: str = Field(..., alias="AVITO_CLIENT_SECRET")
    avito_webhook_secret: str = Field(..., alias="AVITO_WEBHOOK_SECRET")

    # --- База данных (PostgreSQL) ---
    db_user: str = Field(..., alias="POSTGRES_USER")
    db_password: str = Field(..., alias="POSTGRES_PASSWORD")
    db_host: str = Field("postgres", alias="POSTGRES_HOST")
    db_port: int = Field(5432, alias="POSTGRES_PORT")
    db_name: str = Field(..., alias="POSTGRES_DB")

    # --- Redis ---
    redis_host: str = Field("redis", alias="REDIS_HOST")
    redis_port: int = Field(6379, alias="REDIS_PORT")
    redis_db: int = Field(0, alias="REDIS_DB")

    # --- Шифрование и безопасность ---
    encryption_key: str = Field(..., alias="ENCRYPTION_KEY")
    jwt_secret_key: str = Field(..., alias="JWT_SECRET_KEY")

    # --- Оплата (YooKassa/Telegram Payments) ---
    tg_payments_provider_token: Optional[str] = Field(None, alias="TG_PAYMENTS_PROVIDER_TOKEN")
    ykassa_shop_id: Optional[str] = Field(None, alias="YKASSA_SHOP_ID")
    ykassa_secret_key: Optional[str] = Field(None, alias="YKASSA_SECRET_KEY")

    # --- Уведомления админу (переключатели) ---
    admin_notifications_enabled: bool = Field(True, alias="ADMIN_NOTIFICATIONS_ENABLED")
    admin_notify_new_users: bool = Field(True, alias="ADMIN_NOTIFY_NEW_USERS")
    admin_notify_payments: bool = Field(True, alias="ADMIN_NOTIFY_PAYMENTS")
    admin_notify_critical_errors: bool = Field(True, alias="ADMIN_NOTIFY_CRITICAL_ERRORS")
    admin_notify_warnings: bool = Field(False, alias="ADMIN_NOTIFY_WARNINGS")

    # === Вычисляемые поля (собираются из других полей) ===
    @computed_field
    @property
    def database_url(self) -> str:
        """Собирает URL для подключения к PostgreSQL."""
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @computed_field
    @property
    def redis_url(self) -> str:
        """Собирает URL для подключения к Redis."""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field
    @property
    def webapp_base_url(self) -> str:
        """Возвращает базовый URL для веб-приложения и вебхуков."""
        return f"https://{self.domain}" if self.domain else ""

    @computed_field
    @property
    def avito_redirect_uri(self) -> str:
        """Возвращает URL для коллбэка после OAuth авторизации Avito."""
        return self.webapp_base_url + "/" if self.webapp_base_url else ""

# --- Создаем единственный экземпляр настроек для всего приложения ---
try:
    settings = Settings()
    logger.info("Конфигурация успешно загружена через Pydantic.")
    
    if not settings.domain:
        logger.warning(
            "Переменная окружения DOMAIN не установлена. "
            "Вебхуки (Telegram, Avito, OAuth callback) не будут работать корректно без публичного домена."
        )

except Exception as e:
    logger.error(f"ФАТАЛЬНАЯ ОШИБКА: Не удалось загрузить конфигурацию из переменных среды. Ошибка.: {e}")
    # Выбрасываем исключение, чтобы приложение не запустилось с неполной конфигурацией
    raise

# ===================================================================
# === СТАТИЧЕСКАЯ КОНФИГУРАЦИЯ (не зависит от .env)
# ===================================================================

# --- Настройки времени жизни кэша (TTL в секундах) ---
PROCESSED_ID_TTL: int = 86400         # Идемпотентность Avito вебхуков (1 день)
REPLY_MAPPING_TTL: int = 86400 * 3    # Связь TG сообщения с Avito чатом (3 дня)
USER_DATA_CACHE_TTL: int = 3600       # Кэш данных пользователя (1 час)
TERMS_AGREEMENT_CACHE_TTL: int = 86400 * 30 # Кэш согласия (30 дней)
INIT_DATA_MAX_AGE_SECONDS: int = 3600 # Максимальный возраст initData для WebApp (1 час)

# --- Часовые пояса для выбора в WebApp и боте ---
POPULAR_TIMEZONES_PYTZ: Dict[str, str] = {
    "Europe/Kaliningrad": "Калининград (GMT+2)",
    "Europe/Moscow": "Москва (GMT+3)",
    "Europe/Samara": "Самара (GMT+4)",
    "Asia/Yekaterinburg": "Екатеринбург (GMT+5)",
    "Asia/Omsk": "Омск (GMT+6)",
    "Asia/Krasnoyarsk": "Красноярск (GMT+7)",
    "Asia/Irkutsk": "Иркутск (GMT+8)",
    "Asia/Yakutsk": "Якутск (GMT+9)",
    "Asia/Vladivostok": "Владивосток (GMT+10)",
    "Asia/Magadan": "Магадан (GMT+11)",
    "Asia/Kamchatka": "Камчатка (GMT+12)"
}

# --- Текст пользовательского соглашения ---
# Длинный текст лучше хранить здесь или даже в отдельном файле .txt и считывать его
USER_AGREEMENT_INTRO_TEXT: str = """
<b>Пользовательское Соглашение ChatMerger Bot</b>

Пожалуйста, внимательно ознакомьтесь с условиями. Использование Бота означает ваше полное и безоговорочное согласие с ними.

<b>Ключевые моменты:</b>
- Бот предоставляется "как есть".
- Вы несете ответственность за безопасность вашего аккаунта Avito.
- Запрещено использовать Бота для спама и незаконных действий.
- Платные услуги и возврат средств регулируются текущими тарифами и политикой сервиса.
"""

# Скрытая, полная юридическая часть
USER_AGREEMENT_FULL_TEXT: str = """
<b>1. Общие положения</b>
1.1. Бот предоставляется "как есть". Администрация Бота не несет ответственности за любые прямые или косвенные убытки, возникшие в результате использования или невозможности использования Бота.
1.2. Используя Бота, вы подтверждаете, что достигли возраста, необходимого для заключения юридически обязывающих соглашений в вашей юрисдикции.
1.3. Для работы Бота требуется авторизация через ваш аккаунт Avito. Вы несете полную ответственность за безопасность данных вашего аккаунта Avito и за все действия, совершенные с использованием токенов доступа, полученных Ботом с вашего согласия. Мы используем шифрование для хранения ваших токенов доступа Avito.

<b>2. Предоставляемые услуги</b>
2.1. Бот предназначен для агрегации сообщений из ваших чатов Avito и их пересылки в Telegram, а также для отправки ответов из Telegram в чаты Avito.
2.2. Бот может предоставлять дополнительные функции (шаблоны, автоответы, заметки, пересылка и др.) в соответствии с выбранным тарифным планом.
2.3. Функционал Бота и условия тарифов могут изменяться. Актуальная информация доступна через команду /tariffs.

<b>3. Права и обязанности Пользователя</b>
3.1. Пользователь обязуется использовать Бота только в законных целях.
3.2. Пользователь обязуется не использовать Бота для рассылки спама или вредоносной информации.
3.3. Пользователь несет ответственность за содержание сообщений, отправляемых через Бота.
3.4. Пользователь имеет право в любой момент отозвать доступ Бота к своему аккаунту Avito (/myaccounts -> Отключить).

<b>4. Права и обязанности Администрации Бота</b>
4.1. Администрация Бота имеет право приостановить или прекратить доступ Пользователя к Боту в случае нарушения Соглашения.
4.2. Администрация Бота имеет право собирать обезличенные данные об использовании Бота для улучшения его работы.
4.3. Администрация Бота обязуется принимать разумные меры для обеспечения безопасности данных Пользователя.

<b>5. Платные услуги и Кошелек</b>
5.1. Некоторые функции предоставляются на платной основе согласно Тарифным планам (/tariffs).
5.2. Пользователь может пополнять внутренний Кошелек в Боте. Средства с Кошелька могут быть использованы для оплаты Тарифов.
5.3. Оплата производится через интегрированные платежные системы (Telegram Payments).
5.4. Возврат средств с Кошелька или за оплаченные Тарифы осуществляется по усмотрению Администрации и в соответствии с применимым законодательством. В большинстве случаев оплаченные услуги возврату не подлежат, если услуга была предоставлена.

<b>6. Интеллектуальная собственность</b>
6.1. Все права на Бота принадлежат его разработчикам.

<b>7. Заключительные положения</b>
7.1. Соглашение может быть изменено Администрацией. Новая редакция вступает в силу с момента публикации или уведомления.
7.2. Продолжение использования Бота после изменений означает согласие с новой редакцией.

Нажимая "✅ Принять условия", вы подтверждаете, что ознакомились и полностью принимаете условия.
"""

SUPPORT_GREETING_MESSAGE: str = """
💬 <b>Поддержка и Помощь</b>

Пожалуйста, сначала ознакомьтесь с ответами на часто задаваемые вопросы. Возможно, решение уже есть!

Если вы не нашли ответ, вы можете написать сообщение администратору.
"""

# Словарь с вопросами и ответами для FAQ
SUPPORT_FAQ: Dict[str, str] = {
    "q_connect_error": "<b>❓ Что делать, если не получается подключить аккаунт Avito?</b>\n\n1. Убедитесь, что вы авторизованы в браузере именно в том аккаунте Avito, который хотите подключить.\n2. Попробуйте очистить кэш и cookie в браузере для сайта Avito.\n3. Если ошибка повторяется, возможно, Avito временно недоступен. Попробуйте позже.",
    "q_messages_not_coming": "<b>❓ Я подключил аккаунт, но сообщения не приходят.</b>\n\n1. Убедитесь, что вы приняли Пользовательское Соглашение (вам должно было прийти сообщение после подключения аккаунта).\n2. Проверьте в меню \"Мои аккаунты\", что ваш аккаунт активен (🟢).\n3. Напишите тестовое сообщение в любой чат на Avito, чтобы \"активировать\" переписку.",
    "q_webapp_not_loading": "<b>❓ Не загружается или не обновляется WebApp.</b>\n\nЭто связано с агрессивным кэшированием в Telegram. Попробуйте полностью перезапустить ваше приложение Telegram. Для десктопной версии может потребоваться очистка кэша через меню разработчика (правый клик в WebApp -> Inspect -> Network -> Disable cache).",
    "q_other": "<b>❓ Моего вопроса здесь нет.</b>\n\nНажмите на кнопку ниже, чтобы задать вопрос напрямую администратору.",
}