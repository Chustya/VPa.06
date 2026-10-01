"""
Работа с файлами: поиск шаблонов/данных/логотипов, безопасные имена файлов,
открытие PDF системной программой.
"""

import os
import platform
import re
import subprocess
from pathlib import Path

from utils import PROJECT_DIR, AppError, logger, resolve_path

TEMPLATE_EXTENSIONS = (".html", ".htm")
DATA_EXTENSIONS = (".csv", ".xls", ".xlsx")
LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg")


# ---------------------------------------------------------------------------
# Поиск файлов
# ---------------------------------------------------------------------------

def find_files(directories, extensions, search_subfolders=False):
    """
    Ищет файлы с нужными расширениями во всех указанных папках.

    directories — список путей из config.py
    extensions  — кортеж расширений, например (".csv", ".xlsx")

    Возвращает (найденные_файлы, несуществующие_папки).
    Файлы отсортированы по имени, повторы (одна папка указана дважды) убраны.
    """
    found_files = []
    missing_dirs = []
    seen = set()

    for directory_text in directories:
        directory = resolve_path(directory_text)
        if not directory.is_dir():
            missing_dirs.append(directory)
            logger.warning("Папка не найдена: %s", directory)
            continue

        pattern = "**/*" if search_subfolders else "*"
        files_here = []
        for path in directory.glob(pattern):
            # Пропускаем временные файлы Excel (~$file.xlsx) и скрытые файлы
            if path.name.startswith(("~$", ".")):
                continue
            # Сравниваем расширение без учёта регистра: .CSV == .csv
            if path.is_file() and path.suffix.lower() in extensions:
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    files_here.append(resolved)

        found_files.extend(sorted(files_here, key=lambda p: p.name.lower()))

    return found_files, missing_dirs


def _warn_missing_dirs(kind, missing_dirs):
    for directory in missing_dirs:
        print(f"  Внимание: папка для {kind} не найдена: {directory}")


def find_templates(config):
    """Находит HTML-шаблоны. Если ни одного нет — понятная ошибка."""
    files, missing = find_files(config.TEMPLATE_DIRS, TEMPLATE_EXTENSIONS, config.SEARCH_SUBFOLDERS)
    _warn_missing_dirs("шаблонов", missing)
    if not files:
        folders = ", ".join(str(resolve_path(d)) for d in config.TEMPLATE_DIRS)
        raise AppError(
            "Не найдено ни одного HTML-шаблона (.html, .htm).\n"
            f"Положите шаблон в одну из папок: {folders}\n"
            "или измените TEMPLATE_DIRS в config.py."
        )
    return files


def find_data_files(config):
    """Находит файлы с данными CSV/XLS/XLSX."""
    files, missing = find_files(config.DATA_DIRS, DATA_EXTENSIONS, config.SEARCH_SUBFOLDERS)
    _warn_missing_dirs("данных", missing)
    if not files:
        folders = ", ".join(str(resolve_path(d)) for d in config.DATA_DIRS)
        raise AppError(
            "Не найдено ни одного файла с данными (.csv, .xls, .xlsx).\n"
            f"Положите файл в одну из папок: {folders}\n"
            "или измените DATA_DIRS в config.py."
        )
    return files


def find_logos(config):
    """Находит логотипы. Отсутствие логотипов — не ошибка (можно без логотипа)."""
    files, missing = find_files(config.LOGO_DIRS, LOGO_EXTENSIONS, config.SEARCH_SUBFOLDERS)
    _warn_missing_dirs("логотипов", missing)
    return files


def get_template_title(template_path):
    """
    Возвращает «человеческое» название шаблона из тега <title>.
    Например, для <title>Чек стандартный</title> вернётся «Чек стандартный».
    Если тега нет — вернётся имя файла без расширения.
    """
    try:
        text = template_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return template_path.stem
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if match:
        title = " ".join(match.group(1).split())
        # В заголовке тоже могут быть плейсхолдеры — они в списке не нужны
        title = re.sub(r"\{\{.*?\}\}", "", title).strip(" —-№")
        if title:
            return title
    return template_path.stem


def short_path(path):
    """Путь относительно папки проекта (для компактного вывода), если это возможно."""
    try:
        return str(path.relative_to(PROJECT_DIR))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Имена файлов
# ---------------------------------------------------------------------------

# Имена, которые Windows запрещает использовать для файлов (без учёта расширения)
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name, replacement="_", max_length=100):
    """
    Делает строку безопасной для имени файла в Windows и macOS.

    - заменяет запрещённые символы  < > : " / \\ | ? *  и управляющие символы;
    - убирает точки и пробелы в конце (Windows их не любит);
    - обходит зарезервированные имена Windows (CON, NUL, COM1 ...);
    - ограничивает длину имени.

    Пример: 'Заказ №12/3: "срочно"' -> 'Заказ №12_3_ _срочно_'
    """
    name = str(name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', replacement, name)
    name = " ".join(name.split())  # переносы строк и лишние пробелы -> один пробел
    name = name.strip(" .")
    name = name[:max_length].strip(" .")

    if not name:
        name = "file"
    if name.split(".")[0].upper() in WINDOWS_RESERVED_NAMES:
        name = replacement + name
    return name


def make_unique_name(base_name, used_names):
    """
    Если имя уже использовалось в этой генерации, добавляет суффикс: check_1001_2.
    Сравнение без учёта регистра — в Windows и macOS "A.pdf" и "a.pdf" это один файл.
    """
    candidate = base_name
    counter = 2
    while candidate.lower() in used_names:
        candidate = f"{base_name}_{counter}"
        counter += 1
    used_names.add(candidate.lower())
    return candidate


# ---------------------------------------------------------------------------
# Открытие файлов
# ---------------------------------------------------------------------------

def open_file(path):
    """
    Открывает файл (или папку) программой, назначенной в системе по умолчанию.
    Для PDF это обычно Adobe Reader, «Просмотр» (macOS) и т.п.

    Возвращает True при успехе, False при ошибке (ошибка не критична).
    """
    path = str(Path(path))
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)  # есть только в Windows
        elif system == "Darwin":  # macOS
            subprocess.run(["open", path], check=True)
        else:  # Linux и прочие
            subprocess.run(["xdg-open", path], check=True)
        logger.info("Открыт файл: %s", path)
        return True
    except (OSError, subprocess.CalledProcessError) as error:
        logger.warning("Не удалось открыть %s: %s", path, error)
        print(f"  Не удалось открыть автоматически: {path}")
        print("  Откройте файл вручную. Возможно, в системе не назначена программа для PDF.")
        return False
