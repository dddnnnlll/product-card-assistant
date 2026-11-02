"""Проверки назначения, границ объёма и блокирования смысловых ошибок."""

from unittest.mock import patch

import pytest

from card_assistant.grounding import explicit_extraction, grounded_commercial
from card_assistant.models import Extraction, GroundedText, Product, parse_product
from card_assistant.examples import example_json
from card_assistant.provider import ProviderError
from card_assistant.service import build_report, generate_live, input_facts, merge_facts, validate_draft, writing_schema
from tests.fixtures import fixture_extraction, fixture_report
from tests.test_core import free_model


def test_explicit_purpose_is_not_lost_with_bad_llm_quote() -> None:
    """Сохраняет точное назначение из исходника даже при неверной цитате модели."""
    product = Product(name="Прибор", description="Прибор предназначен для измерения избыточного давления.")
    bad = Extraction.model_validate({"facts": [{"label": "назначение", "value": "измерения давления",
                                              "quote": "Измеритель предназначен для измерения давления."}]})
    facts, warnings, conflicts = merge_facts(product, bad)
    commercial = grounded_commercial(facts)
    assert "измерения избыточного давления" in commercial.text
    assert "Кому подходит: данных нет" in commercial.text
    assert not conflicts
    assert any("исключён" in warning for warning in warnings)


def test_commercial_does_not_infer_purpose_from_name() -> None:
    """Не выводит задачу или отрасль применения только из названия прибора."""
    facts, _ = input_facts(Product(name="Манометр для пищевого производства"))
    commercial = grounded_commercial(facts)
    assert "Задача прибора: данных нет" in commercial.text
    assert "пищевого производства" not in commercial.text


def test_normalized_quote_keeps_original_whitespace() -> None:
    """Сопоставляет пробелы в цитате, сохраняя исходную запись значения."""
    product = Product(name="Прибор", description="Питание: 24\u00a0В.")
    extraction = Extraction.model_validate({"facts": [{"label": "питание", "value": "24 В", "quote": "Питание: 24 В."}]})
    facts, _, _ = merge_facts(product, extraction)
    assert any(fact.value == "24\u00a0В" for fact in facts)


def test_title_limit_is_enforced_in_report() -> None:
    """Блокирует слишком длинный заголовок, а не только добавляет предупреждение."""
    product = parse_product(example_json("Манометр · полные данные"))
    draft = fixture_report(product).draft
    draft.seo_title.text = "А" * 71
    with pytest.raises(ProviderError, match="70"):
        build_report(product, fixture_extraction(product), draft, "test", "test", 0)


def test_description_limit_blocks_complete_input_after_one_repair() -> None:
    """Не возвращает короткое описание как готовую карточку при достаточном вводе."""
    product = parse_product(example_json("Манометр · полные данные"))
    draft = fixture_report(product).draft
    draft.seo_description.text = "Краткий текст без требуемого объёма."
    model = free_model()
    with patch("card_assistant.service.free_catalog", return_value={model["id"]: model}), patch(
        "card_assistant.service.complete", side_effect=[fixture_extraction(product).model_dump(), draft.model_dump(), draft.model_dump()]
    ) as request:
        with pytest.raises(ProviderError, match="800–1200"):
            generate_live(product, "test", model["id"])
    assert request.call_count == 3


def test_semantic_issue_blocks_same_number_under_different_property() -> None:
    """Блокирует подмену назначения размера, которую не обнаруживает поиск новых чисел."""
    product = parse_product(example_json("Манометр · полные данные"))
    draft = fixture_report(product).draft
    draft.seo_description.text = draft.seo_description.text.replace("диаметр корпуса: 100 мм", "диаметр резьбы: 100 мм")
    facts, _, _ = merge_facts(product, fixture_extraction(product))
    assert not validate_draft(draft, facts, True)[1]
    issue = {"issues": [{"block": "seo_description", "statement": "диаметр резьбы: 100 мм",
                          "reason": "100 мм относится к диаметру корпуса, а не резьбы"}]}
    model = free_model()
    with patch("card_assistant.service.free_catalog", return_value={model["id"]: model}), patch(
        "card_assistant.service.complete",
        side_effect=[fixture_extraction(product).model_dump(), draft.model_dump(), issue, draft.model_dump(), issue]
    ) as request:
        with pytest.raises(ProviderError, match="резьбы"):
            generate_live(product, "test", model["id"])
    assert request.call_count == 5


def test_incomplete_input_returns_explicit_partial_status() -> None:
    """Разрешает короткий черновик для скудных данных с явным статусом нехватки сведений."""
    product = parse_product(example_json("Неполные данные"))
    draft = fixture_report(product).draft
    draft.seo_description.text = "Недостаточно данных для полного описания."
    model = free_model()
    with patch("card_assistant.service.free_catalog", return_value={model["id"]: model}), patch(
        "card_assistant.service.complete", side_effect=[draft.model_dump(), {"issues": []}]
    ):
        report = generate_live(product, "test", model["id"])
    assert report.status == "needs_information"
    assert not report.requirements_met
    assert report.semantic_reviewed
    assert "данных нет" in report.draft.commercial.text


def test_description_schema_does_not_constrain_other_blocks() -> None:
    """Ограничивает длину только нужного поля, не меняя схему поисковых фраз."""
    schema = writing_schema(True)
    assert schema["$defs"]["Description"]["properties"]["text"]["minLength"] == 800
    assert schema["$defs"]["Title"]["properties"]["text"]["maxLength"] == 70
    assert schema["$defs"]["GroundedText"]["properties"]["text"]["minLength"] == 1


def test_sales_and_training_purpose_are_not_inferred() -> None:
    """Блокирует утверждения о продаже и учебном применении по одному слову в названии."""
    product = parse_product(example_json("Манометр · полные данные"))
    draft = fixture_report(product).draft
    facts, _, _ = merge_facts(product, fixture_extraction(product))
    for phrase in ("Прибор реализуется поставщиком", "Применяется для учебных занятий"):
        draft.seo_description.text = phrase
        assert validate_draft(draft, facts)[1]


def test_oversized_explicit_sentence_does_not_break_extraction() -> None:
    """Пропускает слишком длинное предложение вместо исключения из-за длины цитаты."""
    product = Product(name="Прибор", description="Прибор предназначен для " + "измерения " * 200)
    assert explicit_extraction(product).facts == []


def test_conflict_is_blocked_before_writing() -> None:
    """Останавливает генерацию при разных диапазонах и показывает оба исходных значения."""
    product = parse_product(example_json("Противоречивые данные"))
    model = free_model()
    with patch("card_assistant.service.free_catalog", return_value={model["id"]: model}), patch(
        "card_assistant.service.complete", return_value={"facts": []}
    ) as request:
        with pytest.raises(ProviderError, match="Противоречие") as error:
            generate_live(product, "test", model["id"])
    assert "0–1,6 МПа" in str(error.value)
    assert "0–2,5 МПа" in str(error.value)
    assert request.call_count == 1
