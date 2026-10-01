"""
Создание HTML- и PDF-файлов, подключение шрифтов.

WeasyPrint — библиотека, которая «рисует» HTML+CSS в PDF.
Ей нужны системные библиотеки Pango/GTK. Если их нет, импорт WeasyPrint
падает с OSError — программа покажет понятную инструкцию.
"""

import os
import platform
from pathlib import Path

from utils import AppError, logger, resolve_path

FONT_EXTENSIONS = (".ttf", ".otf")

WEASYPRINT_DOCS = "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"


# ---------------------------------------------------------------------------
# Проверка WeasyPrint
# ---------------------------------------------------------------------------

def load_weasyprint(dll_dir=None):
    """
    Пытается загрузить WeasyPrint.
    Возвращает (модуль, None) при успехе или (None, текст_проблемы) при ошибке.

    dll_dir — (только Windows) папка с библиотеками Pango из MSYS2.
    Используется, если переменная окружения WEASYPRINT_DLL_DIRECTORIES не задана.
    """
    if (platform.system() == "Windows" and dll_dir
            and not os.environ.get("WEASYPRINT_DLL_DIRECTORIES") and Path(dll_dir).is_dir()):
        os.environ["WEASYPRINT_DLL_DIRECTORIES"] = str(dll_dir)
        logger.debug("WEASYPRINT_DLL_DIRECTORIES = %s", dll_dir)

    try:
        import weasyprint
    except ImportError:
        return None, (
            "Библиотека WeasyPrint не установлена — PDF создать не получится.\n"
            "Установите её командой:\n"
            "    pip install weasyprint"
        )
    except OSError as error:
        # Библиотека Python есть, но нет системных библиотек (Pango, GObject)
        logger.error("WeasyPrint не запускается: %s", error)
        if platform.system() == "Windows":
            how_to_fix = (
                "Для Windows:\n"
                "  1. Установите MSYS2: https://www.msys2.org/\n"
                "  2. В окне MSYS2 выполните: pacman -S mingw-w64-x86_64-pango\n"
                "  3. Если MSYS2 установлен не в C:\\msys64 — укажите путь к папке mingw64\\bin\n"
                "     в настройке WEASYPRINT_DLL_DIR в config.py.\n"
                "  Подробно — в README.md, раздел «Установка WeasyPrint»."
            )
        elif platform.system() == "Darwin":
            how_to_fix = "Для macOS выполните в терминале:\n    brew install pango"
        else:
            how_to_fix = "Для Linux установите пакет pango (например: sudo apt install libpango-1.0-0 libpangoft2-1.0-0)."
        return None, (
            "WeasyPrint установлен, но не может запуститься: не хватает системных библиотек.\n"
            f"{how_to_fix}\n"
            f"Подробнее: {WEASYPRINT_DOCS}\n"
            f"Техническая причина: {error}"
        )
    return weasyprint, None


# ---------------------------------------------------------------------------
# Шрифты
# ---------------------------------------------------------------------------

def _is_font_file(path):
    """Проверяет первые байты файла: это действительно TTF/OTF-шрифт?"""
    try:
        with open(path, "rb") as font_file:
            header = font_file.read(4)
    except OSError:
        return False
    return header in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")


def find_font_files(config):
    """Находит файлы шрифтов в FONT_DIR и в списке FONT_FILES."""
    font_files = []

    font_dir = resolve_path(config.FONT_DIR) if config.FONT_DIR else None
    if font_dir and font_dir.is_dir():
        font_files.extend(
            sorted(p for p in font_dir.iterdir() if p.suffix.lower() in FONT_EXTENSIONS)
        )
    elif font_dir:
        logger.warning("Папка шрифтов не найдена: %s", font_dir)

    for font_text in config.FONT_FILES:
        font_path = resolve_path(font_text)
        if font_path.is_file():
            font_files.append(font_path)
        else:
            print(f"  Внимание: файл шрифта не найден: {font_path}")
            logger.warning("Файл шрифта не найден: %s", font_path)

    good_fonts = []
    for font_path in font_files:
        if _is_font_file(font_path):
            good_fonts.append(font_path)
        else:
            print(f"  Внимание: файл «{font_path.name}» не похож на шрифт и будет пропущен.")
            logger.warning("Повреждённый или неверный файл шрифта: %s", font_path)
    return good_fonts


def _describe_font(font_path):
    """
    По имени файла определяет семейство, толщину и начертание шрифта.
    Например: Roboto-BoldItalic.ttf -> ("Roboto", 700, "italic")
    """
    name = font_path.stem.lower().replace("_", "-").replace(" ", "-")

    if "roboto" in name:
        family = "Roboto"
    elif "liberation" in name and "sans" in name:
        family = "Liberation Sans"
    else:
        family = font_path.stem.split("-")[0]

    weight = 400
    for word, value in (("thin", 100), ("light", 300), ("medium", 500),
                        ("semibold", 600), ("bold", 700), ("black", 900)):
        if word in name:
            weight = value
    style = "italic" if "italic" in name else "normal"
    return family, weight, style


def build_font_face_css(font_files):
    """
    Создаёт правила @font-face для найденных шрифтов.
    Шрифт подключается прямо из файла, поэтому не нужно устанавливать его в систему.
    """
    rules = []
    for font_path in font_files:
        family, weight, style = _describe_font(font_path)
        # as_uri() превращает путь в file:///..., корректно кодируя русские буквы и пробелы
        rules.append(
            "@font-face {\n"
            f'    font-family: "{family}";\n'
            f'    src: url("{font_path.resolve().as_uri()}");\n'
            f"    font-weight: {weight};\n"
            f"    font-style: {style};\n"
            "}"
        )
        logger.debug("Шрифт подключён: %s (%s, %s, %s)", font_path.name, family, weight, style)
    return "\n".join(rules)


# ---------------------------------------------------------------------------
# Создание файлов
# ---------------------------------------------------------------------------

def create_html(html_text, html_path):
    """Сохраняет готовый HTML в файл (UTF-8)."""
    try:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_text, encoding="utf-8")
    except PermissionError:
        raise AppError(f"Нет доступа к файлу {html_path.name}. Возможно, он открыт в другой программе.")
    except OSError as error:
        raise AppError(f"Не удалось создать HTML {html_path.name}: {error}")
    logger.info("HTML создан: %s", html_path)


def create_pdf(weasyprint, html_text, pdf_path, base_url):
    """
    Превращает HTML в PDF с помощью WeasyPrint.
    base_url — папка шаблона: от неё считаются относительные пути к картинкам и CSS.
    """
    try:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        document = weasyprint.HTML(string=html_text, base_url=str(base_url))
        document.write_pdf(str(pdf_path))
    except PermissionError:
        raise AppError(
            f"Не удалось записать {pdf_path.name}: файл занят.\n"
            "    Закройте этот PDF в программе просмотра и запустите генерацию снова."
        )
    except Exception as error:
        logger.exception("Ошибка создания PDF %s", pdf_path)
        raise AppError(f"Не удалось создать PDF {pdf_path.name}: {error}")
    logger.info("PDF создан: %s", pdf_path)
