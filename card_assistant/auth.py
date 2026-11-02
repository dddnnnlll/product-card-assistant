"""Проверка общего пароля демонстрационного приложения без хранения открытого значения."""

import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    """Создаёт солёный PBKDF2-хеш пароля для серверного секрета."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    """Проверяет пароль сравнением хешей и отклоняет повреждённую конфигурацию."""
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != 600_000 or len(salt) != 32 or len(expected) != 64:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return bool(password) and hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False
