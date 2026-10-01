"""
Вспомогательные функции: ввод в консоли, выбор из списка, логирование, ошибки.
"""

import logging
import sys
from pathlib import Path

# Папка проекта — там, где лежит этот файл. Все относительные пути из config.py
# считаются от неё, поэтому программу можно запускать из любой текущей папки.
PROJECT_DIR = Path(__file__).resolve().parent

LINE = "=" * 40

logger = logging.getLogger("pdfchek")


class AppError(Exception):
    """
    «Понятная» ошибка для обычного пользователя.

    Когда программа выбрасывает AppError, main.py показывает только текст
    сообщения, без длинного traceback.
    """


class UserCancelled(Exception):
    """Пользователь сам решил прервать работу (ввёл q, ответил «n» и т.п.)."""


def resolve_path(path_text):
    """Превращает путь из config.py в абсолютный путь (относительно папки проекта)."""
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------

def setup_logging(log_dir, debug=False):
    """
    Настраивает логирование.

    - В файл output/logs/app.log пишется всё подробно (уровень DEBUG).
    - В консоль лог выводится только в режиме DEBUG, чтобы не мешать пользователю.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(file_handler)

    if debug:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(logging.Formatter("  [log %(levelname)s] %(message)s"))
        logger.addHandler(console_handler)

    # WeasyPrint и fontTools очень «разговорчивы» — оставляем от них только ошибки
    for noisy in ("weasyprint", "fontTools"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    return log_file


def prepare_console():
    """
    Защита от ошибок вывода кириллицы в «старых» консолях Windows:
    символ, который нельзя показать, заменится на «?» вместо падения программы.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Ввод в консоли
# ---------------------------------------------------------------------------

def ask(prompt):
    """
    Читает строку, введённую пользователем.
    Ввод "q" (или "й" — если забыли переключить раскладку) завершает программу.
    """
    try:
        answer = input(prompt).strip()
    except EOFError:
        raise UserCancelled()
    if answer.lower() in ("q", "quit", "exit", "й", "выход"):
        raise UserCancelled()
    return answer


def ask_yes_no(question, default=None):
    """Задаёт вопрос «да/нет». Возвращает True или False."""
    hint = "[y/n]"
    if default is True:
        hint = "[Y/n]"
    elif default is False:
        hint = "[y/N]"

    while True:
        answer = ask(f"{question} {hint}: ").lower()
        if not answer and default is not None:
            return default
        # Поддерживаем и русскую раскладку: «н» = y, «т» = n, а также «да»/«нет»
        if answer in ("y", "yes", "д", "да", "н"):
            return True
        if answer in ("n", "no", "нет", "т"):
            return False
        print("  Введите y (да) или n (нет).")


def select_from_list(title, items, zero_option=None, default=None):
    """
    Показывает нумерованный список и просит выбрать один пункт.

    title       — заголовок, например «Выберите шаблон»
    items       — список строк для показа
    zero_option — текст для пункта [0] (например «Без логотипа»), если он нужен
    default     — номер пункта, который выбирается по Enter

    Возвращает индекс выбранного элемента в items (с нуля) или None,
    если выбран пункт [0].
    """
    print()
    print(title)
    print()
    if zero_option:
        print(f"  [0] {zero_option}")
    for number, item in enumerate(items, start=1):
        print(f"  [{number}] {item}")
    print()

    prompt = "Ваш выбор"
    if default is not None:
        prompt += f" (Enter = {default})"
    prompt += ": "

    min_number = 0 if zero_option else 1
    while True:
        answer = ask(prompt)
        if not answer and default is not None:
            answer = str(default)
        if answer.isdigit():
            number = int(answer)
            if number == 0 and zero_option:
                return None
            if min_number <= number <= len(items) and number != 0:
                return number - 1
        print(f"  Введите число от {min_number} до {len(items)} (или q для выхода).")


def parse_selection(text, total):
    """
    Разбирает строку выбора записей.

    Поддерживаются варианты:
        "1"        -> [0]
        "1,3"      -> [0, 2]
        "2-5"      -> [1, 2, 3, 4]
        "1, 4-6"   -> [0, 3, 4, 5]
        "all"/"все"/"*" -> все записи

    Возвращает список индексов (с нуля) без повторов, в порядке ввода.
    Если строка содержит ошибку — выбрасывает ValueError с понятным текстом.
    """
    text = text.strip().lower()
    if text in ("all", "все", "всё", "*", "фдд"):
        return list(range(total))

    indexes = []
    # Разрешаем разделять номера запятой, точкой с запятой или пробелом
    parts = text.replace(";", ",").replace(" ", ",").split(",")
    for part in parts:
        if not part:
            continue
        if "-" in part:
            start_text, _, end_text = part.partition("-")
            if not (start_text.isdigit() and end_text.isdigit()):
                raise ValueError(f"не понимаю диапазон «{part}»")
            start, end = int(start_text), int(end_text)
            if start > end:
                start, end = end, start
            numbers = range(start, end + 1)
        elif part.isdigit():
            numbers = [int(part)]
        else:
            raise ValueError(f"«{part}» — не номер записи")

        for number in numbers:
            if not 1 <= number <= total:
                raise ValueError(f"записи с номером {number} нет (всего записей: {total})")
            if number - 1 not in indexes:
                indexes.append(number - 1)

    if not indexes:
        raise ValueError("не указано ни одного номера")
    return indexes
