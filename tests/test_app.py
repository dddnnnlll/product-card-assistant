"""Проверки пользовательских сценариев средствами Streamlit AppTest."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from card_assistant.provider import ProviderError
from card_assistant.auth import hash_password
from tests.fixtures import fixture_report

APP = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch):
    """Исключает реальные ключи из UI-тестов, чтобы тестирование не вызывало платёжный или сетевой API."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password("test-password"))

    def empty_env(*args, **kwargs):
        """Подменяет чтение локального .env пустым набором настроек."""
        return {}

    monkeypatch.setattr("dotenv.dotenv_values", empty_env)

    def controlled_generation(product, api_key, model_id, repair=False):
        """Возвращает контролируемый ответ API в тестах и отклоняет отсутствующий ключ."""
        if not api_key:
            raise ProviderError("Для генерации нужен API-ключ OpenRouter.")
        if "противоречат" in product.description:
            raise ProviderError("Противоречие в данных: уточните диапазон измерений перед генерацией.")
        report = fixture_report(product)
        return report.model_copy(update={"mode": "OpenRouter API", "model": model_id, "api_calls": 2})

    monkeypatch.setattr("card_assistant.service.generate_live", controlled_generation)


def click_label(app: AppTest, label: str) -> AppTest:
    """Нажимает кнопку с заданной подписью и выполняет цикл Streamlit."""
    for button in app.button:
        if button.label == label:
            button.click()
            return app.run()
    raise AssertionError(f"Button missing: {label}")


def test_api_form_and_invalid_input() -> None:
    """Проверяет единственный режим API, заполнение формы и удаление результата при ошибке."""
    app = logged_in_app()
    assert not app.exception
    assert len(app.radio) == 0
    app.text_input(key="api_key_input").set_value("test-key").run()
    click_label(app, "Заполнить пример")
    assert app.text_input(key="name").value == "Учебный манометр М-100"
    click_label(app, "Создать описание")
    assert not app.exception
    assert "seo_description" in app.session_state["report"]["draft"]
    assert app.session_state["report"]["api_calls"] == 2
    assert app.session_state["report"]["mode"] == "OpenRouter API"
    app.text_input(key="name").set_value("").run()
    assert any("изменили" in warning.value for warning in app.warning)
    click_label(app, "Создать описание")
    assert app.error
    assert len(app.metric) == 0
    assert not app.exception


def test_conflict_and_missing_api_key() -> None:
    """Проверяет переключение примера, предупреждение о конфликте и ошибку отсутствия ключа."""
    app = logged_in_app()
    app.text_input(key="api_key_input").set_value("test-key").run()
    app.selectbox(key="example_label").select("Противоречивые данные").run()
    click_label(app, "Заполнить пример")
    click_label(app, "Создать описание")
    assert not app.exception
    assert any("Противоречие" in error.value for error in app.error)
    app.text_input(key="api_key_input").set_value("").run()
    click_label(app, "Создать описание")
    assert not app.exception
    assert any("API-ключ" in error.value for error in app.error)


def logged_in_app() -> AppTest:
    """Открывает тестовую сессию и выполняет вход с тестовым паролем."""
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    app.text_input(key="login_password").set_value("test-password")
    return click_label(app, "Войти")


def test_login_gate_and_logout() -> None:
    """Проверяет скрытие генерации до входа, отказ неверному паролю и очистку при выходе."""
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not app.exception
    assert [item.label for item in app.button] == ["Войти"]
    assert len(app.text_area) == 0
    app.text_input(key="login_password").set_value("wrong")
    click_label(app, "Войти")
    assert any("Неверный пароль" in item.value for item in app.error)
    assert len(app.text_area) == 0
    app.session_state["login_retry_after"] = 0
    app.text_input(key="login_password").set_value("test-password")
    click_label(app, "Войти")
    assert not app.exception
    app.text_input(key="api_key_input").set_value("test-key").run()
    click_label(app, "Заполнить пример")
    click_label(app, "Создать описание")
    click_label(app, "Выйти")
    assert not app.exception
    assert [item.label for item in app.button] == ["Войти"]
    assert "report" not in app.session_state
    assert "api_key_input" not in app.session_state


def test_missing_auth_secret_closes_access(monkeypatch) -> None:
    """Проверяет закрытый доступ при отсутствии серверного секрета входа."""
    monkeypatch.delenv("APP_PASSWORD_HASH")
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not app.exception
    assert len(app.button) == 0
    assert any("Доступ ещё не настроен" in item.value for item in app.error)
