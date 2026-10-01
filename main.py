"""
Генератор чеков в HTML и PDF.

Запуск:
    python main.py            — обычный режим
    python main.py --debug    — с подробной технической информацией об ошибках

Сценарий: шаблон -> файл данных -> записи -> логотип -> подтверждение -> генерация.
"""

import datetime
import sys
import traceback

import config
from data_reader import (
    EncodingDetectionError,
    find_column,
    get_record_label,
    read_data_file,
)
from file_manager import (
    find_data_files,
    find_logos,
    find_templates,
    get_template_title,
    make_unique_name,
    open_file,
    sanitize_filename,
    short_path,
)
from pdf_generator import (
    build_font_face_css,
    create_html,
    create_pdf,
    find_font_files,
    load_weasyprint,
)
from template_engine import (
    build_all_fields_table,
    build_base_css,
    build_head_html,
    build_logo_html,
    extract_placeholders,
    find_missing_placeholders,
    insert_after_body_start,
    insert_into_head,
    load_template,
    render_template,
)
from utils import (
    LINE,
    AppError,
    UserCancelled,
    ask,
    ask_yes_no,
    logger,
    parse_selection,
    prepare_console,
    resolve_path,
    select_from_list,
    setup_logging,
)


# ---------------------------------------------------------------------------
# Шаги выбора
# ---------------------------------------------------------------------------

def select_template():
    """Шаг 1. Выбор HTML-шаблона."""
    templates = find_templates(config)
    titles = [
        f"{get_template_title(path)} — {path.name}  ({short_path(path.parent)})"
        for path in templates
    ]
    if len(templates) == 1:
        print(f"\nНайден один шаблон: {titles[0]}")
        index = 0
    else:
        index = select_from_list("Выберите шаблон", titles)
    template_path = templates[index]
    logger.info("Выбран шаблон: %s", template_path)
    return template_path


def select_data_file():
    """Шаг 2. Выбор файла с данными."""
    data_files = find_data_files(config)
    names = [f"{path.name}  ({short_path(path.parent)})" for path in data_files]
    if len(data_files) == 1:
        print(f"\nНайден один файл данных: {names[0]}")
        index = 0
    else:
        index = select_from_list("Выберите файл данных", names)
    data_path = data_files[index]
    logger.info("Выбран файл данных: %s", data_path)
    return data_path


def choose_sheet(sheet_names):
    """Выбор листа, если в Excel-файле их несколько."""
    index = select_from_list("В файле несколько листов. Выберите лист", sheet_names, default=1)
    return sheet_names[index]


def load_records(data_path):
    """
    Читает выбранный файл. Если кодировку CSV не удалось определить —
    предлагает ввести её вручную.
    """
    encoding = None
    while True:
        try:
            return read_data_file(data_path, config, encoding=encoding, sheet_chooser=choose_sheet)
        except EncodingDetectionError as error:
            print(f"\n{error}")
            print("Введите кодировку вручную (например: utf-8, cp1251, cp866, utf-16)")
            encoding = ask("или нажмите Enter для выхода: ")
            if not encoding:
                raise UserCancelled()


def show_records(records, labels, indexes):
    """Печатает список записей с их номерами."""
    for index in indexes[:config.MAX_RECORDS_TO_SHOW]:
        print(f"  [{index + 1}] {labels[index]}")
    hidden = len(indexes) - config.MAX_RECORDS_TO_SHOW
    if hidden > 0:
        print(f"  ... и ещё {hidden}. Чтобы найти нужную запись, введите часть текста.")


def select_records(records, labels):
    """
    Шаг 3. Выбор записей.
    Можно ввести: 5 | 5,8,12 | 2-10 | all — или текст для поиска (например, фамилию).
    """
    print("\nДоступные записи:\n")
    show_records(records, labels, list(range(len(records))))

    while True:
        print()
        print('Введите номера через запятую (5,8,12), диапазон (2-5) или "all" — все записи.')
        print('Для поиска введите текст (например, фамилию) или ?текст.')
        answer = ask("Ваш выбор: ")
        if not answer:
            continue

        is_search = answer.startswith("?") or (
            any(char.isalpha() for char in answer)
            and answer.lower() not in ("all", "все", "всё", "фдд")
        )
        if is_search:
            search_text = answer.lstrip("?").strip().lower()
            found = [
                index for index, record in enumerate(records)
                if any(search_text in value.lower() for value in record.values())
            ]
            if found:
                print(f"\nНайдено: {len(found)}")
                show_records(records, labels, found)
            else:
                print("  Ничего не найдено.")
            continue

        try:
            indexes = parse_selection(answer, len(records))
        except ValueError as error:
            print(f"  Ошибка: {error}.")
            continue

        logger.info("Выбраны записи: %s", ", ".join(str(i + 1) for i in indexes))
        return indexes


def select_logo(template_path, uses_logo_placeholder):
    """
    Шаг 4. Выбор логотипа (или «Без логотипа»).
    Если в TEMPLATE_LOGOS для шаблона указан логотип — он предлагается по Enter.
    """
    logos = find_logos(config)
    if not logos:
        print("\nЛоготипы не найдены — документы будут без логотипа.")
        logger.info("Логотип: нет (файлы не найдены)")
        return None

    default = None
    preferred_name = config.TEMPLATE_LOGOS.get(template_path.name)
    if preferred_name:
        for number, path in enumerate(logos, start=1):
            if path.name.lower() == preferred_name.lower():
                default = number
        if default is None:
            print(f"\n  Внимание: логотип «{preferred_name}» из TEMPLATE_LOGOS не найден.")

    names = [f"{path.name}  ({short_path(path.parent)})" for path in logos]
    while True:
        index = select_from_list("Выберите логотип", names, zero_option="Без логотипа", default=default)
        if index is None:
            logger.info("Логотип: без логотипа")
            return None
        logo_path = logos[index]
        try:
            build_logo_html(logo_path)  # проверяем файл сразу, а не в конце генерации
            break
        except AppError as error:
            print(f"  Проблема с логотипом: {error}")
            print("  Выберите другой логотип или «Без логотипа».")
            logger.warning("Логотип не подходит: %s", error)

    if logo_path.suffix.lower() == ".svg":
        print("  Подсказка: SVG поддерживается, но если логотип выглядит неправильно — используйте PNG.")
    if not uses_logo_placeholder:
        print("  В шаблоне нет {{logo}} — логотип будет вставлен в начало документа.")
    logger.info("Выбран логотип: %s", logo_path)
    return logo_path


def warn_missing_placeholders(missing):
    """Предупреждает о плейсхолдерах, для которых нет колонок в данных."""
    if not missing:
        return
    print("\nВнимание! В файле данных нет колонок для этих плейсхолдеров шаблона:")
    for name in missing:
        print(f"  {{{{{name}}}}}")
    print("В документах эти места останутся пустыми.")
    logger.warning("Нет колонок для плейсхолдеров: %s", ", ".join(missing))
    if not ask_yes_no("Продолжить всё равно?"):
        raise UserCancelled()


def show_preview(records, labels, indexes):
    """Шаг 5. Предпросмотр выбранных записей."""
    print()
    print(LINE)
    print("Вы выбрали:")
    if len(indexes) <= config.PREVIEW_MAX_RECORDS:
        for index in indexes:
            print(f"\n  Запись [{index + 1}]")
            for column, value in records[index].items():
                value = " ".join(value.split())
                if len(value) > 70:
                    value = value[:67] + "..."
                print(f"    {column}: {value}")
    else:
        print()
        for index in indexes[:10]:
            print(f"  [{index + 1}] {labels[index]}")
        if len(indexes) > 10:
            print(f"  ... и ещё {len(indexes) - 10}")


# ---------------------------------------------------------------------------
# Генерация
# ---------------------------------------------------------------------------

def make_base_name(record, columns, record_index, total_records, used_names):
    """Имя файла без расширения: check_1001 или check_005 (если нет колонки с номером)."""
    number_column = find_column(columns, config.FILENAME_COLUMNS)
    number = record.get(number_column, "").strip() if number_column else ""
    if not number:
        width = max(3, len(str(total_records)))
        number = str(record_index + 1).zfill(width)
    base_name = sanitize_filename(f"{config.OUTPUT_FILENAME_PREFIX}_{number}")
    return make_unique_name(base_name, used_names)


def generate_documents(template_path, template_text, columns, records, indexes,
                       logo_path, weasyprint):
    """
    Шаг 6. Создание HTML и PDF для каждой выбранной записи.
    Ошибка в одной записи не останавливает обработку остальных.
    """
    output_dir = resolve_path(config.OUTPUT_DIR)
    html_dir = output_dir / "html"
    pdf_dir = output_dir / "pdf"

    # Общие части готовим один раз для всех документов
    font_files = find_font_files(config)
    if not font_files and weasyprint:
        print("  Внимание: шрифты Roboto / Liberation Sans не найдены в папке fonts.\n"
              "  Будет использован системный шрифт — кириллица может выглядеть иначе.\n"
              "  Как добавить шрифт — см. README.md, раздел «Шрифты».")
        logger.warning("Файлы шрифтов не найдены")
    base_css = build_base_css(
        build_font_face_css(font_files), config.LOGO_MAX_WIDTH, config.LOGO_MAX_HEIGHT
    )
    head_html = build_head_html(
        base_css,
        base_url=template_path.parent.as_uri() + "/",
        add_charset="charset" not in template_text[:2000].lower(),
    )
    logo_html = build_logo_html(logo_path) if logo_path else ""
    template_has_logo = any(name.lower() == "logo" for name in extract_placeholders(template_text))
    today = datetime.date.today().strftime("%d.%m.%Y")

    print("\nСоздание документов...\n")
    used_names = set()
    created_html = []
    created_pdf = []
    errors = 0

    for record_index in indexes:
        record = records[record_index]
        base_name = make_base_name(record, columns, record_index, len(records), used_names)

        special_values = {
            "logo": logo_html,
            "all_fields_table": build_all_fields_table(record),
            "today": today,
            "record_number": str(record_index + 1),
        }
        html_text = render_template(
            template_text, record, special_values, config.MISSING_PLACEHOLDER_VALUE
        )
        if logo_html and not template_has_logo:
            html_text = insert_after_body_start(
                html_text, f'<div class="logo-container">{logo_html}</div>'
            )
        html_text = insert_into_head(html_text, head_html)

        html_path = html_dir / f"{base_name}.html"
        try:
            create_html(html_text, html_path)
            created_html.append(html_path)
            print(f"[OK] {html_path.name}")
        except AppError as error:
            print(f"[ОШИБКА] {html_path.name}: {error}")
            logger.error("Ошибка HTML %s: %s", html_path.name, error)
            errors += 1
            continue

        if weasyprint:
            pdf_path = pdf_dir / f"{base_name}.pdf"
            try:
                create_pdf(weasyprint, html_text, pdf_path, template_path.parent)
                created_pdf.append(pdf_path)
                print(f"[OK] {pdf_path.name}")
            except AppError as error:
                print(f"[ОШИБКА] {error}")
                logger.error("Ошибка PDF %s: %s", pdf_path.name, error)
                errors += 1
        print()

    return created_html, created_pdf, errors


def open_results(created_pdf, created_html):
    """
    Открывает результат системной программой.

    Почему так:
      - один PDF открывается сразу — это самый частый случай;
      - при нескольких PDF открывать десятки окон неудобно, поэтому по умолчанию
        (режим "ask") программа спрашивает, а по Enter открывает ПАПКУ с PDF —
        это одно окно, в котором видны все созданные файлы.
    """
    if not config.OPEN_AFTER_GENERATION:
        return

    files = created_pdf or created_html  # если PDF не создавались — работаем с HTML
    if not files:
        return
    if len(files) == 1:
        open_file(files[0])
        return

    folder = files[0].parent
    mode = config.OPEN_MULTIPLE_MODE
    if mode == "first":
        open_file(files[0])
        return
    if mode == "folder":
        open_file(folder)
        return

    options = [f"Открыть папку с файлами ({short_path(folder)})", "Открыть первый файл"]
    if len(files) <= 10:
        options.append(f"Открыть все файлы ({len(files)} шт.)")
    choice = select_from_list(
        "Что открыть?", options, zero_option="Ничего не открывать", default=1
    )
    if choice == 0:
        open_file(folder)
    elif choice == 1:
        open_file(files[0])
    elif choice == 2:
        for path in files:
            open_file(path)


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------

def main():
    print(LINE)
    print("Генератор чеков (HTML + PDF)")
    print("Для выхода в любой момент введите q")
    print(LINE)

    # Проверяем WeasyPrint заранее, чтобы не узнать о проблеме после всех вопросов
    weasyprint = None
    if config.CREATE_PDF:
        weasyprint, problem = load_weasyprint(config.WEASYPRINT_DLL_DIR)
        if problem:
            print(f"\n{problem}\n")
            if not ask_yes_no("Продолжить и создать только HTML-файлы?", default=False):
                raise UserCancelled()

    template_path = select_template()
    template_text = load_template(template_path)
    placeholders = extract_placeholders(template_text)
    logger.debug("Плейсхолдеры шаблона: %s", placeholders)

    data_path = select_data_file()
    columns, records, format_info = load_records(data_path)
    print(f"\nФайл прочитан ({format_info}).")
    print(f"Найдено записей: {len(records)}")
    logger.info("Найдено записей: %s, колонки: %s", len(records), columns)

    warn_missing_placeholders(find_missing_placeholders(placeholders, columns))

    labels = [get_record_label(record, columns, config) for record in records]
    indexes = select_records(records, labels)

    uses_logo_placeholder = any(name.lower() == "logo" for name in placeholders)
    logo_path = select_logo(template_path, uses_logo_placeholder)

    show_preview(records, labels, indexes)
    print()
    print(LINE)
    print(f"Шаблон: {get_template_title(template_path)} ({template_path.name})")
    print(f"Файл: {data_path.name}")
    print(f"Записей: {len(indexes)}")
    print(f"Логотип: {logo_path.name if logo_path else 'без логотипа'}")
    print(f"Формат: {'HTML + PDF' if weasyprint else 'только HTML'}")
    print(LINE)
    if not ask_yes_no("Продолжить?"):
        raise UserCancelled()

    created_html, created_pdf, errors = generate_documents(
        template_path, template_text, columns, records, indexes, logo_path, weasyprint
    )

    print(LINE)
    print("Готово!" if not errors else f"Готово, но с ошибками: {errors}")
    print(f"\nСоздано HTML: {len(created_html)}")
    if weasyprint:
        print(f"Создано PDF: {len(created_pdf)}")
        for path in created_pdf:
            print(f"  {path.name}")
    output_dir = resolve_path(config.OUTPUT_DIR)
    print(f"\nПапка с результатами: {output_dir}")
    print(LINE)

    open_results(created_pdf, created_html)
    return 1 if errors else 0


def run():
    """Точка входа: настраивает консоль/лог и перехватывает ошибки."""
    debug = config.DEBUG or "--debug" in sys.argv
    prepare_console()
    log_file = setup_logging(resolve_path(config.OUTPUT_DIR) / "logs", debug)
    logger.info("===== Запуск программы =====")

    try:
        exit_code = main()
    except (UserCancelled, KeyboardInterrupt):
        print("\n\nРабота прервана. Файлы не созданы или созданы частично.")
        logger.info("Работа прервана пользователем")
        exit_code = 2
    except AppError as error:
        print(f"\nОШИБКА: {error}")
        logger.error("Ошибка: %s", error)
        if debug:
            traceback.print_exc()
        exit_code = 1
    except Exception as error:  # непредвиденная ошибка — подробности в логе
        logger.exception("Непредвиденная ошибка")
        print(f"\nНепредвиденная ошибка: {error}")
        if debug:
            traceback.print_exc()
        else:
            print(f"Подробности записаны в {log_file}")
            print("Для подробного вывода запустите: python main.py --debug")
        exit_code = 1

    logger.info("===== Завершение (код %s) =====", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
