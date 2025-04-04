import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker  # noqa: F401
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, BigInteger, select, delete, DateTime, ForeignKey  # noqa: F401
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
from datetime import datetime, timedelta

# Logging
logging.basicConfig(level=logging.INFO)

# === DATABASE SETUP === #
engine = create_async_engine("sqlite+aiosqlite:///bot.db")
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(String)
    delay_hours: Mapped[float] = mapped_column(Float)
    link: Mapped[str] = mapped_column(String)


class SentMessage(Base):
    __tablename__ = "sent_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# === FSM === #
class AdminStates(StatesGroup):
    add_message_title = State()
    add_message_text = State()
    add_message_delay = State()
    add_message_link = State()


# === BOT SETUP === #
bot = Bot(token="7149425421:AAGESxE2Y-7gX5u0vUozwrdvi7Tcwn4FDZ0")
dp = Dispatcher()
scheduler = AsyncIOScheduler()


# === UTILS === #
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def send_scheduled_messages():
    async with SessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        messages = (await session.execute(select(Message))).scalars().all()
        now = datetime.utcnow()

        for user in users:
            for msg in messages:
                send_time = user.registered_at + timedelta(hours=msg.delay_hours)

                already_sent = await session.execute(
                    select(SentMessage).where(
                        SentMessage.user_id == user.id, SentMessage.message_id == msg.id
                    )
                )
                if already_sent.first():
                    continue

                if now >= send_time:
                    try:
                        await bot.send_message(
                            user.telegram_id, text=f"{msg.text}\n{msg.link}"
                        )
                        # await bot.send_message(user.telegram_id, msg.link)  # Отправляем ссылку отдельным сообщением

                        session.add(SentMessage(user_id=user.id, message_id=msg.id))
                        await session.commit()
                    except Exception as e:
                        logging.warning(f"Ошибка при отправке {user.telegram_id}: {e}")


# === START === #
@dp.message(F.text == "/start")
async def start_handler(msg: types.Message):
    async with SessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(User.telegram_id == msg.from_user.id)
            )
        ).scalar_one_or_none()
        if not user:
            session.add(
                User(
                    name=msg.from_user.full_name,
                    telegram_id=msg.from_user.id,
                    role="user",
                    registered_at=datetime.utcnow(),
                )
            )
            await session.commit()

            welcome_text = (
                "Привет!\n\n"
                "Совсем скоро с вами свяжется наш менеджер Алина. Предлагаем познакомиться подробнее с нашей франшизой.\n\n"
                "Основатели федеральной сети студий заботы о теле «Рельсы-рельсы, шпалы-шпалы» Константин и Лилия Бородины в интервью для Сергея Терентьева.\n\n"
                "- Как пришла идея открыть свою студию\n"
                "- Первая студия открытая по франшизе в Мурманске\n"
                "- Особенности сервиса и уюта студий «Рельсы-рельсы, шпалы-шпалы»\n\n"
                "Смотреть интервью: \nhttps://rutube.ru/video/70a68bad9e59559b581d77722ed6f036/"
            )
            await msg.answer(welcome_text)
            # await msg.answer("https://disk.yandex.ru/i/AsoaQ8nfTuNOhg")
        else:
            await msg.answer("Вы уже зарегистрированы.")


# === АДМИН: ДОБАВЛЕНИЕ СООБЩЕНИЙ === #
@dp.message(F.text == "Добавить сообщение")
async def add_message(msg: types.Message, state: FSMContext):
    await msg.answer("Введите название сообщения:")
    await state.set_state(AdminStates.add_message_title)


@dp.message(AdminStates.add_message_title)
async def message_title_step(msg: types.Message, state: FSMContext):
    await state.update_data(title=msg.text)
    await msg.answer("Введите текст сообщения:")
    await state.set_state(AdminStates.add_message_text)


@dp.message(AdminStates.add_message_text)
async def message_text_step(msg: types.Message, state: FSMContext):
    await state.update_data(text=msg.text)
    await msg.answer(
        "Через сколько часов (можно с дробной частью) после старта отправить это сообщение?"
    )
    await state.set_state(AdminStates.add_message_delay)


@dp.message(AdminStates.add_message_delay)
async def message_delay_step(msg: types.Message, state: FSMContext):
    try:
        delay = float(msg.text)
        if delay < 0.016:  # Минимальное значение (примерно 1 минута)
            await msg.answer(
                "Минимальное значение задержки — 0.016 (примерно 1 минута)"
            )
            return
        await state.update_data(delay_hours=delay)
        await msg.answer("Введите ссылку для инлайн-кнопки:")
        await state.set_state(AdminStates.add_message_link)
    except ValueError:
        await msg.answer("Введите корректное число (можно с дробной частью)")


@dp.message(AdminStates.add_message_link)
async def message_link_step(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    async with SessionLocal() as session:
        session.add(
            Message(
                title=data["title"],
                text=data["text"],
                delay_hours=data["delay_hours"],
                link=msg.text,
            )
        )
        await session.commit()
    await msg.answer("Сообщение успешно добавлено! ✅")
    await state.clear()


# === СПИСОК СООБЩЕНИЙ === #
@dp.message(F.text == "Список сообщений")
async def list_messages(msg: types.Message):
    async with SessionLocal() as session:
        messages = (await session.execute(select(Message))).scalars().all()
        if messages:
            for message in messages:
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Удалить",
                                callback_data=f"delete_message_{message.id}",
                            )
                        ]
                    ]
                )
                await msg.answer(
                    f"📩 {message.title}\n📝 {message.text}\n🔗 {message.link}",
                    reply_markup=keyboard,
                )
        else:
            await msg.answer("Список сообщений пуст.")


@dp.callback_query(F.data.startswith("delete_message_"))
async def delete_message(callback: types.CallbackQuery):
    message_id = int(callback.data.split("_")[2])
    async with SessionLocal() as session:
        await session.execute(delete(Message).where(Message.id == message_id))
        await session.commit()
    await callback.message.edit_text("Сообщение удалено.")


@dp.callback_query(F.data.startswith("delete_user_"))
async def delete_user(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    async with SessionLocal() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()
    await callback.message.edit_text("Пользователь удален.")


# === СПИСОК ПОЛЬЗОВАТЕЛЕЙ === #
@dp.message(F.text == "Список пользователей")
async def list_users(msg: types.Message):
    async with SessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        if users:
            for user in users:
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Удалить", callback_data=f"delete_user_{user.id}"
                            )
                        ]
                    ]
                )
                await msg.answer(
                    f"👤 {user.name} ({user.telegram_id})", reply_markup=keyboard
                )
        else:
            await msg.answer("Список пользователей пуст.")


# === ADMIN PANEL === #
@dp.message(F.text == "/admin")
async def admin_panel(msg: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Добавить сообщение")],
            [KeyboardButton(text="Список сообщений")],
            [KeyboardButton(text="Список пользователей")],
        ],
        resize_keyboard=True,
    )
    await msg.answer("Админ-панель:", reply_markup=kb)


# === SCHEDULER === #
scheduler.add_job(send_scheduled_messages, trigger="interval", seconds=10)


# === MAIN === #
async def main():
    await init_db()
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
