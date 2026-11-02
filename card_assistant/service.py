"""Подготовка фактов, контроль черновика и экспорт результата."""

import copy
import re

from pydantic import ValidationError

from .models import Draft, Extraction, Fact, GroundedText, Product, Report, Review
from .prompts import EXTRACT_SYSTEM, WRITE_SYSTEM, REVIEW_SYSTEM
from .grounding import explicit_extraction, grounded_commercial, source_span, PURPOSE_LABELS, AUDIENCE_LABELS
from .provider import ProviderError, complete, free_catalog

ALIASES = {
    "диапазон измерений": "диапазон", "диапазон измерения": "диапазон",
    "измеряемый диапазон": "диапазон", "рабочий диапазон": "диапазон",
    "резьба присоединения": "присоединение",
}


def normalize(value: str) -> str:
    """Приводит пробелы, десятичные разделители и тире к виду для консервативного сравнения."""
    return re.sub(r"\s+", "", value.casefold().replace(",", ".").replace("–", "-").replace("−", "-"))


def canonical_label(label: str) -> str:
    """Сопоставляет небольшой список синонимов технических полей."""
    lowered = label.strip().casefold()
    return ALIASES.get(lowered, lowered)


def input_facts(product: Product) -> tuple[list[Fact], list[str]]:
    """Переносит известные входные поля в факты и явно отмечает пустые сведения."""
    facts = [Fact(id="f1", label="Название", value=product.name, source="name", quote=product.name)]
    warnings = []
    if product.manufacturer and product.manufacturer.strip():
        facts.append(Fact(id="f2", label="Производитель", value=product.manufacturer,
                          source="manufacturer", quote=product.manufacturer))
    else:
        warnings.append("Производитель: данных нет.")
    for label, value in product.specs.items():
        if value and value.strip():
            facts.append(Fact(id=f"f{len(facts) + 1}", label=label, value=value,
                              source=f"specs.{label}", quote=value))
        else:
            warnings.append(f"{label}: данных нет.")
    if not product.specs:
        warnings.append("Список характеристик не заполнен.")
    if not product.description.strip():
        warnings.append("Описание или фрагмент паспорта: данных нет.")
    return facts, warnings


def merge_facts(product: Product, extraction: Extraction) -> tuple[list[Fact], list[str], list[str]]:
    """Проверяет цитаты и исключает из генерации свойства с обнаруженными конфликтами."""
    facts, warnings = input_facts(product)
    candidates = explicit_extraction(product).facts + extraction.facts
    for item in candidates:
        quote = source_span(product.description, item.quote)
        value = source_span(quote or "", item.value)
        if quote is None or value is None:
            warnings.append(f"Не удалось подтвердить цитатой поле «{item.label}»; факт исключён.")
            continue
        if any(canonical_label(fact.label) == canonical_label(item.label)
               and normalize(fact.value) == normalize(value) for fact in facts):
            continue
        facts.append(Fact(id=f"f{len(facts) + 1}", label=item.label, value=value,
                          source="description", quote=quote))
    groups: dict[str, list[Fact]] = {}
    for fact in facts:
        groups.setdefault(canonical_label(fact.label), []).append(fact)
    conflicts = []
    for label, group in groups.items():
        if len({normalize(fact.value) for fact in group}) > 1:
            conflicts.append(label)
            values = "; ".join(f"{fact.value} ({fact.source})" for fact in group)
            warnings.append(f"Противоречие — {group[0].label}: {values}. Требуется уточнение; поле исключено из генерации.")
    if any(canonical_label(fact.label) in conflicts and fact.source == "name" for fact in facts):
        raise ProviderError("Обнаружено противоречие в названии товара. Уточните исходные данные.")
    return facts, warnings, conflicts


def all_blocks(draft: Draft) -> list[GroundedText]:
    """Возвращает все генерируемые блоки, включая поисковые запросы."""
    return [draft.seo_title, draft.seo_description, draft.commercial, *draft.search_queries]


def numeric_tokens(text: str) -> set[str]:
    """Извлекает цифровые фрагменты с учётом десятичных разделителей."""
    return {value.replace(",", ".") for value in re.findall(r"\d+(?:[.,]\d+)?", text)}


def measurement_tokens(text: str) -> set[str]:
    """Извлекает пары чисел и распространённых единиц для обнаружения подмены единиц."""
    matches = re.findall(r"\d+(?:[.,]\d+)?\s*(?:мпа|кпа|па|бар|мм|см|мА|мВ|мкА|гц|кг|°[cс]|в|а|%)\b", text, re.IGNORECASE)
    return {normalize(match) for match in matches}


def validate_draft(draft: Draft, allowed: list[Fact], strict_description: bool = False) -> tuple[list[dict[str, str]], list[str]]:
    """Проверяет длину, уникальность запросов, ссылки на факты и неподтверждённые числа."""
    checks = []
    hard_errors = []
    if len(draft.seo_title.text) > 70:
        hard_errors.append("SEO-заголовок превышает 70 символов.")
    if len(draft.seo_description.text) > 1200 or (strict_description and len(draft.seo_description.text) < 800):
        hard_errors.append(f"SEO-описание: {len(draft.seo_description.text)} символов; требуется 800–1200.")
    known = {fact.id: fact for fact in allowed}
    for label, actual, minimum, maximum in [
        ("SEO-заголовок", len(draft.seo_title.text), 1, 70),
        ("SEO-описание", len(draft.seo_description.text), 800, 1200),
        ("Поисковые запросы", len(draft.search_queries), 3, 5),
    ]:
        checks.append({"Проверка": label, "Результат": str(actual),
                       "Статус": "OK" if minimum <= actual <= maximum else "Требует правки"})
    for block in all_blocks(draft):
        if any(ref not in known for ref in block.fact_ids):
            hard_errors.append("В ответе есть ссылки на отсутствующие или спорные факты.")
            continue
        source = " ".join(known[ref].value for ref in block.fact_ids)
        for phrase in ("реализуется", "поставляется", "в наличии", "для учебных", "в учебных",
                       "демонстрационных целях", "сертифицирован", "гарантирует", "взрывозащищ"):
            if phrase in block.text.casefold() and phrase not in source.casefold():
                hard_errors.append(f"Неподтверждённое утверждение: «{phrase}».")
        if numeric_tokens(block.text) - numeric_tokens(source):
            hard_errors.append("В тексте есть числа, не подтверждённые указанными фактами.")
        if measurement_tokens(block.text) - measurement_tokens(source):
            hard_errors.append("В тексте есть сочетание числа и единицы, не подтверждённое указанными фактами.")
    if len({query.text.casefold() for query in draft.search_queries}) != len(draft.search_queries):
        hard_errors.append("Поисковые запросы повторяются.")
    hard_errors = list(dict.fromkeys(hard_errors))
    checks.append({"Проверка": "Ссылки, числа и единицы", "Результат": " / ".join(hard_errors) if hard_errors else "Проверено",
                   "Статус": "Ошибка" if hard_errors else "OK"})
    return checks, hard_errors


def build_report(product: Product, extraction: Extraction, draft: Draft, mode: str,
                 model: str, api_calls: int, strict_description: bool = False,
                 semantic_reviewed: bool = False) -> Report:
    """Собирает карточку с исходными характеристиками и предупреждениями о проверке."""
    facts, warnings, conflicts = merge_facts(product, extraction)
    allowed = [fact for fact in facts if canonical_label(fact.label) not in conflicts]
    draft = draft.model_copy(update={"commercial": grounded_commercial(allowed)})
    checks, hard_errors = validate_draft(draft, allowed, strict_description)
    if hard_errors:
        raise ProviderError("Черновик не прошёл проверку: " + " ".join(hard_errors))
    for item in checks:
        if item["Статус"] != "OK":
            warnings.append(f"{item['Проверка']}: {item['Результат']}; требование по объёму не выполнено.")
    characteristics = []
    for label, value in product.specs.items():
        conflict = canonical_label(label) in conflicts
        characteristics.append({"Характеристика": label, "Значение": value or "данных нет",
                                "Источник": f"specs.{label}", "Статус": "Уточнить" if conflict else "Из входных данных"})
    for fact in facts:
        if fact.source == "description":
            characteristics.append({"Характеристика": fact.label, "Значение": fact.value,
                                    "Источник": "description", "Статус": "Уточнить" if canonical_label(fact.label) in conflicts else "Цитата из описания"})
    warnings.append("Нужна проверка менеджером: автоматические проверки не доказывают смысловую достоверность текста и интерпретации цитат.")
    if not any(fact.label.casefold() in PURPOSE_LABELS for fact in allowed):
        warnings.append("Назначение прибора: данных нет; требуется уточнение.")
    if not any(fact.label.casefold() in AUDIENCE_LABELS for fact in allowed):
        warnings.append("Кому подходит прибор: данных нет; требуется уточнение.")
    met = all(check["Статус"] == "OK" for check in checks)
    return Report(product=product, draft=draft, facts=facts, characteristics=characteristics,
                  warnings=list(dict.fromkeys(warnings)), checks=checks, mode=mode,
                  model=model, api_calls=api_calls, requirements_met=met,
                  semantic_reviewed=semantic_reviewed, status="draft" if met else "needs_information")


def writing_schema(strict_description: bool) -> dict:
    """Добавляет ограничения длины описания в схему ответа для достаточно полного входа."""
    schema = copy.deepcopy(Draft.model_json_schema())
    description = copy.deepcopy(schema["$defs"]["GroundedText"])
    description["properties"]["text"].update(minLength=800 if strict_description else 1, maxLength=1200)
    schema["$defs"]["Description"] = description
    schema["properties"]["seo_description"] = {"$ref": "#/$defs/Description"}
    title = copy.deepcopy(schema["$defs"]["GroundedText"])
    title["properties"]["text"]["maxLength"] = 70
    schema["$defs"]["Title"] = title
    schema["properties"]["seo_title"] = {"$ref": "#/$defs/Title"}
    return schema


def generate_live(product: Product, api_key: str, model_id: str, repair: bool = True) -> Report:
    """Генерирует и проверяет карточку, разрешая одну правку и максимум пять вызовов API."""
    if not api_key.strip():
        raise ProviderError("Для реальной генерации нужен API-ключ OpenRouter. Добавьте его в настройках или .env.")
    if not model_id.endswith(":free"):
        raise ProviderError("Разрешены только модели с суффиксом :free.")
    catalog = free_catalog()
    if model_id not in catalog:
        raise ProviderError("Выбранная модель отсутствует в текущем бесплатном каталоге. Выберите другую модель в настройках.")
    model = catalog[model_id]
    calls = 0
    extraction = Extraction(facts=[])
    if product.description.strip():
        raw = complete(api_key, model, EXTRACT_SYSTEM,
                       {"description": product.description, "spec_labels": list(product.specs)},
                       Extraction.model_json_schema())
        calls += 1
        try:
            extraction = Extraction.model_validate(raw)
        except ValidationError:
            raise ProviderError("Модель вернула неверную структуру фактов. Выберите другую бесплатную модель.") from None
    facts, _, conflicts = merge_facts(product, extraction)
    if conflicts:
        details = []
        for label in conflicts:
            values = list(dict.fromkeys(fact.value for fact in facts if canonical_label(fact.label) == label))
            details.append(f"{label}: " + " / ".join(values))
        raise ProviderError("Противоречие в данных: " + "; ".join(details) +
                            ". Уточните эти значения перед генерацией.")
    allowed = [fact for fact in facts if canonical_label(fact.label) not in conflicts]
    strict = sum(fact.source not in {"name", "manufacturer"} for fact in allowed) >= 3
    payload = {"facts": [fact.model_dump() for fact in allowed],
               "description_min_chars": 800 if strict else 1, "description_max_chars": 1200}
    final_issues = []
    for attempt in range(2 if repair else 1):
        raw = complete(api_key, model, WRITE_SYSTEM, payload, writing_schema(strict))
        calls += 1
        try:
            draft = Draft.model_validate(raw)
        except ValidationError:
            final_issues = ["Соблюдай JSON-схему: все обязательные поля, ссылки на факты и 3–5 поисковых фраз."]
            payload["corrections"] = final_issues
            if repair and attempt == 0:
                continue
            raise ProviderError("Модель вернула неверную структуру карточки.") from None
        draft.commercial = grounded_commercial(allowed)
        checks, issues = validate_draft(draft, allowed, strict)
        if not issues:
            review_raw = complete(api_key, model, REVIEW_SYSTEM,
                                  {"facts": payload["facts"], "draft": draft.model_dump()},
                                  Review.model_json_schema())
            calls += 1
            try:
                review = Review.model_validate(review_raw)
            except ValidationError:
                raise ProviderError("Не удалось проверить достоверность ответа: модель вернула неверный формат проверки.") from None
            issues = [f"{issue.block}: «{issue.statement}» — {issue.reason}" for issue in review.issues]
        if not issues:
            return build_report(product, extraction, draft, "OpenRouter API", model_id, calls,
                                strict_description=strict, semantic_reviewed=True)
        final_issues = issues
        payload["previous_draft"] = draft.model_dump()
        payload["corrections"] = issues
    raise ProviderError("Черновик не прошёл проверку: " + " ".join(final_issues) +
                        " Добавьте подтверждённые сведения или выберите другую бесплатную модель.")


def markdown_report(report: Report) -> str:
    """Экспортирует карточку и предупреждения в переносимый Markdown."""
    lines = [f"# {report.draft.seo_title.text}", "", f"> Черновик · {report.mode} · {report.model}",
             "", "## SEO-описание", "", report.draft.seo_description.text,
             "", "## Характеристики", ""]
    for item in report.characteristics:
        lines.append(f"- {item['Характеристика']}: {item['Значение']} ({item['Статус']})")
    lines += ["", "## Поисковые фразы (без проверки частотности)", ""]
    lines += [f"- {query.text}" for query in report.draft.search_queries]
    lines += ["", "## Для менеджера", "", report.draft.commercial.text, "", "## Требует внимания", ""]
    lines += [f"- {warning}" for warning in report.warnings]
    lines += ["", "## Источники фактов", ""]
    lines += [f"- {fact.id}: {fact.label} — {fact.value}; источник: {fact.source}; цитата: «{fact.quote}»" for fact in report.facts]
    return "\n".join(lines) + "\n"
