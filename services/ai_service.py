"""
Сервис для работы с OpenAI GPT-4o.
"""
import json
import base64
import time
from typing import Optional
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from prompts.system_prompts import SystemPrompts
from .memory_service import MemoryService
from database.models import ApiUsageLog

# Примерные цены OpenAI (в центах за 1K токенов)
PRICING = {
    "gpt-4o": {"prompt": 0.25, "completion": 1.0},  # $2.50/$10 per 1M
    "gpt-4o-mini": {"prompt": 0.015, "completion": 0.06},
    "whisper-1": {"per_minute": 0.6},  # $0.006 per minute
}

# Схема функций для календаря (OpenAI Function Calling)
CALENDAR_FUNCTIONS = [
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "Создать новое событие в календаре. ВАЖНО: если пользователь просит 'напомнить за X часов/минут ДО события' — это reminder_minutes, НЕ отдельная функция set_reminder!",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название события (созвон с Петей, встреча, тренировка)"
                    },
                    "date": {
                        "type": "string",
                        "description": "Дата: сегодня, завтра, послезавтра, понедельник, следующая среда, 25 января, 15.01"
                    },
                    "time": {
                        "type": "string",
                        "description": "Время в формате HH:MM (14:00, 09:30)"
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Длительность в минутах. По умолчанию: созвон/встреча=60, стендап=30, тренировка=90, обед=60"
                    },
                    "recurrence": {
                        "type": "string",
                        "enum": ["daily", "weekly", "monthly", "none"],
                        "description": "Повторение. ТОЛЬКО если явно сказано 'каждый день', 'еженедельно', 'каждый понедельник'. По умолчанию none."
                    },
                    "reminder_minutes": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "ОБЯЗАТЕЛЬНО при 'напомни за X до'. Минуты до события: 'за 3 часа'=180, 'за 1 час'=60, 'за день'=1440. Пример: 'напомни за 3 часа и за час' → [180, 60]"
                    },
                    "location": {
                        "type": "string",
                        "description": "Место события (адрес, офис, ссылка на Zoom). Только если явно указано."
                    }
                },
                "required": ["title", "time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": "Изменить существующее событие: перенести на другое время/дату, изменить длительность. Используй для 'перенеси', 'передвинь', 'измени время'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название события для поиска (созвон, встреча с Петей)"
                    },
                    "new_date": {
                        "type": "string",
                        "description": "Новая дата: завтра, понедельник, следующая среда, 25 января"
                    },
                    "new_time": {
                        "type": "string",
                        "description": "Новое время в формате HH:MM"
                    },
                    "new_duration": {
                        "type": "integer",
                        "description": "Новая длительность в минутах"
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "Удалить событие из календаря. ТОЛЬКО при явных словах: удали, отмени, убери.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название события для удаления"
                    },
                    "delete_all": {
                        "type": "boolean",
                        "description": "True если нужно удалить ВСЕ события с таким названием (со всех дат)"
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "Показать события из календаря. Для вопросов: что на сегодня, план на завтра, что по плану, расписание.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": ["today", "tomorrow", "week", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
                        "description": "Период: today (сегодня), tomorrow (завтра), week (неделя), или конкретный день недели"
                    }
                },
                "required": ["period"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_events",
            "description": "Найти события по запросу. Для: найди встречу, когда был созвон, покажи все митинги.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос (название события или его часть)"
                    },
                    "period": {
                        "type": "string",
                        "enum": ["week", "month", "past_week", "past_month"],
                        "description": "Где искать: week/month (будущее), past_week/past_month (прошлое)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_free_slots",
            "description": "Найти свободное время в календаре. Для: когда свободен, найди окно, есть ли время.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Дата: сегодня, завтра, понедельник"
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Нужная длительность в минутах (по умолчанию 60)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Создать напоминание. Поддерживает 2 режима: 1) ОТНОСИТЕЛЬНЫЙ: 'напомни через час' — используй minutes; 2) АБСОЛЮТНЫЙ: 'напомни завтра в 10:00' — используй date и time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "description": "Через сколько минут от СЕЙЧАС напомнить (через час=60, через 30 минут=30). Используй ЭТО поле для 'через X минут/часов'"
                    },
                    "date": {
                        "type": "string",
                        "description": "Дата напоминания: 'сегодня', 'завтра', '20 января', '2025-01-20'. Используй для абсолютного времени"
                    },
                    "time": {
                        "type": "string",
                        "description": "Время напоминания: '10:00', '14:30'. Используй вместе с date для абсолютного времени"
                    },
                    "message": {
                        "type": "string",
                        "description": "О чём напомнить (из контекста разговора)"
                    }
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rename_event",
            "description": "Переименовать событие. Для: переименуй, назови, измени название.",
            "parameters": {
                "type": "object",
                "properties": {
                    "old_title": {
                        "type": "string",
                        "description": "Текущее название события"
                    },
                    "new_title": {
                        "type": "string",
                        "description": "Новое название"
                    }
                },
                "required": ["old_title", "new_title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chat_response",
            "description": "Обычный разговор без действий с календарём. Для приветствий, вопросов, обсуждений, когда НЕ нужны операции с календарём.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Причина почему это не действие с календарём"
                    }
                }
            }
        }
    }
]


class AIService:
    """Работа с OpenAI API"""

    def __init__(self, session: AsyncSession):
        self.client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self.memory = MemoryService(session)
        self.session = session

    async def _log_usage(
        self,
        user_id: int,
        api_type: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        response_time_ms: int = 0,
        audio_duration_sec: float = 0,
    ):
        """Записать использование API в базу"""
        total_tokens = prompt_tokens + completion_tokens

        # Расчёт стоимости
        cost = 0.0
        if model in PRICING:
            pricing = PRICING[model]
            if "per_minute" in pricing:
                # Whisper — цена за минуту
                cost = (audio_duration_sec / 60) * pricing["per_minute"]
            else:
                # GPT — цена за токены
                cost = (prompt_tokens / 1000) * pricing["prompt"]
                cost += (completion_tokens / 1000) * pricing["completion"]

        log = ApiUsageLog(
            user_id=user_id,
            api_type=api_type,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=cost,
            response_time_ms=response_time_ms,
        )
        self.session.add(log)
        await self.session.commit()

    async def chat(
        self,
        user_id: int,
        message: str,
        user_name: str = "Пользователь",
        save_to_history: bool = True,
    ) -> str:
        """Основной метод чата с AI"""

        # Получаем контекст памяти
        memory_context = await self.memory.build_context_string(user_id)

        # Получаем историю сообщений
        history = await self.memory.get_conversation_history(user_id)

        # Формируем системный промпт
        system_prompt = SystemPrompts.get_main_prompt(
            user_name=user_name,
            memory_context=memory_context,
        )

        # Собираем сообщения
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})

        # Запрос к GPT с замером времени и таймаутом
        start_time = time.time()
        response = await self.client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1000,
            timeout=30.0,  # 30 секунд таймаут
        )
        response_time_ms = int((time.time() - start_time) * 1000)

        # Проверяем что ответ не пустой
        if not response.choices:
            return "Извини, не получилось обработать запрос. Попробуй ещё раз."

        assistant_message = response.choices[0].message.content or ""

        # Логируем использование API
        usage = response.usage
        await self._log_usage(
            user_id=user_id,
            api_type="chat",
            model=config.OPENAI_MODEL,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            response_time_ms=response_time_ms,
        )

        # Сохраняем в историю
        if save_to_history:
            await self.memory.save_message(user_id, "user", message)
            await self.memory.save_message(user_id, "assistant", assistant_message)

        return assistant_message

    async def analyze_voice(self, user_id: int, transcription: str, user_name: str = "Пользователь") -> str:
        """Анализ голосового сообщения"""
        prompt = SystemPrompts.get_voice_analysis_prompt()

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": transcription},
        ]

        start_time = time.time()
        response = await self.client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1000,
            timeout=30.0,
        )
        response_time_ms = int((time.time() - start_time) * 1000)

        if not response.choices:
            return "Не удалось обработать голосовое сообщение."

        result = response.choices[0].message.content or ""

        # Логируем использование API
        usage = response.usage
        await self._log_usage(
            user_id=user_id,
            api_type="voice_analysis",
            model=config.OPENAI_MODEL,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            response_time_ms=response_time_ms,
        )

        # Сохраняем в историю
        await self.memory.save_message(user_id, "user", f"[Голосовое] {transcription}", "voice")
        await self.memory.save_message(user_id, "assistant", result)

        return result

    async def analyze_image(self, user_id: int, image_base64: str, user_prompt: str = None) -> str:
        """Анализ изображения (скриншота)"""
        prompt = user_prompt or "Что ты видишь на этом изображении? Если это список задач, расписание или финансы — структурируй информацию."

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ]

        start_time = time.time()
        response = await self.client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            max_tokens=1500,
            timeout=60.0,  # Изображения могут обрабатываться дольше
        )
        response_time_ms = int((time.time() - start_time) * 1000)

        if not response.choices:
            return "Не удалось обработать изображение."

        result = response.choices[0].message.content or ""

        # Логируем использование API
        usage = response.usage
        await self._log_usage(
            user_id=user_id,
            api_type="image",
            model=config.OPENAI_MODEL,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            response_time_ms=response_time_ms,
        )

        # Сохраняем в историю
        await self.memory.save_message(user_id, "user", "[Изображение]", "image")
        await self.memory.save_message(user_id, "assistant", result)

        return result

    async def transcribe_audio(self, audio_file_path: str, user_id: int = None) -> str:
        """Транскрибация аудио через Whisper"""
        import os

        # Получаем длительность аудио (примерно по размеру файла)
        file_size = os.path.getsize(audio_file_path)
        # Примерно 16KB на секунду для OGG файлов
        audio_duration_sec = file_size / 16000

        start_time = time.time()
        with open(audio_file_path, "rb") as audio_file:
            transcription = await self.client.audio.transcriptions.create(
                model=config.WHISPER_MODEL,
                file=audio_file,
            )
        response_time_ms = int((time.time() - start_time) * 1000)

        # Логируем использование Whisper
        if user_id:
            await self._log_usage(
                user_id=user_id,
                api_type="whisper",
                model=config.WHISPER_MODEL,
                response_time_ms=response_time_ms,
                audio_duration_sec=audio_duration_sec,
            )

        return transcription.text

    async def extract_tasks_from_text(self, text: str) -> list[dict]:
        """Извлечь задачи из текста"""
        prompt = SystemPrompts.get_task_extraction_prompt()

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ]

        response = await self.client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=1000,
            timeout=30.0,
        )

        if not response.choices:
            return "[]"

        return response.choices[0].message.content or "[]"

    async def generate_daily_plan(self, user_id: int, tasks: list, user_name: str) -> str:
        """Сгенерировать план дня"""
        memory_context = await self.memory.build_context_string(user_id)

        prompt = SystemPrompts.get_daily_plan_prompt(user_name, memory_context)

        tasks_text = "\n".join([f"- {t.title} (приоритет: {t.priority})" for t in tasks]) if tasks else "Задач пока нет"

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Мои текущие задачи:\n{tasks_text}"},
        ]

        response = await self.client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=800,
            timeout=30.0,
        )

        if not response.choices:
            return "Не удалось сгенерировать план."

        return response.choices[0].message.content or ""

    async def generate_reflection_questions(self) -> str:
        """Вопросы для вечерней рефлексии"""
        prompt = SystemPrompts.get_reflection_prompt()

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Задай мне вопросы для вечерней рефлексии"},
        ]

        response = await self.client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            temperature=0.8,
            max_tokens=500,
            timeout=30.0,
        )

        if not response.choices:
            return "Не удалось сгенерировать вопросы."

        return response.choices[0].message.content or ""

    async def update_user_memory(self, user_id: int, key: str, content: str, importance: int = 5):
        """Обновить память о пользователе"""
        await self.memory.save_memory(
            user_id=user_id,
            key=key,
            value={"content": content, "importance": importance},
        )

    async def detect_intent(self, message: str, user_id: int = None, calendar_events: list = None) -> dict:
        """Определить намерение пользователя через OpenAI Function Calling"""
        from datetime import datetime
        import pytz

        tz = pytz.timezone("Europe/Moscow")
        now = datetime.now(tz)
        weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

        # Системный промпт для function calling
        system_prompt = f"""Ты помощник для работы с календарём. Сегодня {now.strftime('%d.%m.%Y')} ({weekdays[now.weekday()]}), время {now.strftime('%H:%M')}.

Твоя задача — определить что хочет пользователь и вызвать нужную функцию.

ВАЖНЫЕ ПРАВИЛА:
1. Отвечай ТОЛЬКО на русском языке
2. "следующий/следующая/следующую + день недели" = день недели СЛЕДУЮЩЕЙ недели (не ближайший!)
3. Просто "в среду" = ближайшая среда
4. "перенеси", "передвинь" = update_event (изменение существующего события)
5. Если нет явного времени/даты и это не команда календаря — используй chat_response
6. "созвон", "встреча", "митинг" = duration 60 минут по умолчанию
7. "стендап", "дейли" = duration 30 минут
8. recurrence ТОЛЬКО если буквально сказано "каждый", "ежедневно", "еженедельно"
9. НЕ ПУТАЙ создание нового события (create_event) и изменение существующего (update_event)!
   - "поставь созвон на 15:00" = create_event
   - "перенеси созвон на 15:00" = update_event
   - "[название] на [дату]" без слова "поставь/добавь" = update_event (перенос)
10. НАПОМИНАНИЯ О СОБЫТИЯХ:
   - "напомни за 3 часа и за час" при создании события = reminder_minutes: [180, 60] в create_event
   - "напомни за день до" = reminder_minutes: [1440]
   - НЕ вызывай set_reminder для напоминаний о событиях! set_reminder ТОЛЬКО для "напомни через час" без события"""

        # Добавляем контекст
        context_parts = []

        if calendar_events:
            events_list = [f"- {e.get('summary', 'Без названия')}" for e in calendar_events[:10]]
            if events_list:
                context_parts.append(f"События в календаре:\n" + "\n".join(events_list))

        if user_id:
            history = await self.memory.get_conversation_history(user_id, limit=6)
            if history:
                recent_msgs = [f"{'Пользователь' if m['role'] == 'user' else 'Бот'}: {m['content'][:150]}" for m in history[-6:]]
                context_parts.append(f"Контекст разговора:\n" + "\n".join(recent_msgs))

        messages = [{"role": "system", "content": system_prompt}]

        if context_parts:
            messages.append({"role": "user", "content": "КОНТЕКСТ:\n" + "\n\n".join(context_parts)})
            messages.append({"role": "assistant", "content": "Понял контекст."})

        messages.append({"role": "user", "content": message})

        start_time = time.time()
        response = await self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=CALENDAR_FUNCTIONS,
            tool_choice="required",  # Обязательно вызвать функцию
            temperature=0.1,
            timeout=15.0,  # Быстрый таймаут для intent detection
        )
        response_time_ms = int((time.time() - start_time) * 1000)

        # Логируем использование
        if user_id:
            usage = response.usage
            await self._log_usage(
                user_id=user_id,
                api_type="intent_fc",
                model="gpt-4o-mini",
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                response_time_ms=response_time_ms,
            )

        # Проверяем что ответ не пустой
        if not response.choices:
            return {"actions": [{"intent": "chat"}]}

        # Обрабатываем вызовы функций
        choice = response.choices[0]

        if choice.message.tool_calls:
            actions = []
            for tool_call in choice.message.tool_calls:
                func_name = tool_call.function.name
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                # Конвертируем function call в старый формат actions
                action = self._convert_function_to_action(func_name, func_args)
                if action:
                    actions.append(action)

            return {"actions": actions} if actions else {"actions": [{"intent": "chat"}]}

        return {"actions": [{"intent": "chat"}]}

    def _convert_function_to_action(self, func_name: str, args: dict) -> dict:
        """Конвертировать вызов функции в формат action для совместимости"""

        if func_name == "create_event":
            action = {
                "intent": "create_tasks",
                "title": args.get("title", ""),
                "date": args.get("date", "сегодня"),
                "time": args.get("time", ""),
                "duration_minutes": args.get("duration_minutes", 60),
            }
            if args.get("recurrence") and args["recurrence"] != "none":
                action["recurrence"] = args["recurrence"]
            if args.get("reminder_minutes"):
                action["reminder_minutes"] = args["reminder_minutes"]
            if args.get("location"):
                action["location"] = args["location"]
            return action

        elif func_name == "update_event":
            action = {
                "intent": "update_task",
                "original_title": args.get("title", ""),
            }
            if args.get("new_date"):
                action["new_date"] = args["new_date"]
            if args.get("new_time"):
                action["new_time"] = args["new_time"]
            if args.get("new_duration"):
                action["new_duration"] = args["new_duration"]
            return action

        elif func_name == "delete_event":
            return {
                "intent": "delete_task",
                "original_title": args.get("title", ""),
                "delete_all": args.get("delete_all", False),
            }

        elif func_name == "list_events":
            return {
                "intent": "list_tasks",
                "period": args.get("period", "today"),
            }

        elif func_name == "search_events":
            return {
                "intent": "search_events",
                "query": args.get("query", ""),
                "period": args.get("period", "week"),
            }

        elif func_name == "find_free_slots":
            return {
                "intent": "find_free_slots",
                "date": args.get("date", "сегодня"),
                "duration_minutes": args.get("duration_minutes", 60),
            }

        elif func_name == "set_reminder":
            result = {
                "intent": "set_reminder",
                "message": args.get("message", "напоминание"),
            }
            # Поддерживаем оба режима: относительный (minutes) и абсолютный (date + time)
            if args.get("minutes"):
                result["minutes"] = args["minutes"]
            if args.get("date"):
                result["date"] = args["date"]
            if args.get("time"):
                result["time"] = args["time"]
            return result

        elif func_name == "rename_event":
            return {
                "intent": "rename_task",
                "original_title": args.get("old_title", ""),
                "new_title": args.get("new_title", ""),
            }

        elif func_name == "chat_response":
            return {"intent": "chat"}

        return {"intent": "chat"}

    async def generate_task_response(self, title: str, datetime_str: str) -> str:
        """Сгенерировать ответ о созданной задаче"""
        if datetime_str:
            return f"✅ Записал: **{title}**\n📅 {datetime_str}"
        else:
            return f"✅ Записал: **{title}**\n\n⏰ Когда запланировать? Напиши дату и время."

    async def generate_tasks_list_response(self, events_text: str, period: str) -> str:
        """Сгенерировать ответ со списком задач"""
        if not events_text or "Нет событий" in events_text:
            period_text = {
                "today": "сегодня",
                "tomorrow": "завтра",
                "week": "на этой неделе"
            }.get(period, "")
            return f"📭 У тебя {period_text} пока нет запланированных дел.\n\nХочешь что-то добавить?"
        return events_text

    async def extract_context(self, user_id: int, message: str, assistant_response: str) -> dict | None:
        """
        Извлечь контекст из сообщения, если он есть.
        GPT сам решает, есть ли в сообщении что-то важное для запоминания.
        Возвращает None если ничего запоминать не нужно.
        """
        prompt = """Ты анализируешь диалог пользователя с ассистентом.
Твоя задача — определить, содержит ли сообщение пользователя важную информацию о нём, которую стоит запомнить.

ЗАПОМИНАТЬ НУЖНО:
- Цели и планы ("хочу похудеть", "учу английский", "готовлюсь к марафону")
- Предпочтения ("я жаворонок", "не люблю звонки до 10", "предпочитаю переписку")
- Личные факты ("работаю в IT", "есть дети", "живу в Москве")
- Привычки и расписание ("тренируюсь по вторникам", "обед в 13:00")
- Проекты и дедлайны ("запускаю стартап", "защита диплома в июне")

НЕ ЗАПОМИНАТЬ:
- Бытовые вопросы ("сколько времени", "какая погода")
- Команды боту ("поставь напоминание", "покажи задачи")
- Общие фразы ("привет", "спасибо", "ок")

Ответь СТРОГО в JSON формате:
{"should_save": false} — если ничего запоминать не нужно
{"should_save": true, "category": "goals|preferences|facts|projects", "content": "краткое описание факта", "importance": 1-10}

Примеры:
"Поставь встречу на завтра" → {"should_save": false}
"Я хочу к лету пробежать полумарафон" → {"should_save": true, "category": "goals", "content": "Цель: пробежать полумарафон к лету", "importance": 8}
"Я работаю продактом в Яндексе" → {"should_save": true, "category": "facts", "content": "Работает продакт-менеджером в Яндексе", "importance": 7}
"Мне не нравятся ранние встречи" → {"should_save": true, "category": "preferences", "content": "Не любит ранние встречи", "importance": 6}
"""

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Сообщение пользователя: {message}\nОтвет бота: {assistant_response[:200]}"},
        ]

        try:
            start_time = time.time()
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",  # Используем мини-модель для экономии
                messages=messages,
                temperature=0.1,
                max_tokens=150,
                timeout=10.0,
            )
            response_time_ms = int((time.time() - start_time) * 1000)

            # Логируем использование API
            usage = response.usage
            await self._log_usage(
                user_id=user_id,
                api_type="context_extraction",
                model="gpt-4o-mini",
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                response_time_ms=response_time_ms,
            )

            if not response.choices:
                return None

            result_text = (response.choices[0].message.content or "").strip()

            # Убираем markdown обёртки
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]

            result = json.loads(result_text)

            if result.get("should_save"):
                # Сохраняем в память
                category = result.get("category", "facts")
                content = result.get("content", "")
                importance = result.get("importance", 5)

                # Получаем текущий контекст категории
                existing = await self.memory.get_memory(user_id, category)
                if existing and isinstance(existing, dict):
                    # Добавляем к существующему
                    existing_content = existing.get("content", "")
                    # Проверяем, нет ли уже такого факта
                    if content.lower() not in existing_content.lower():
                        new_content = f"{existing_content}; {content}" if existing_content else content
                        await self.memory.save_memory(user_id, category, {
                            "content": new_content,
                            "importance": max(importance, existing.get("importance", 5))
                        })
                else:
                    await self.memory.save_memory(user_id, category, {
                        "content": content,
                        "importance": importance
                    })

                return result

            return None

        except Exception as e:
            # Если ошибка — просто не сохраняем, не критично
            return None
