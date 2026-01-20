"""
Сервис памяти AI.
Хранит и извлекает контекст о пользователе.
Переписки шифруются при сохранении в БД.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User, MemoryContext, Conversation
from config import config
from services.encryption_service import encryption


class MemoryService:
    """Управление памятью AI о пользователе"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(self, telegram_id: int, username: str = None, first_name: str = None) -> tuple[User, bool]:
        """
        Получить или создать пользователя.
        Возвращает (user, is_new) — is_new=True если пользователь только что создан.
        """
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        is_new = False

        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
            )
            self.session.add(user)
            try:
                await self.session.commit()
                await self.session.refresh(user)
                is_new = True
            except Exception:
                await self.session.rollback()
                # Пользователь уже существует, получаем его
                result = await self.session.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
                user = result.scalar_one_or_none()
        else:
            # Обновляем имя если изменилось
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                await self.session.commit()

        return user, is_new

    async def save_message(self, user_id: int, role: str, content: str, message_type: str = "text"):
        """Сохранить сообщение в историю (с шифрованием)"""
        # Шифруем содержимое сообщения
        encrypted_content = encryption.encrypt(content)

        conversation = Conversation(
            user_id=user_id,
            role=role,
            content=encrypted_content,
            message_type=message_type,
        )
        self.session.add(conversation)
        await self.session.commit()

    async def get_conversation_history(self, user_id: int, limit: int = None) -> list[dict]:
        """Получить историю сообщений для контекста (с расшифровкой)"""
        if limit is None:
            limit = config.CONVERSATION_HISTORY_LIMIT

        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(desc(Conversation.created_at))
            .limit(limit)
        )
        conversations = result.scalars().all()

        # Возвращаем в хронологическом порядке, расшифровывая содержимое
        return [
            {"role": c.role, "content": encryption.decrypt(c.content)}
            for c in reversed(conversations)
        ]

    async def save_memory(self, user_id: int, key: str, value: dict):
        """Сохранить долгосрочную память"""
        # Проверяем, есть ли уже такой ключ
        result = await self.session.execute(
            select(MemoryContext)
            .where(MemoryContext.user_id == user_id, MemoryContext.key == key)
        )
        memory = result.scalar_one_or_none()

        if memory:
            memory.value = value
            memory.updated_at = datetime.utcnow()
        else:
            memory = MemoryContext(
                user_id=user_id,
                key=key,
                value=value,
            )
            self.session.add(memory)

        await self.session.commit()

    async def get_memory(self, user_id: int, key: str) -> Optional[dict]:
        """Получить конкретную память"""
        result = await self.session.execute(
            select(MemoryContext)
            .where(MemoryContext.user_id == user_id, MemoryContext.key == key)
        )
        memory = result.scalar_one_or_none()
        return memory.value if memory else None

    async def get_all_memories(self, user_id: int) -> dict:
        """Получить всю долгосрочную память пользователя"""
        result = await self.session.execute(
            select(MemoryContext).where(MemoryContext.user_id == user_id)
        )
        memories = result.scalars().all()

        return {m.key: m.value for m in memories}

    async def build_context_string(self, user_id: int) -> str:
        """Сформировать строку контекста для системного промпта"""
        memories = await self.get_all_memories(user_id)

        if not memories:
            return "Информация о пользователе пока не собрана."

        context_parts = []

        if "goals" in memories:
            goals = memories["goals"]
            if isinstance(goals, dict) and "content" in goals:
                context_parts.append(f"Цели: {goals['content']}")

        if "preferences" in memories:
            prefs = memories["preferences"]
            if isinstance(prefs, dict) and "content" in prefs:
                context_parts.append(f"Предпочтения: {prefs['content']}")

        if "insights" in memories:
            insights = memories["insights"]
            if isinstance(insights, dict) and "content" in insights:
                context_parts.append(f"Инсайты: {insights['content']}")

        if "facts" in memories:
            facts = memories["facts"]
            if isinstance(facts, dict) and "content" in facts:
                context_parts.append(f"Факты: {facts['content']}")

        return "\n".join(context_parts) if context_parts else "Информация о пользователе пока не собрана."
