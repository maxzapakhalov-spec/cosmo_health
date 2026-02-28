import asyncio
import aiohttp
import pdfplumber
import flet as ft
import threading
from concurrent.futures import ThreadPoolExecutor

# ===== НАСТРОЙКИ =====
DEEPSEEK_API_KEY = "sk-faa1c9e28c0d429785779dac9e1010ed"          # замените на свой ключ
API_URL = "https://api.deepseek.com/v1/chat/completions"    # эндпоинт DeepSeek
PDF_PATH = "протокола пдф.pdf"                              # путь к файлу с протоколами
# =====================

# Глобальная переменная для текста протоколов (загружается один раз при старте)
PROTOCOLS_TEXT = ""

# Исполнитель для асинхронных задач
executor = ThreadPoolExecutor(max_workers=1)

def extract_protocols_from_pdf(path: str) -> str:
    """Извлекает и возвращает весь текст из PDF-файла."""
    full_text = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text.append(page_text)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать PDF: {e}")
    return "\n".join(full_text)

async def analyze_with_deepseek(pulse, hrv, spo2, pressure, temp, description):
    """
    Отправляет запрос в DeepSeek, используя загруженные протоколы,
    и возвращает ответ модели.
    """
    system_prompt = (
        "Ты — медицинский ассистент для космических полётов. "
        "Используй следующие протоколы для диагностики и рекомендаций:\n\n"
        f"{PROTOCOLS_TEXT}\n\n"
        "Отвечай на русском языке строго в указанном формате, без лишних пояснений."
    )

    user_prompt = (
        f"Пульс: {pulse}\n"
        f"HRV: {hrv}\n"
        f"SpO2: {spo2}\n"
        f"Давление: {pressure}\n"
        f"Температура: {temp}\n"
        f"Общее самочувствие: {description}\n\n"
        "На основе протоколов дай рекомендации и три наиболее вероятных состояния (болезни) "
        "с указанием процентов вероятности (сумма 100%). Ответ оформи в виде:\n"
        "Рекомендации: ...\n"
        "Состояния:\n"
        "- Название1 — XX%\n"
        "- Название2 — YY%\n"
        "- Название3 — ZZ%."
    )

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",            # или другая доступная модель
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3                    # низкая температура для стабильности формата
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(API_URL, json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['choices'][0]['message']['content']
            else:
                error_text = await resp.text()
                return f"Ошибка API (код {resp.status}): {error_text}"

def parse_response(response_text):
    """
    Разбирает ответ модели на рекомендации и список состояний.
    Возвращает (рекомендации, список кортежей (название, процент)).
    """
    recommendations = ""
    states = []

    if "Рекомендации:" in response_text:
        parts = response_text.split("Рекомендации:", 1)
        rest = parts[1]
        if "Состояния:" in rest:
            rec_part, states_part = rest.split("Состояния:", 1)
            recommendations = rec_part.strip()
            # Парсим строки состояний
            for line in states_part.strip().split("\n"):
                line = line.strip()
                if line.startswith("-") and "—" in line:
                    # Убираем начальный дефис и делим по тире
                    content = line[1:].strip()
                    if "—" in content:
                        name, percent = content.split("—", 1)
                        states.append((name.strip(), percent.strip()))
        else:
            recommendations = rest.strip()
    else:
        # Если формат не соблюдён, возвращаем весь ответ как рекомендации
        recommendations = response_text

    return recommendations, states

async def main(page: ft.Page):
    global PROTOCOLS_TEXT

    # Настройка страницы
    page.title = "Cosmo Health"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#0B0E12"
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.scroll = ft.ScrollMode.AUTO

    # Загрузка протоколов из PDF
    try:
        PROTOCOLS_TEXT = extract_protocols_from_pdf(PDF_PATH)
    except Exception as e:
        page.add(ft.Text(f"Ошибка загрузки протоколов: {e}", color=ft.Colors.RED))
        return

    # Заголовок
    page.add(ft.Text("COSMO HEALTH", size=32, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE))

    # Поля ввода (можно добавить иконки для красоты)
    pulse_field = ft.TextField(label="Пульс", width=250, color=ft.Colors.WHITE)
    hrv_field = ft.TextField(label="HRV", width=250, color=ft.Colors.WHITE)
    spo2_field = ft.TextField(label="SpO₂", width=250, color=ft.Colors.WHITE)
    pressure_field = ft.TextField(label="Давление", width=250, color=ft.Colors.WHITE)
    temp_field = ft.TextField(label="Температура", width=250, color=ft.Colors.WHITE)
    description_field = ft.TextField(
        label="Общее самочувствие",
        multiline=True,
        min_lines=2,
        max_lines=5,
        width=400,
        color=ft.Colors.WHITE
    )

    # Индикатор загрузки
    progress_bar = ft.ProgressBar(width=400, visible=False)
    
    # Кнопка анализа
    analyze_btn = ft.ElevatedButton("Анализ")
    
    # Области для вывода результатов
    recommendations_text = ft.Text("", selectable=True, color=ft.Colors.WHITE, size=16)
    states_list = ft.Column(spacing=5)

    async def analyze_click(e):
        # Получаем значения полей
        pulse = pulse_field.value
        hrv = hrv_field.value
        spo2 = spo2_field.value
        pressure = pressure_field.value
        temp = temp_field.value
        description = description_field.value

        # Простейшая проверка заполненности
        if not all([pulse, hrv, spo2, pressure, temp, description]):
            recommendations_text.value = "Пожалуйста, заполните все поля!"
            page.update()
            return

        # Индикация загрузки
        analyze_btn.text = "Анализ..."
        analyze_btn.disabled = True
        progress_bar.visible = True
        recommendations_text.value = "Запрашиваю данные у DeepSeek..."
        states_list.controls.clear()
        page.update()

        # Вызов API
        response = await analyze_with_deepseek(pulse, hrv, spo2, pressure, temp, description)

        # Парсинг ответа
        recs, states = parse_response(response)
        recommendations_text.value = recs if recs else "Не удалось получить рекомендации."

        states_list.controls.clear()
        if states:
            for name, percent in states:
                states_list.controls.append(
                    ft.Text(f"• {name} — {percent}", color=ft.Colors.WHITE, size=16)
                )
        else:
            states_list.controls.append(
                ft.Text("Не удалось определить состояния", color=ft.Colors.WHITE70, size=16)
            )

        # Возвращаем кнопку в исходное состояние
        analyze_btn.text = "Анализ"
        analyze_btn.disabled = False
        progress_bar.visible = False
        page.update()

    # Назначаем обработчик после создания всех элементов
    analyze_btn.on_click = lambda e: asyncio.create_task(analyze_click(e))

    # Размещаем всё на странице
    page.add(
        ft.Row(
            [
                # Левая колонка — ввод
                ft.Container(
                    content=ft.Column([
                        pulse_field,
                        hrv_field,
                        spo2_field,
                        pressure_field,
                        temp_field,
                        description_field,
                        progress_bar,
                        analyze_btn,
                    ], spacing=10),
                    width=450,
                    padding=20,
                ),

                # Разделитель
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE24),

                # Правая колонка — результаты
                ft.Container(
                    content=ft.Column([
                        ft.Text("Рекомендации", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        recommendations_text,
                        ft.Divider(height=20, color=ft.Colors.WHITE24),
                        ft.Text("Состояния", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        states_list,
                    ], spacing=10, scroll=ft.ScrollMode.AUTO),
                    expand=True,
                    padding=20,
                ),
            ],
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
    )

# Запуск приложения (исправлено для новых версий Flet)
if __name__ == "__main__":
    ft.run(main)
