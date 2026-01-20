"""
Инициализация бота и диспетчера.
"""
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import config

# Создаем хранилище FSM
storage = MemoryStorage()

# Создаем бота и диспетчер
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
