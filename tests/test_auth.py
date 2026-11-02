"""Проверки хеширования общего пароля."""

from card_assistant.auth import hash_password, verify_password


def test_password_hash_and_wrong_values() -> None:
    """Проверяет правильный пароль, случайную соль и отклонение неверных значений."""
    encoded = hash_password("test-password")
    assert verify_password("test-password", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("", encoded)
    assert not verify_password("test-password", "broken")
    assert not verify_password("test-password", encoded.replace("600000", "1"))
    assert hash_password("test-password") != encoded
