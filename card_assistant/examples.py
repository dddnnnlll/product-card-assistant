"""Учебные входные данные для заполнения формы перед реальным вызовом API."""

from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
EXAMPLES = {
    "Манометр · полные данные": "manometer.json",
    "Преобразователь · полные данные": "transmitter.json",
    "Неполные данные": "incomplete.json",
    "Противоречивые данные": "conflict.json",
}


def example_json(label: str) -> str:
    """Читает выбранный пример входных данных без генерации текста."""
    return (EXAMPLES_DIR / EXAMPLES[label]).read_text(encoding="utf-8")
