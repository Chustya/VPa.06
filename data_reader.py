"""
Чтение данных из CSV, XLS и XLSX.

Результат чтения любого файла одинаковый:
    columns — список названий колонок (из первой строки файла)
    records — список записей; каждая запись — словарь {колонка: значение-строка}

Благодаря этому шаблону всё равно, откуда пришли данные.
Исходные файлы только читаются и никогда не изменяются.
"""

import csv
import datetime
import io
import math
import numbers

from utils import AppError, logger


class EncodingDetectionError(AppError):
    """Не получилось автоматически определить кодировку CSV."""


# ---------------------------------------------------------------------------
# Общая часть для CSV и Excel
# ---------------------------------------------------------------------------

def build_records(rows, file_name):
    """
    Превращает «сырые» строки таблицы (списки строк) в columns + records.
    Первая непустая строка — названия колонок.
    """
    # Полностью пустые строки (например, в конце файла) пропускаем
    numbered_rows = [
        (line_number, row)
        for line_number, row in enumerate(rows, start=1)
        if any(cell.strip() for cell in row)
    ]
    if not numbered_rows:
        raise AppError(f"Файл «{file_name}» пустой — в нём нет ни заголовков, ни данных.")

    header_line, header = numbered_rows[0]
    columns = clean_column_names(header, file_name)

    records = []
    for line_number, row in numbered_rows[1:]:
        if len(row) > len(columns):
            extra_cells = row[len(columns):]
            if any(cell.strip() for cell in extra_cells):
                raise AppError(
                    f"Неправильная структура файла «{file_name}».\n"
                    f"В строке {line_number} значений: {len(row)}, "
                    f"а колонок в заголовке: {len(columns)}.\n"
                    "Проверьте разделитель и кавычки в этой строке."
                )
            row = row[:len(columns)]
        elif len(row) < len(columns):
            logger.debug("Строка %s короче заголовка — недостающие ячейки будут пустыми", line_number)
            row = row + [""] * (len(columns) - len(row))

        records.append(dict(zip(columns, row)))

    if not records:
        raise AppError(
            f"В файле «{file_name}» есть только строка заголовков, записей нет."
        )
    return columns, records


def clean_column_names(header, file_name):
    """
    Приводит названия колонок в порядок:
    - убирает пробелы по краям;
    - пустое название заменяет на column_3 и т.п.;
    - повторяющиеся названия делает уникальными: amount, amount_2.
    """
    columns = []
    for index, name in enumerate(header, start=1):
        name = name.strip()
        if not name:
            name = f"column_{index}"
        if name.lower() in (c.lower() for c in columns):
            new_name = f"{name}_{index}"
            print(f"  Внимание: в «{file_name}» колонка «{name}» повторяется, "
                  f"вторая переименована в «{new_name}».")
            name = new_name
        columns.append(name)
    return columns


def find_column(columns, candidates):
    """Ищет первую подходящую колонку из списка кандидатов (без учёта регистра)."""
    lower_map = {column.lower(): column for column in columns}
    for candidate in candidates:
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]
    return None


def get_record_label(record, columns, config):
    """
    Короткая подпись записи для списка выбора: «Иванов Иван — № 1001».
    Если подходящих колонок нет — первые два непустых значения.
    """
    name_column = find_column(columns, config.RECORD_NAME_COLUMNS)
    number_column = find_column(columns, config.RECORD_NUMBER_COLUMNS)

    name = record.get(name_column, "") if name_column else ""
    number = record.get(number_column, "") if number_column else ""

    if name or number:
        parts = [name] if name else []
        if number:
            parts.append(f"№ {number}")
        label = " — ".join(parts)
    else:
        values = [value for value in record.values() if value.strip()]
        label = " — ".join(values[:2]) or "(пустая запись)"

    # Длинные подписи обрезаем, чтобы список оставался читаемым
    label = " ".join(label.split())
    return label if len(label) <= 80 else label[:77] + "..."


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

# Символы из верхней половины Windows-1251, которые почти не встречаются
# в обычном тексте. Если их много — скорее всего, кодировка определена неверно.
_UNUSUAL_CP1251_CHARS = set("ЂЃ‚ѓ„…†‡€‰Љ‹ЊЌЋЏђ‘’“”•™љ›њќћџЎўЈ¤Ґ¦§©Є¬®Ї°±Ііґµ¶·єјЅѕї")


def _looks_like_cp1251(text):
    non_ascii = [char for char in text if ord(char) > 127]
    if not non_ascii:
        return True
    unusual = sum(1 for char in non_ascii if char in _UNUSUAL_CP1251_CHARS)
    return unusual / len(non_ascii) < 0.05


def decode_csv_bytes(raw_bytes, file_name, encoding=None):
    """
    Превращает байты файла в текст.

    Если кодировка не указана — пробуем по очереди:
      1. UTF-8 (с BOM или без — "utf-8-sig" понимает оба варианта);
      2. Windows-1251 (типичная кодировка CSV из русского Excel).
    Возвращает (текст, название_кодировки).
    """
    if encoding:
        try:
            return raw_bytes.decode(encoding), encoding
        except LookupError:
            raise AppError(f"Неизвестная кодировка «{encoding}». Примеры: utf-8, cp1251.")
        except UnicodeDecodeError:
            raise EncodingDetectionError(
                f"Файл «{file_name}» не читается в кодировке «{encoding}»."
            )

    try:
        text = raw_bytes.decode("utf-8-sig")
        has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
        return text, "UTF-8 with BOM" if has_bom else "UTF-8"
    except UnicodeDecodeError:
        pass

    try:
        text = raw_bytes.decode("cp1251")
        if _looks_like_cp1251(text):
            return text, "Windows-1251"
    except UnicodeDecodeError:
        pass

    raise EncodingDetectionError(
        f"Не удалось автоматически определить кодировку файла «{file_name}».\n"
        "Поддерживаются UTF-8, UTF-8 with BOM и Windows-1251."
    )


def detect_delimiter(text, forced_delimiter=None):
    """Определяет разделитель колонок: запятая, точка с запятой, табуляция или |."""
    if forced_delimiter:
        return forced_delimiter

    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # Sniffer не справился — выбираем символ, который чаще всего встречается в заголовке
        first_line = text.splitlines()[0] if text.strip() else ""
        counts = {d: first_line.count(d) for d in (";", ",", "\t", "|")}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","


def read_csv(path, config, encoding=None):
    """Читает CSV-файл. Возвращает (columns, records, описание_формата)."""
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError:
        raise AppError(f"Файл данных не найден: {path}")
    except PermissionError:
        raise AppError(f"Нет доступа к файлу «{path.name}». Закройте его в других программах.")

    if not raw_bytes.strip():
        raise AppError(f"Файл «{path.name}» пустой.")

    text, used_encoding = decode_csv_bytes(raw_bytes, path.name, encoding or config.CSV_ENCODING)
    delimiter = detect_delimiter(text, config.CSV_DELIMITER)

    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as error:
        raise AppError(f"Неправильная структура CSV-файла «{path.name}»: {error}")

    columns, records = build_records(rows, path.name)
    delimiter_names = {",": "запятая", ";": "точка с запятой", "\t": "табуляция", "|": "|"}
    info = f"кодировка {used_encoding}, разделитель: {delimiter_names.get(delimiter, delimiter)}"
    logger.info("CSV %s прочитан: %s", path.name, info)
    return columns, records, info


# ---------------------------------------------------------------------------
# Excel (XLS / XLSX)
# ---------------------------------------------------------------------------

def format_cell(value):
    """
    Превращает значение ячейки Excel в строку так, как его ожидает увидеть человек:
      пустая ячейка -> ""
      5000.0        -> "5000"
      дата          -> "30.09.2026"
    """
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, datetime.datetime):  # сюда же попадает pandas.Timestamp
        if value != value:  # NaT (пустая дата) не равна сама себе
            return ""
        if (value.hour, value.minute, value.second) == (0, 0, 0):
            return value.strftime("%d.%m.%Y")
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, datetime.date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M")
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        value = float(value)
        if value.is_integer():
            return str(int(value))
        return format(value, ".15g")
    text = str(value)
    return "" if text in ("NaT", "nan") else text.strip()


def _import_pandas():
    try:
        import pandas
        return pandas
    except ImportError:
        raise AppError(
            "Для чтения Excel-файлов нужна библиотека pandas.\n"
            "Установите зависимости командой:\n"
            "    pip install -r requirements.txt"
        )


def read_excel(path, config, sheet_chooser=None):
    """
    Читает XLS или XLSX через pandas.
      .xlsx — движок openpyxl
      .xls  — движок xlrd

    sheet_chooser — функция, которая спросит пользователя, какой лист взять,
    если листов несколько. Получает список имён листов, возвращает имя.
    """
    pandas = _import_pandas()
    engine = "xlrd" if path.suffix.lower() == ".xls" else "openpyxl"
    engine_package = {"xlrd": "xlrd", "openpyxl": "openpyxl"}[engine]

    try:
        # Открываем файл только для чтения; исходный файл не изменяется
        with pandas.ExcelFile(path, engine=engine) as workbook:
            sheet_names = workbook.sheet_names
            sheet = sheet_names[0]
            if len(sheet_names) > 1 and sheet_chooser:
                sheet = sheet_chooser(sheet_names)
            # header=None: заголовки обрабатываем сами (как в CSV)
            # dtype=object: значения не приводятся к одному типу — мы форматируем их в format_cell
            table = workbook.parse(sheet, header=None, dtype=object)
    except ImportError:
        raise AppError(
            f"Для чтения файлов {path.suffix} нужна библиотека {engine_package}.\n"
            f"Установите её командой:\n    pip install {engine_package}"
        )
    except FileNotFoundError:
        raise AppError(f"Файл данных не найден: {path}")
    except PermissionError:
        raise AppError(
            f"Нет доступа к файлу «{path.name}».\n"
            "Если он открыт в Excel — закройте его и запустите программу снова."
        )
    except AppError:
        raise
    except Exception as error:  # повреждённый файл, неверный формат и т.п.
        logger.exception("Ошибка чтения Excel %s", path)
        raise AppError(
            f"Не удалось прочитать Excel-файл «{path.name}».\n"
            f"Возможно, файл повреждён или имеет другой формат.\n"
            f"Техническая причина: {error}"
        )

    rows = [[format_cell(value) for value in row] for row in table.itertuples(index=False)]
    columns, records = build_records(rows, path.name)
    info = f"лист «{sheet}»"
    logger.info("Excel %s прочитан: %s", path.name, info)
    return columns, records, info


def read_data_file(path, config, encoding=None, sheet_chooser=None):
    """Читает файл данных любого поддерживаемого формата."""
    if not path.exists():
        raise AppError(f"Файл данных не найден: {path}")
    if path.stat().st_size == 0:
        raise AppError(f"Файл «{path.name}» пустой (0 байт). Откройте его и заполните данными.")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_csv(path, config, encoding)
    if suffix in (".xls", ".xlsx"):
        return read_excel(path, config, sheet_chooser)
    raise AppError(f"Формат «{suffix}» не поддерживается. Используйте CSV, XLS или XLSX.")
