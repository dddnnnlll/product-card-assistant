"""Вызовы только бесплатных моделей через OpenRouter без платного fallback."""

import json
from decimal import Decimal, InvalidOperation

import requests

API_ROOT = "https://openrouter.ai/api/v1"
DEFAULT_MODELS = [
    "nvidia/nemotron-3.5-super-vl:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
]


class ProviderError(RuntimeError):
    """Безопасная для отображения ошибка обращения к провайдеру."""


def zero_price(model: dict) -> bool:
    """Проверяет нулевую стоимость всех заявленных компонентов модели."""
    prices = model.get("pricing", {})
    try:
        return (
            model.get("id", "").endswith(":free")
            and "prompt" in prices and "completion" in prices
            and all(Decimal(str(value)) == 0 for value in prices.values())
        )
    except (InvalidOperation, TypeError, ValueError):
        return False


def free_catalog() -> dict[str, dict]:
    """Получает текущий каталог и оставляет только бесплатные текстовые модели."""
    try:
        response = requests.get(f"{API_ROOT}/models", timeout=(10, 30))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("Invalid catalog")
        return {
            model["id"]: model for model in payload["data"]
            if isinstance(model, dict) and zero_price(model)
            and "text" in model.get("architecture", {}).get("output_modalities", [])
        }
    except (requests.RequestException, ValueError, KeyError, TypeError):
        raise ProviderError("Не удалось проверить бесплатные модели. Проверьте интернет и попробуйте снова.") from None


def parse_response_json(content: str) -> dict:
    """Разбирает чистый JSON или один JSON-блок, не вырезая фрагменты из произвольного текста."""
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if lines[0].lower() in ("```", "```json"):
            text = "\n".join(lines[1:-1])
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("Expected object")
        return payload
    except (ValueError, RecursionError):
        raise ProviderError("Модель вернула некорректный JSON. Попробуйте другую бесплатную модель.") from None


def complete(api_key: str, model: dict, system: str, data: dict, schema: dict) -> dict:
    """Отправляет один запрос с запретом ненулевых цен и безопасной обработкой ответа."""
    if not api_key.strip():
        raise ProviderError("Добавьте API-ключ OpenRouter в настройках или локальном .env.")
    if not zero_price(model):
        raise ProviderError("Вызов заблокирован: разрешены только модели :free с нулевыми ценами.")
    body = {
        "model": model["id"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
        "max_tokens": 5500,
        "temperature": 0.2,
        "provider": {"max_price": {"prompt": 0, "completion": 0}},
    }
    parameters = model.get("supported_parameters", [])
    if "structured_outputs" in parameters:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        }
    elif "response_format" in parameters:
        body["response_format"] = {"type": "json_object"}
    if "reasoning" in parameters:
        body["reasoning"] = {"enabled": False, "exclude": True}
    try:
        response = requests.post(
            f"{API_ROOT}/chat/completions", json=body,
            headers={"Authorization": f"Bearer {api_key.strip()}", "X-Title": "Product Card Assistant"},
            timeout=(10, 100),
        )
    except requests.Timeout:
        raise ProviderError("Модель не ответила вовремя. Попробуйте позже или выберите другую модель.") from None
    except requests.RequestException:
        raise ProviderError("Не удалось связаться с OpenRouter. Проверьте подключение к интернету.") from None
    errors = {
        400: "Модель не приняла формат запроса. Выберите другую бесплатную модель.",
        401: "OpenRouter отклонил API-ключ. Проверьте ключ в настройках.",
        402: "OpenRouter заблокировал запрос по балансу аккаунта. Пополнение не требуется для штатного бесплатного режима; проверьте аккаунт.",
        403: "Нет доступа к модели: проверьте доступность и настройки провайдера в OpenRouter.",
        404: "Бесплатная модель сейчас недоступна. Обновите список и выберите другую.",
        429: "Достигнут лимит бесплатных запросов или сервер перегружен. Попробуйте позже.",
    }
    if response.status_code in errors:
        raise ProviderError(errors[response.status_code])
    if not response.ok:
        raise ProviderError("OpenRouter временно не может выполнить запрос. Попробуйте позже.")
    try:
        payload = response.json()
        choice = payload["choices"][0]
        if choice.get("finish_reason") not in ("stop", "end_turn"):
            raise ProviderError("Ответ модели оборван или отклонён. Попробуйте другую модель.")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Модель вернула пустой ответ. Попробуйте другую модель.")
        return parse_response_json(content)
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError("OpenRouter вернул неожиданный формат ответа.") from None
