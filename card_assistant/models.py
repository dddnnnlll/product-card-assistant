"""Схемы входа, фактов и результата генерации."""

import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

ShortText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=300)]


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    manufacturer: ShortText | None = None
    specs: dict[ShortText, ShortText | None] = Field(default_factory=dict, max_length=40)
    description: str = Field(default="", max_length=12000)

    @field_validator("specs")
    @classmethod
    def check_spec_names(cls, value: dict) -> dict:
        """Отклоняет характеристики с пустыми названиями."""
        if any(not key.strip() for key in value):
            raise ValueError("Название характеристики не может быть пустым")
        return value


class Fact(BaseModel):
    id: str
    label: str
    value: str
    source: str
    quote: str


class ExtractedFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: ShortText
    value: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    quote: Annotated[str, StringConstraints(min_length=1, max_length=1500)]


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    facts: list[ExtractedFact] = Field(max_length=30)


class GroundedText(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2500)]
    fact_ids: list[str] = Field(min_length=1, max_length=50)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    seo_title: GroundedText
    seo_description: GroundedText
    search_queries: list[GroundedText] = Field(min_length=3, max_length=5)
    commercial: GroundedText


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    block: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=1500)
    reason: str = Field(min_length=1, max_length=1500)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    issues: list[ReviewIssue] = Field(max_length=20)


class Report(BaseModel):
    product: Product
    draft: Draft
    facts: list[Fact]
    characteristics: list[dict[str, str]]
    warnings: list[str]
    checks: list[dict[str, str]]
    mode: str
    model: str
    api_calls: int
    needs_review: bool = True
    requirements_met: bool = False
    semantic_reviewed: bool = False
    status: str = "needs_information"


class InputError(ValueError):
    """Понятная пользователю ошибка входных данных."""


def unique_object(pairs: list[tuple]) -> dict:
    """Отклоняет повторяющиеся JSON-ключи до потери исходных значений."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Повторяющийся JSON-ключ: {key}")
        result[key] = value
    return result


def parse_product(raw: str) -> Product:
    """Проверяет размер, синтаксис и схему JSON одного товара."""
    if len(raw.encode("utf-8")) > 60000:
        raise InputError("JSON слишком большой: максимум 60 КБ.")
    try:
        data = json.loads(raw, object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise InputError(f"Некорректный JSON: строка {exc.lineno}, столбец {exc.colno}.") from None
    except RecursionError:
        raise InputError("Слишком много уровней вложенности JSON.") from None
    try:
        return Product.model_validate(data)
    except ValidationError as exc:
        messages = []
        for item in exc.errors(include_input=False, include_url=False)[:6]:
            field = ".".join(str(part) for part in item["loc"]) or "JSON"
            messages.append(f"{field}: {item['msg']}")
        raise InputError("Проверьте поля: " + "; ".join(messages)) from None
