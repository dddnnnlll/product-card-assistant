"""Контролируемые ответы только для изолированных тестов, недоступные в приложении."""

import json
from pathlib import Path

from card_assistant.models import Draft, Extraction, GroundedText, Product, Report
from card_assistant.service import build_report, canonical_label, merge_facts

from card_assistant.examples import EXAMPLES_DIR, EXAMPLES, example_json


def fixture_extraction(product: Product) -> Extraction:
    """Возвращает вручную размеченные цитаты только для неизменённых учебных примеров."""
    for filename in EXAMPLES.values():
        source = Product.model_validate_json((EXAMPLES_DIR / filename).read_text(encoding="utf-8"))
        comparable = product.model_copy(update={"manufacturer": product.manufacturer or None,
                                                "specs": {key: value or None for key, value in product.specs.items()}})
        expected = source.model_copy(update={"manufacturer": source.manufacturer or None,
                                             "specs": {key: value or None for key, value in source.specs.items()}})
        if comparable == expected:
            fixture = json.loads((Path(__file__).parent / "extractions.json").read_text(encoding="utf-8"))
            return Extraction.model_validate(fixture[filename])
    raise ValueError("Локальная демонстрация поддерживает четыре примера без изменений. Для своих данных выберите OpenRouter API.")


def fixture_report(product: Product) -> Report:
    """Собирает шаблонную демонстрационную карточку из учебных фактов без LLM."""
    extraction = fixture_extraction(product)
    facts, _, conflicts = merge_facts(product, extraction)
    allowed = [fact for fact in facts if canonical_label(fact.label) not in conflicts]
    ids = [fact.id for fact in allowed]
    details = "; ".join(f"{fact.label.lower()}: {fact.value}" for fact in allowed if fact.source != "name")
    text = (
        f"{product.name} — черновик карточки на основании переданных сведений. "
        f"В исходных данных указано: {details or 'дополнительных характеристик нет'}. "
        "При выборе прибора сопоставьте указанные параметры с требованиями вашей задачи. "
        "Если нужного свойства нет в перечне, запросите его подтверждение у поставщика. "
        "Отсутствующее значение не означает, что прибор обладает или не обладает этим свойством. "
        "Перед заказом уточните исполнение и проверьте сведения по актуальному паспорту конкретного изделия. "
        "Противоречивые характеристики вынесены в предупреждения и не используются в этом описании. "
        "Окончательное решение о применимости требует проверки исходной документации."
    )
    purpose = next((fact.value for fact in allowed if fact.label.casefold() == "назначение"), None)
    commercial = (f"Подходит для задачи, прямо указанной в исходном описании: {purpose}. "
                  "Соответствие условиям заказчика необходимо уточнить." if purpose else
                  "Назначение прибора не подтверждено достаточными исходными данными. Запросите задачу применения и паспорт изделия.")
    title = product.name if len(product.name) <= 70 else product.name[:67].rstrip() + "…"
    draft = Draft(
        seo_title=GroundedText(text=title, fact_ids=["f1"]),
        seo_description=GroundedText(text=text, fact_ids=ids),
        search_queries=[GroundedText(text=query, fact_ids=["f1"]) for query in
                        [product.name, f"{product.name} характеристики", f"{product.name} заказать"]],
        commercial=GroundedText(text=commercial, fact_ids=ids),
    )
    report = build_report(product, extraction, draft, "Локальная демонстрация", "Шаблон, без LLM", 0)
    report.warnings.insert(0, "Это шаблонная демонстрация интерфейса, а не ответ модели. Учебные данные не подтверждают свойства реального товара.")
    return report
