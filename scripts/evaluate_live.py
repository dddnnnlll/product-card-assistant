"""Воспроизводимая проверка двух учебных товаров и необязательных граничных сценариев."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from card_assistant.models import parse_product
from card_assistant.provider import DEFAULT_MODELS, ProviderError
from card_assistant.service import all_blocks, generate_live, markdown_report, normalize


def main() -> int:
    """Запускает реальные запросы и сохраняет карточки и фактические результаты проверок."""
    parser = argparse.ArgumentParser(description="Проверка качества через реальный API; расходует бесплатную квоту")
    parser.add_argument("--edge-cases", action="store_true")
    parser.add_argument("--cases", nargs="+", choices=["manometer", "transmitter", "incomplete", "conflict"])
    args = parser.parse_args()
    names = args.cases or (["manometer", "transmitter", "incomplete", "conflict"] if args.edge_cases else ["manometer", "transmitter"])
    key = os.environ.get("OPENROUTER_API_KEY") or dotenv_values(ROOT / ".env").get("OPENROUTER_API_KEY", "")
    if not key:
        print("Добавьте ключ в .env; реальные запросы не выполнялись.")
        return 1
    results = []
    for name in names:
        print(f"Проверка: {name}", flush=True)
        product = parse_product((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))
        try:
            report = generate_live(product, key, DEFAULT_MODELS[0])
        except ProviderError as exc:
            blocked_conflict = name == "conflict" and "Противоречие в данных:" in str(exc)
            item = {"case": name, "passed": blocked_conflict,
                    "status": "blocked_conflict" if blocked_conflict else "failed", "error": str(exc)}
            results.append(item)
            (ROOT / "examples" / f"live_{name}.error.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(str(exc), flush=True)
            continue
        base = ROOT / "examples" / f"live_{name}"
        base.with_suffix(".result.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        base.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
        passed = report.semantic_reviewed and (report.requirements_met if name in {"manometer", "transmitter"} else True)
        if name == "incomplete":
            passed = passed and report.status == "needs_information" and "данных нет" in report.draft.commercial.text
        if name == "conflict":
            draft_text = normalize(" ".join(block.text for block in all_blocks(report.draft)))
            passed = passed and any("Противоречие" in warning for warning in report.warnings)
            passed = passed and all(normalize(value) not in draft_text for value in ("0–1,6 МПа", "0–2,5 МПа"))
        item = {"case": name, "passed": bool(passed), "model": report.model, "api_calls": report.api_calls,
                "title_chars": len(report.draft.seo_title.text), "description_chars": len(report.draft.seo_description.text),
                "search_queries": len(report.draft.search_queries), "requirements_met": report.requirements_met,
                "semantic_reviewed": report.semantic_reviewed, "status": report.status}
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    summary = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "synthetic_inputs": True, "results": results}
    target = ROOT / "examples" / ("live_edge_checks.json" if args.cases else "live_evaluation.json")
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
