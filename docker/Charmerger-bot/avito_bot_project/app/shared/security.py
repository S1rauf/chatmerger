# src/shared/security.py
from cryptography.fernet import Fernet
from .config import settings

# Инициализируем шифровальщик
try:
    fernet = Fernet(settings.encryption_key.encode()) 
except (ValueError, TypeError):
    raise ValueError("ENCRYPTION_KEY must be a 32-byte URL-safe base64-encoded key.")

def encrypt_token(token: str) -> str:
    """Шифрует строку и возвращает результат в виде строки."""
    if not token:
        return ""
    return fernet.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """Дешифрует строку и возвращает результат."""
    if not encrypted_token:
        return ""
    return fernet.decrypt(encrypted_token.encode()).decode()