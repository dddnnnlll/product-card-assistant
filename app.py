"""Интерфейс Streamlit для генерации товарных карточек через подключённую модель."""

import json
import os
from pathlib import Path

import streamlit as st
from dotenv import dotenv_values

from card_assistant.examples import EXAMPLES, example_json
from card_assistant.models import InputError, Product, Report, parse_product
from card_assistant.provider import DEFAULT_MODELS, ProviderError
from card_assistant.service import generate_live, markdown_report

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Карточка товара", layout="wide")


def local_key() -> str:
    """Читает ключ из окружения, локального .env или секретов Streamlit без вывода значения."""
    value = os.environ.get("OPENROUTER_API_KEY") or dotenv_values(ROOT / ".env").get("OPENROUTER_API_KEY")
    if value:
        return value
    try:
        return st.secrets.get("OPENROUTER_API_KEY", "")
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return ""


def fill_example() -> None:
    """Заполняет обычные поля выбранным учебным примером и убирает старый результат."""
    product = parse_product(example_json(st.session_state.get("example_label", next(iter(EXAMPLES)))))
    st.session_state.update({"name": product.name, "manufacturer": product.manufacturer or "",
                             "specs_text": "\n".join(f"{key}: {value or ''}" for key, value in product.specs.items()),
                             "description": product.description})
    st.session_state.pop("report", None)
    st.session_state.pop("generation_error", None)


def form_product() -> Product:
    """Преобразует заполненную форму в проверенный товар, сохраняя неизвестные значения."""
    if not st.session_state.get("name", "").strip():
        raise InputError("Напишите название товара в первом поле.")
    specs = {}
    for number, line in enumerate(st.session_state.get("specs_text", "").splitlines(), 1):
        if not line.strip():
            continue
        if ":" not in line:
            raise InputError(f"В характеристиках, строка {number}: поставьте двоеточие между названием и значением. Например: Питание: 24 В")
        label, value = (part.strip() for part in line.split(":", 1))
        if label in specs:
            raise InputError(f"Характеристика «{label}» указана дважды. Оставьте одно значение.")
        specs[label] = value or None
    return parse_product(json.dumps({"name": st.session_state["name"],
                                     "manufacturer": st.session_state.get("manufacturer", "") or None,
                                     "specs": specs, "description": st.session_state.get("description", "")}, ensure_ascii=False))


def plain_text(report: Report) -> str:
    """Собирает заголовок, описание и остальные блоки в обычный текст для скачивания."""
    lines = [report.draft.seo_title.text, "", report.draft.seo_description.text, "", "Характеристики"]
    lines += [f"{item['Характеристика']}: {item['Значение']}" +
              (" (требует уточнения)" if item["Статус"] == "Уточнить" else "")
              for item in report.characteristics]
    lines += ["", "Поисковые фразы"] + [query.text for query in report.draft.search_queries]
    lines += ["", "Для менеджера", report.draft.commercial.text, "", "Перед публикацией проверьте текст и характеристики."]
    lines += ["", "Замечания"] + report.warnings
    return "\n".join(lines)


def render_report(report: Report) -> None:
    """Показывает готовое описание и прячет дополнительные сведения в одном блоке."""
    st.divider()
    st.subheader("Готовое описание")
    try:
        if form_product() != report.product:
            st.warning("Вы изменили данные. Нажмите «Создать описание», чтобы обновить текст.")
    except InputError:
        st.warning("Вы изменили данные. Исправьте форму и создайте описание заново.")
    st.text(report.draft.seo_title.text)
    st.text(report.draft.seo_description.text)
    if not report.requirements_met:
        st.warning("Данных недостаточно для полной карточки по заданию. Добавьте подтверждённые сведения; ниже доступен частичный черновик.")
    else:
        st.caption(f"Заголовок: {len(report.draft.seo_title.text)} из 70 символов. Описание: {len(report.draft.seo_description.text)} из 800–1200.")
    if any("Противоречие" in warning for warning in report.warnings):
        st.warning("В данных есть противоречие. Откройте подробности ниже и уточните характеристики.")
    st.download_button("Скачать описание", plain_text(report), "описание-товара.txt", "text/plain", type="primary")
    st.caption("Перед публикацией прочитайте текст и проверьте характеристики.")
    with st.expander("Характеристики, поисковые фразы и проверка"):
        if report.characteristics:
            st.dataframe(report.characteristics, hide_index=True, width="stretch")
        st.write("По каким фразам можно искать товар")
        st.text("\n".join(query.text for query in report.draft.search_queries))
        st.write("Для менеджера")
        st.text(report.draft.commercial.text)
        for warning in report.warnings:
            st.warning(warning)
        st.dataframe(report.checks, hide_index=True, width="stretch")
        st.caption(f"Модель: {report.model}. Запросов: {report.api_calls}.")
        st.code(plain_text(report), language=None, wrap_lines=True)
        st.write("Источники фактов")
        st.dataframe([fact.model_dump() for fact in report.facts], hide_index=True, width="stretch")
        st.json(report.model_dump(), expanded=False)
        st.download_button("Скачать JSON", report.model_dump_json(indent=2), "product-card.json", "application/json")
        st.download_button("Скачать Markdown", markdown_report(report), "product-card.md", "text/markdown")


def main() -> None:
    """Предлагает заполнить четыре поля и создать описание одной основной кнопкой."""
    st.markdown("""<style>
        :root {color-scheme:dark;}
        .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {background:#101418; color:#E5EBF0;}
        .block-container {padding-top:3rem; max-width:860px; padding-bottom:3rem;}
        h1 {font-size:2.2rem !important; letter-spacing:-0.7px;}
        [data-testid="stText"] {font-family:inherit; line-height:1.65; white-space:pre-wrap;}
        [data-testid="stCaptionContainer"] {color:#A4B2BE;}
        [data-testid="stBaseButton-primary"] {color:#101418 !important; font-weight:600;}
        [data-testid="stAlert"] p {color:#D4E5F2;}
        [data-testid="stToolbar"] {display:none;}
    </style>""", unsafe_allow_html=True)
    for field in ("name", "manufacturer", "specs_text", "description"):
        st.session_state.setdefault(field, "")
    saved_key = local_key()
    if st.session_state.get("pipeline_version") != 2:
        st.session_state.pop("report", None)
        st.session_state["pipeline_version"] = 2
    st.title("Создать описание товара")
    st.write("Заполните данные и нажмите кнопку. Полученный текст можно скачать и вставить в магазин.")
    st.button("Заполнить пример", on_click=fill_example, help="Подставит вымышленный товар, чтобы попробовать приложение.")
    st.text_input("Название товара", key="name", placeholder="Например: Манометр М-100", max_chars=200)
    st.text_input("Производитель, если знаете", key="manufacturer", placeholder="Можно оставить пустым", max_chars=300)
    st.text_area("Характеристики", key="specs_text", height=140,
                 placeholder="Диапазон измерений: 0–1,6 МПа\nКласс точности: 1,5\nДиаметр корпуса: 100 мм")
    st.caption("Каждую характеристику пишите с новой строки: название, двоеточие, значение.")
    st.text_area("Описание из паспорта, если есть", key="description", height=100,
                 placeholder="Вставьте исходное описание товара. Если его нет, оставьте пустым.", max_chars=12000)

    with st.expander("Настройки"):
        entered_key = st.text_input("API-ключ OpenRouter", type="password", key="api_key_input")
        api_key = entered_key.strip() or saved_key.strip()
        st.caption("Ключ подключён. Можно пользоваться." if api_key else "Подключите ключ OpenRouter, чтобы создавать описания.")
        model_id = st.selectbox("Бесплатная модель", DEFAULT_MODELS, key="selected_model")
        repair = st.checkbox("Одна попытка исправить ответ", value=True,
                             help="При ошибке объёма или фактов приложение просит модель исправить карточку один раз.")
        st.selectbox("Другой учебный пример", list(EXAMPLES), key="example_label")
        st.caption("После выбора примера нажмите «Заполнить пример» над формой.")

    if st.button("Создать описание", type="primary", width="stretch"):
        st.session_state.pop("report", None)
        st.session_state.pop("generation_error", None)
        try:
            product = form_product()
            with st.spinner("Пишем описание. Обычно это занимает несколько десятков секунд…"):
                report = generate_live(product, api_key, model_id, repair)
            st.session_state["report"] = report.model_dump()
        except (InputError, ProviderError, ValueError) as exc:
            st.session_state["generation_error"] = str(exc)
    if st.session_state.get("generation_error"):
        st.error(st.session_state["generation_error"])
    if st.session_state.get("report"):
        render_report(Report.model_validate(st.session_state["report"]))


if __name__ == "__main__":
    main()
