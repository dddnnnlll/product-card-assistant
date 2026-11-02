"""Извлечение явно записанного назначения и сборка блока применимости из источников."""

import re

from .models import ExtractedFact, Extraction, Fact, GroundedText, Product

PURPOSE_LABELS = {"назначение", "задача прибора", "purpose"}
AUDIENCE_LABELS = {"кому подходит", "пользователи", "целевая аудитория", "аудитория"}


def source_span(text: str, snippet: str) -> str | None:
    """Находит исходную цитату, допуская только различия в пробельных символах."""
    parts = snippet.split()
    if not parts:
        return None
    match = re.search(r"\s+".join(re.escape(part) for part in parts), text)
    return match.group(0) if match else None


def explicit_extraction(product: Product) -> Extraction:
    """Копирует явно записанное назначение, аудиторию и именованные характеристики без выводов."""
    facts = []
    labels = sorted(set(product.specs) | {"Кому подходит", "Назначение", "Задача прибора"})
    for raw in re.split(r"(?<=[.!?])\s+|\n+", product.description):
        sentence = raw.strip()
        if not sentence:
            continue
        purpose = re.search(r"\bпредназначен(?:а|о|ы)?\s+для\s+(.+)", sentence, re.IGNORECASE)
        if purpose:
            value = purpose.group(1).rstrip(".!?").strip()
            if value and len(value) <= 500 and len(sentence) <= 1500:
                facts.append(ExtractedFact(label="Назначение", value=value, quote=sentence))
        for label in labels:
            match = re.match(re.escape(label) + r"\s*:\s*(.+)", sentence, re.IGNORECASE)
            if match:
                value = match.group(1).rstrip(".!?").strip()
                if value and len(value) <= 500 and len(sentence) <= 1500:
                    facts.append(ExtractedFact(label=label, value=value, quote=sentence))
    return Extraction(facts=facts[:30])


def grounded_commercial(facts: list[Fact]) -> GroundedText:
    """Формирует применимость и назначение только из явно подтверждённых полей."""
    purposes = [fact for fact in facts if fact.label.casefold() in PURPOSE_LABELS]
    audiences = [fact for fact in facts if fact.label.casefold() in AUDIENCE_LABELS]
    audience = "; ".join(fact.value for fact in audiences) if audiences else "данных нет; уточните требования заказчика"
    purpose = "; ".join(fact.value for fact in purposes) if purposes else "данных нет; запросите назначение прибора"
    ids = [fact.id for fact in audiences + purposes] or [facts[0].id]
    return GroundedText(text=f"Кому подходит: {audience}. Задача прибора: {purpose}.", fact_ids=ids)
