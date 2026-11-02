"""Проверки ввода, фактов, бесплатного доступа и результата без реального API."""

import json
from unittest.mock import Mock, patch

import pytest
import requests

from card_assistant.examples import EXAMPLES, example_json
from tests.fixtures import fixture_extraction, fixture_report
from card_assistant.models import Draft, Extraction, GroundedText, InputError, Product, parse_product
from card_assistant.provider import ProviderError, complete, free_catalog, parse_response_json, zero_price
from card_assistant.service import generate_live, markdown_report, merge_facts, validate_draft


def sample_product() -> Product:
    """Возвращает полный учебный товар для независимых сценариев."""
    return parse_product(example_json(next(iter(EXAMPLES))))


def free_model() -> dict:
    """Создаёт минимальный ответ каталога для бесплатной текстовой модели."""
    return {"id": "test/model:free", "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"output_modalities": ["text"]}, "supported_parameters": ["response_format"]}


@pytest.mark.parametrize("raw", ["", "{", "[]", "null", '{"name":""}', '{"name":12}',
                                    '{"name":"A", "specs":{"x":false}}',
                                    '{"name":"A", "specs":{" ":"x"}}',
                                    '{"name":"A", "extra":"x"}',
                                    '{"name":"A", "name":"B"}',
                                    '{"name":"A", "specs":{"x":"1", "x":"2"}}'])
def test_invalid_input_is_rejected(raw: str) -> None:
    """Отклоняет некорректный синтаксис, типы, лишние поля и дубликаты."""
    with pytest.raises(InputError):
        parse_product(raw)


def test_empty_optional_fields_are_reported() -> None:
    """Сохраняет неизвестные значения и выводит понятные предупреждения."""
    product = parse_product(example_json("Неполные данные"))
    report = fixture_report(product)
    assert report.characteristics[0]["Значение"] == "данных нет"
    assert any("Производитель: данных нет" in warning for warning in report.warnings)
    assert report.api_calls == 0


def test_size_limit() -> None:
    """Отклоняет чрезмерный ввод до запуска LLM."""
    with pytest.raises(InputError, match="большой"):
        parse_product(" " * 60001)


def test_all_demo_examples_export() -> None:
    """Проверяет генерацию и экспорт всех четырёх локальных сценариев."""
    for label in EXAMPLES:
        product = parse_product(example_json(label))
        report = fixture_report(product)
        assert report.needs_review
        assert report.api_calls == 0
        assert "Источники фактов" in markdown_report(report)
        assert json.loads(report.model_dump_json())["mode"] == "Локальная демонстрация"


def test_custom_input_cannot_masquerade_as_demo() -> None:
    """Не выдаёт фиктивный результат модели для произвольного пользовательского ввода."""
    with pytest.raises(ValueError, match="без изменений"):
        fixture_report(Product(name="Произвольный прибор"))


def test_conflicting_values_not_used_in_text() -> None:
    """Сохраняет оба источника конфликта и исключает спорный диапазон из описания."""
    product = parse_product(example_json("Противоречивые данные"))
    report = fixture_report(product)
    assert any("Противоречие" in warning for warning in report.warnings)
    assert "1,6" not in report.draft.seo_description.text
    assert "2,5" not in report.draft.seo_description.text
    assert sum(item["Статус"] == "Уточнить" for item in report.characteristics) == 2


def test_invalid_quote_excluded() -> None:
    """Исключает извлечённое свойство, для которого нет точной исходной цитаты."""
    extraction = Extraction.model_validate({"facts": [{"label": "Материал", "value": "алюминий", "quote": "Корпус: алюминий"}]})
    facts, warnings, _ = merge_facts(sample_product(), extraction)
    assert all(fact.value != "алюминий" for fact in facts)
    assert any("исключён" in warning for warning in warnings)


def test_measurement_and_fact_reference_checks() -> None:
    """Обнаруживает новые числа, подмену единиц и ссылки на неизвестные факты."""
    product = sample_product()
    report = fixture_report(product)
    allowed, _, _ = merge_facts(product, fixture_extraction(product))
    for text in ["Диаметр 900 мм", "Диаметр 100 бар"]:
        draft = report.draft.model_copy(deep=True)
        draft.commercial.text = text
        _, errors = validate_draft(draft, allowed)
        assert errors
    draft = report.draft.model_copy(deep=True)
    draft.seo_title.fact_ids = ["not-existing"]
    assert validate_draft(draft, allowed)[1]


def test_free_only_gate() -> None:
    """Блокирует платные модели и ненулевые дополнительные тарифы."""
    model = free_model()
    assert zero_price(model)
    for mutation in [{"id": "test/paid"}, {"pricing": {"prompt": "1", "completion": "0"}},
                     {"pricing": {"prompt": "0", "completion": "0", "request": "0.01"}}]:
        candidate = {**model, **mutation}
        assert not zero_price(candidate)
        with patch("card_assistant.provider.requests.post") as post:
            with pytest.raises(ProviderError):
                complete("fake-test-key", candidate, "system", {}, {})
            post.assert_not_called()


def test_catalog_filters_and_network_error() -> None:
    """Фильтрует платные модели и безопасно обрабатывает недоступность каталога."""
    response = Mock()
    response.json.return_value = {"data": [free_model(), {**free_model(), "id": "test/paid"}]}
    with patch("card_assistant.provider.requests.get", return_value=response):
        assert list(free_catalog()) == ["test/model:free"]
    with patch("card_assistant.provider.requests.get", side_effect=requests.Timeout):
        with pytest.raises(ProviderError, match="бесплатные модели"):
            free_catalog()


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 429, 500])
def test_api_errors_do_not_leak_key(status: int) -> None:
    """Возвращает безопасную ошибку без секретов и ответа внешнего сервера."""
    response = Mock(status_code=status, ok=False)
    response.text = "fake-sensitive-key"
    with patch("card_assistant.provider.requests.post", return_value=response):
        with pytest.raises(ProviderError) as error:
            complete("fake-sensitive-key", free_model(), "system", {}, {})
    assert "fake-sensitive-key" not in str(error.value)


def test_timeouts_and_truncation() -> None:
    """Обрабатывает тайм-аут и не принимает оборванный ответ модели."""
    with patch("card_assistant.provider.requests.post", side_effect=requests.Timeout):
        with pytest.raises(ProviderError, match="вовремя"):
            complete("test", free_model(), "system", {}, {})
    response = Mock(ok=True, status_code=200)
    response.json.return_value = {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
    with patch("card_assistant.provider.requests.post", return_value=response):
        with pytest.raises(ProviderError, match="оборван"):
            complete("test", free_model(), "system", {}, {})


def test_json_parsing_and_zero_price_request() -> None:
    """Принимает JSON-блок и отправляет явный нулевой предел стоимости."""
    assert parse_response_json('```json\n{"facts": []}\n```') == {"facts": []}
    with pytest.raises(ProviderError):
        parse_response_json('Вот ответ: {"facts": []}')
    response = Mock(ok=True, status_code=200)
    response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": '{"facts": []}'}}]}
    with patch("card_assistant.provider.requests.post", return_value=response) as post:
        assert complete("test", free_model(), "system", {}, {}) == {"facts": []}
        body = post.call_args.kwargs["json"]
        assert body["provider"]["max_price"] == {"prompt": 0, "completion": 0}
        assert body["model"].endswith(":free")


def test_live_pipeline_with_mocked_model() -> None:
    """Проводит полный сценарий API на контролируемых ответах вместо реальных расходов."""
    product = sample_product()
    expected = fixture_report(product)
    model = free_model()
    with patch("card_assistant.service.free_catalog", return_value={model["id"]: model}), patch(
        "card_assistant.service.complete", side_effect=[fixture_extraction(product).model_dump(), expected.draft.model_dump(), {"issues": []}]
    ) as complete_mock:
        actual = generate_live(product, "test-key", model["id"])
    assert actual.api_calls == 3
    assert complete_mock.call_count == 3
    assert actual.semantic_reviewed
    assert actual.requirements_met
    assert actual.mode == "OpenRouter API"


def test_missing_key_does_not_access_network() -> None:
    """Отклоняет запуск без ключа до любых сетевых вызовов."""
    with patch("card_assistant.service.free_catalog") as catalog:
        with pytest.raises(ProviderError, match="API-ключ"):
            generate_live(sample_product(), "", "test/model:free")
    catalog.assert_not_called()


def test_repair_is_bounded_and_unsafe_result_is_rejected() -> None:
    """Ограничивает исправления одной попыткой и блокирует неподтверждённые свойства."""
    product = sample_product()
    bad = fixture_report(product).draft
    bad.seo_description.text = "Давление 999 бар"
    model = free_model()
    with patch("card_assistant.service.free_catalog", return_value={model["id"]: model}), patch(
        "card_assistant.service.complete", side_effect=[fixture_extraction(product).model_dump(), bad.model_dump(), bad.model_dump()]
    ) as complete_mock:
        with pytest.raises(ProviderError, match="не прошёл"):
            generate_live(product, "test", model["id"], repair=True)
    assert complete_mock.call_count == 3
