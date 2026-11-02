"""Создаёт хеш пароля для секретов Streamlit без открытого ввода в командной строке."""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from card_assistant.auth import hash_password


def main() -> None:
    """Запрашивает пароль скрытым вводом и печатает только его солёный хеш."""
    password = getpass.getpass("Пароль приложения: ")
    if len(password) < 12:
        raise SystemExit("Используйте пароль длиной не менее 12 символов.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
