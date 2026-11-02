"""Проверки пользовательских сценариев средствами Streamlit AppTest."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from card_assistant.provider import ProviderError
from tests.fixtures import fixture_report

APP = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch):
    """Исключает реальные ключи из UI-тестов, чтобы тестирование не вызывало платёжный или сетевой API."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

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
    app = AppTest.from_file(str(APP), default_timeout=20).run()
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
    app = AppTest.from_file(str(APP), default_timeout=20).run()
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
