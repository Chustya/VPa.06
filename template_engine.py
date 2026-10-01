"""
Простой шаблонизатор: подстановка {{плейсхолдеров}} в HTML.

Плейсхолдер — это имя колонки в двойных фигурных скобках: {{name}}, {{amount}}.
Регистр и пробелы внутри скобок не важны: {{ Name }} == {{name}}.
Можно использовать и русские названия колонок: {{Клиент}}.

Специальные плейсхолдеры (не требуют колонок в данных):
    {{logo}}             — логотип, выбранный при запуске
    {{all_fields_table}} — таблица со всеми полями записи
    {{today}}            — сегодняшняя дата (ДД.ММ.ГГГГ)
    {{record_number}}    — порядковый номер записи в файле
"""

import base64
import html
import re
from xml.etree import ElementTree

from utils import AppError, logger

# \{\{ ... \}\} — двойные фигурные скобки, внутри — любые символы, кроме скобок
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

SPECIAL_PLACEHOLDERS = ("logo", "all_fields_table", "today", "record_number")


# ---------------------------------------------------------------------------
# Шаблон
# ---------------------------------------------------------------------------

def load_template(path):
    """Читает HTML-шаблон (UTF-8; при неудаче — Windows-1251). Шаблон не изменяется."""
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError:
        raise AppError(f"Шаблон не найден: {path}")
    except OSError as error:
        raise AppError(f"Не удалось прочитать шаблон «{path.name}»: {error}")

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("cp1251", errors="replace")
        print(f"  Внимание: шаблон «{path.name}» не в кодировке UTF-8. "
              "Рекомендуется сохранить его в UTF-8.")

    if not text.strip():
        raise AppError(f"Шаблон «{path.name}» пустой.")
    return text


def extract_placeholders(template_text):
    """Возвращает список плейсхолдеров шаблона (без повторов, в порядке появления)."""
    names = []
    for match in PLACEHOLDER_PATTERN.finditer(template_text):
        name = match.group(1)
        if name.lower() not in (n.lower() for n in names):
            names.append(name)
    return names


def find_missing_placeholders(placeholders, columns):
    """Плейсхолдеры, для которых нет колонки в данных (специальные не считаются)."""
    column_names = {column.lower() for column in columns}
    return [
        name for name in placeholders
        if name.lower() not in column_names and name.lower() not in SPECIAL_PLACEHOLDERS
    ]


def escape_value(value):
    """
    Экранирует значение для HTML: < > & " ' превращаются в безопасные сущности,
    поэтому данные не могут «сломать» разметку. Переносы строк -> <br>.
    """
    escaped = html.escape(str(value), quote=True)
    return escaped.replace("\r\n", "\n").replace("\n", "<br>")


def render_template(template_text, record, special_values, missing_value=""):
    """
    Подставляет значения записи в шаблон.

    record         — словарь {колонка: значение} (значения будут экранированы)
    special_values — словарь для специальных плейсхолдеров; это уже готовый HTML,
                     он НЕ экранируется (его формирует сама программа)
    """
    # Словарь «имя в нижнем регистре -> значение» для поиска без учёта регистра
    values = {column.strip().lower(): value for column, value in record.items()}
    special = {name.lower(): value for name, value in special_values.items()}

    def replace(match):
        key = match.group(1).lower()
        if key in values:
            return escape_value(values[key])
        if key in special:
            return special[key]
        return escape_value(missing_value)

    return PLACEHOLDER_PATTERN.sub(replace, template_text)


def build_all_fields_table(record):
    """HTML-таблица «Поле — Значение» со всеми полями записи (для {{all_fields_table}})."""
    rows = "\n".join(
        f"    <tr><th>{escape_value(column)}</th><td>{escape_value(value)}</td></tr>"
        for column, value in record.items()
    )
    return f'<table class="all-fields">\n{rows}\n</table>'


# ---------------------------------------------------------------------------
# Логотип
# ---------------------------------------------------------------------------

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def build_logo_html(logo_path):
    """
    Готовит тег <img> с логотипом.

    Картинка встраивается прямо в HTML (data URI, base64). Так логотип
    гарантированно найдётся и в HTML-файле, и в WeasyPrint — независимо от того,
    где лежит исходный файл и есть ли в пути русские буквы или пробелы.
    """
    suffix = logo_path.suffix.lower()
    mime_type = _IMAGE_TYPES.get(suffix)
    if not mime_type:
        raise AppError(f"Формат логотипа «{suffix}» не поддерживается. Используйте PNG или JPG.")

    try:
        data = logo_path.read_bytes()
    except OSError as error:
        raise AppError(f"Не удалось прочитать логотип «{logo_path.name}»: {error}")

    if not data:
        raise AppError(f"Файл логотипа «{logo_path.name}» пустой.")

    # Проверяем «подпись» файла: действительно ли это картинка заявленного формата
    signature_ok = {
        ".png": data.startswith(b"\x89PNG"),
        ".jpg": data.startswith(b"\xff\xd8"),
        ".jpeg": data.startswith(b"\xff\xd8"),
        ".svg": b"<svg" in data[:4096].lower(),
    }[suffix]
    if not signature_ok:
        raise AppError(
            f"Файл «{logo_path.name}» не похож на изображение {suffix.upper()[1:]}.\n"
            "Возможно, он повреждён или у него неправильное расширение."
        )

    if suffix == ".svg":
        # WeasyPrint читает SVG как XML: при любой ошибке в разметке вместо
        # логотипа молча появится текст «Логотип». Поэтому проверяем заранее.
        try:
            ElementTree.fromstring(data)
        except ElementTree.ParseError as error:
            raise AppError(
                f"SVG-файл «{logo_path.name}» содержит ошибку и не будет показан в PDF ({error}).\n"
                "Пересохраните его в редакторе или используйте логотип в формате PNG."
            )

    encoded = base64.b64encode(data).decode("ascii")
    logger.debug("Логотип %s встроен в HTML (%s байт)", logo_path.name, len(data))
    return f'<img class="logo" src="data:{mime_type};base64,{encoded}" alt="Логотип">'


# ---------------------------------------------------------------------------
# Вставка служебных блоков в <head> и <body>
# ---------------------------------------------------------------------------

def insert_into_head(html_text, head_html):
    """
    Вставляет блок в самое начало <head>. Стили шаблона идут ПОСЛЕ него,
    поэтому шаблон может переопределить любые общие правила.
    """
    head_match = re.search(r"<head[^>]*>", html_text, re.IGNORECASE)
    if head_match:
        position = head_match.end()
        return html_text[:position] + "\n" + head_html + html_text[position:]

    html_match = re.search(r"<html[^>]*>", html_text, re.IGNORECASE)
    if html_match:
        position = html_match.end()
        return html_text[:position] + "\n<head>\n" + head_html + "</head>\n" + html_text[position:]

    return "<head>\n" + head_html + "</head>\n" + html_text


def insert_after_body_start(html_text, body_html):
    """Вставляет блок сразу после <body> (или в начало документа, если <body> нет)."""
    body_match = re.search(r"<body[^>]*>", html_text, re.IGNORECASE)
    if body_match:
        position = body_match.end()
        return html_text[:position] + "\n" + body_html + "\n" + html_text[position:]
    return body_html + "\n" + html_text


def build_head_html(base_css, base_url=None, add_charset=True):
    """Служебный блок для <head>: кодировка, базовый адрес и общий CSS."""
    parts = []
    if add_charset:
        parts.append('<meta charset="utf-8">')
    if base_url:
        # <base> нужен, чтобы относительные ссылки шаблона (картинки, css)
        # работали и из папки output/html
        parts.append(f'<base href="{html.escape(base_url, quote=True)}">')
    parts.append("<style>\n" + base_css + "\n</style>")
    return "\n".join(parts) + "\n"


def build_base_css(font_face_css, logo_max_width, logo_max_height):
    """
    Общие стили, которые добавляются в каждый документ:
    шрифты с кириллицей, аккуратные таблицы, перенос длинных строк, размер логотипа.
    """
    return f"""{font_face_css}
html, body {{
    font-family: "Roboto", "Liberation Sans", sans-serif;
}}
body {{
    /* длинные слова и ссылки переносятся, а не вылезают за край страницы */
    overflow-wrap: break-word;
    word-wrap: break-word;
}}
table {{
    border-collapse: collapse;
    max-width: 100%;
    table-layout: auto;   /* ширина колонок подбирается автоматически */
}}
th, td {{
    vertical-align: top;
    word-wrap: break-word;
    overflow-wrap: break-word;
    word-break: break-word;
    overflow-wrap: anywhere; /* позволяет таблице сжиматься даже при очень длинных словах */
    hyphens: auto;
}}
thead {{ display: table-header-group; }}  /* шапка таблицы повторяется на новой странице */
tr {{ break-inside: avoid; page-break-inside: avoid; }}
img {{ max-width: 100%; }}
.logo {{
    display: block;
    max-width: {logo_max_width};
    max-height: {logo_max_height};
    width: auto;
    height: auto;          /* пропорции логотипа сохраняются */
    object-fit: contain;
}}
.logo-container {{ margin-bottom: 10px; }}
table.all-fields {{ width: 100%; }}
table.all-fields th, table.all-fields td {{
    border: 1px solid #999;
    padding: 4px 6px;
    text-align: left;
}}
table.all-fields th {{ width: 35%; background: #f2f2f2; }}
"""
