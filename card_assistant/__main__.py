"""Генерация карточки из JSON-файла для воспроизводимой проверки без браузера."""

import argparse
import os
from pathlib import Path

from dotenv import dotenv_values

from .models import InputError, parse_product
from .provider import DEFAULT_MODELS, ProviderError
from .service import generate_live, markdown_report


def main() -> int:
    """Генерирует карточку через API и сохраняет JSON и Markdown в выбранную папку."""
    parser = argparse.ArgumentParser(description="Создать карточку товара через бесплатную модель OpenRouter")
    parser.add_argument("input", type=Path, help="Входной JSON одного товара")
    parser.add_argument("--output", type=Path, default=Path("generated"))
    parser.add_argument("--model", default=DEFAULT_MODELS[0])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    key = os.environ.get("OPENROUTER_API_KEY") or dotenv_values(root / ".env").get("OPENROUTER_API_KEY", "")
    try:
        product = parse_product(args.input.read_text(encoding="utf-8-sig"))
        report = generate_live(product, key, args.model)
        args.output.mkdir(parents=True, exist_ok=True)
        target = args.output / args.input.stem
        target.with_suffix(".result.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        target.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
    except (InputError, ProviderError, OSError, UnicodeError) as exc:
        print(str(exc))
        return 1
    print(f"Сохранено: {target}.result.json и {target}.md")
    print(f"Соответствие формату: {report.requirements_met}; проверка смысла: {report.semantic_reviewed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
