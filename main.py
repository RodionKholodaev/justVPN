# main.py
import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from yookassa import Configuration, Payment
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ----------------- CONFIG -----------------
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
YOOKASSA_SHOP_ID = "YOUR_SHOP_ID"
YOOKASSA_SECRET_KEY = "YOUR_SECRET_KEY"

# Server where 3x_ui runs (used in generated ss:// links)
SERVER_IP = "91.184.248.35"  # твой сервер IP
SHADOWSOCKS_PORT = 8388     # порт shadowsocks на сервере (изменить если нужно)
SHADOWSOCKS_METHOD = "chacha20-ietf-poly1305"  # корректно для большинства клиентов

# Optional: 3x_ui API config (если появится — бот сможет вызывать API и запрашивать реальные конфиги)
XUI_API_URL = None  # пример: "http://127.0.0.1:54321" или "http://91.184.248.35:54321"
XUI_ADMIN_USER = None
XUI_ADMIN_PASS = None

# Prices (в рублях)
PRICES = {
    "trial": 1,     # 3 дня
    "30": 250,      # 30 дней
    "90": 500,      # 90 дней
    "180": 900,     # 180 дней
}

# Durations (days)
DURATIONS = {
    "trial": 3,
    "30": 30,
    "90": 90,
    "180": 180,
}

# Storage
DATA_FILE = "users_data.json"
POLL_INTERVAL = 4  # seconds between yookassa polling attempts
POLL_TIMEOUT = 5 * 60  # seconds for payment waiting (5 minutes)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ----------------- STORAGE -----------------
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            users_data = json.load(f)
    except Exception as e:
        logger.exception("Failed to load data file, starting with empty store.")
        users_data = {}
else:
    users_data = {}


def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(users_data, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Failed to save data file.")


# ----------------- HELPERS -----------------
def ensure_user_record(user_id: int, username: Optional[str]):
    uid = str(user_id)
    if uid not in users_data:
        users_data[uid] = {
            "username": username or "",
            "is_paid": False,
            "subscription_end": None,
            "trial_used": False,
            "vpn_config_link": None,
        }
        save_data()


def make_ss_link(method: str, password: str, host: str, port: int, name: str) -> str:
    """
    Формируем стандартную ss:// ссылку в формате:
    ss://<base64(method:password@host:port)>#name
    """
    raw = f"{method}:{password}@{host}:{port}"
    b64 = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return f"ss://{b64}#{name}"


async def create_3x_user_and_get_ss(uid: str, days: int) -> str:
    """
    Заглушка: если нет настроенной интеграции с 3x_ui, мы генерируем пароль и возвращаем ss:// ссылку,
    предполагая, что на сервере есть пользователь с этим паролем и портом SHADOWSOCKS_PORT.
    Если ты добавишь XUI_API_URL + creds, сюда можно вставить реальный HTTP-вызов к 3x_ui для создания записи.
    """
    # Если есть реальные креды — можно реализовать здесь API-вызов в 3x_ui и вернуть настоящий конфиг.
    if XUI_API_URL and XUI_ADMIN_USER and XUI_ADMIN_PASS:
        # TODO: реализовать интеграцию с 3x_ui API.
        # Примерный план: получить токен/авторизацию, POST создать пользователя/inbound, прочитать credentials и собрать ss:// ссылку.
        # Сейчас — fallback к генерации.
        pass

    # Генерируем пароль
    password = uuid.uuid4().hex[:16]
    name = f"User{uid}"
    ss_link = make_ss_link(SHADOWSOCKS_METHOD, password, SERVER_IP, SHADOWSOCKS_PORT, name)
    # NOTE: этот пароль нужно вручную/скриптом добавить в 3x_ui или настроить заранее шаблон,
    # либо реализовать интеграцию с 3x_ui API, чтобы ссылка была рабочей.
    return ss_link


def add_subscription(uid: str, days: int):
    user = users_data.get(uid)
    now = datetime.now()
    if user and user.get("subscription_end"):
        try:
            cur_end = datetime.strptime(user["subscription_end"], "%Y-%m-%d")
        except Exception:
            cur_end = now
        if cur_end > now:
            new_end = cur_end + timedelta(days=days)
        else:
            new_end = now + timedelta(days=days)
    else:
        new_end = now + timedelta(days=days)

    users_data[uid]["is_paid"] = True
    users_data[uid]["subscription_end"] = new_end.strftime("%Y-%m-%d")
    save_data()


# ----------------- YOOKASSA -----------------
def yookassa_setup():
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY


def create_payment(user_id: int, amount: int, description: str, metadata: dict):
    """
    Создать платеж и вернуть (confirmation_url, payment_id)
    """
    yookassa_setup()
    payload = {
        "amount": {"value": str(amount), "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": "https://example.com/return"},
        "capture": True,
        "description": description,
        "metadata": metadata,
    }
    p = Payment.create(payload)
    url = p.confirmation.confirmation_url
    pid = p.id
    return url, pid


def get_payment_status(payment_id: str) -> Optional[str]:
    yookassa_setup()
    p = Payment.find_one(payment_id)
    # Depending on SDK version, accessing status might vary; common attr is 'status'
    return getattr(p, "status", None)


# ----------------- TELEGRAM HANDLERS -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id, user.username)

    kb = [
        [InlineKeyboardButton("🆓 Попробовать (3 дня за 1 ₽)", callback_data="pay_trial")],
        [InlineKeyboardButton("💳 30 дней — 250 ₽", callback_data="pay_30")],
        [InlineKeyboardButton("💳 90 дней — 500 ₽", callback_data="pay_90")],
        [InlineKeyboardButton("💳 180 дней — 900 ₽", callback_data="pay_180")],
        [InlineKeyboardButton("📲 Моя подписка", callback_data="status")],
    ]
    text = (
        "Привет! Я выдам тебе ссылку для импорта в Hiddify/v2RayTun (Shadowsocks).\n\n"
        "Выбери тариф:"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    uid = str(user.id)
    ensure_user_record(user.id, user.username)

    if query.data == "status":
        user_rec = users_data.get(uid)
        if user_rec and user_rec.get("is_paid") and user_rec.get("subscription_end"):
            await query.edit_message_text(
                f"Ваша подписка активна до {user_rec['subscription_end']}.\n\n"
                f"Ссылка для импорта:\n{user_rec.get('vpn_config_link') or '—'}"
            )
        else:
            await query.edit_message_text("У вас нет активной подписки. Выберите тариф в меню /start.")
        return

    # Оплаты
    if query.data == "pay_trial":
        # Проверяем, использовал ли пользователь trial
        if users_data[uid].get("trial_used"):
            await query.edit_message_text("Вы уже использовали пробный период.")
            return
        amount = PRICES["trial"]
        days = DURATIONS["trial"]
        description = "Пробный период VPN 3 дня"
        metadata = {"user_id": uid, "tariff": "trial", "days": days}
    elif query.data == "pay_30":
        amount = PRICES["30"]
        days = DURATIONS["30"]
        description = "VPN 30 дней"
        metadata = {"user_id": uid, "tariff": "30", "days": days}
    elif query.data == "pay_90":
        amount = PRICES["90"]
        days = DURATIONS["90"]
        description = "VPN 90 дней"
        metadata = {"user_id": uid, "tariff": "90", "days": days}
    elif query.data == "pay_180":
        amount = PRICES["180"]
        days = DURATIONS["180"]
        description = "VPN 180 дней"
        metadata = {"user_id": uid, "tariff": "180", "days": days}
    else:
        await query.edit_message_text("Неизвестное действие.")
        return

    # Создаём оплату в ЮKassa
    try:
        confirmation_url, payment_id = await context.application.run_in_executor(
            None, create_payment, int(uid), amount, description, metadata
        )
    except Exception as e:
        logger.exception("create_payment failed")
        await query.edit_message_text("Ошибка при создании платежа. Повторите позже.")
        return

    # Сохраняем temporary payment info (можно расширить/логировать)
    users_data[uid]["pending_payment"] = {"payment_id": payment_id, "amount": amount, "created_at": datetime.now().isoformat()}
    save_data()

    # Отправляем ссылку на оплату
    await query.edit_message_text(
        f"Перейдите по ссылке для оплаты ({amount} ₽):\n{confirmation_url}\n\n"
        "После оплаты бот автоматически проверит платёж и выдаст ссылку."
    )

    # Запускаем фоновую таску, которая будет чекать статус платежа
    asyncio.create_task(payment_watcher(payment_id, uid, days, context))


async def payment_watcher(payment_id: str, uid: str, days: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Фоновая проверка статуса платежа. При успешной оплате: создаём SS ссылку и присылаем пользователю.
    """
    logger.info("Start watching payment %s for user %s", payment_id, uid)
    start = datetime.now()
    timeout = timedelta(seconds=POLL_TIMEOUT)

    while datetime.now() - start < timeout:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            status = await context.application.run_in_executor(None, get_payment_status, payment_id)
        except Exception:
            logger.exception("Failed to fetch payment status")
            continue

        logger.info("Payment %s status: %s", payment_id, status)
        if status == "succeeded" or status == "succeeded" or status == "SUCCEEDED":
            # Оплата прошла
            # Зарегистрируем подписку и сгенерируем ссылку
            add_subscription(uid, days)
            # пробный флаг
            meta = users_data[uid].get("pending_payment", {}).get("metadata", {})
            # Если tariff == trial, отмечаем trial_used (we already prevent creating trial twice)
            if users_data[uid].get("subscription_end") and "trial" in (users_data[uid].get("pending_payment", {}).get("payment_id", "") or ""):
                pass

            # Получаем реальную/заглушечную ss-ссылку
            try:
                ss_link = await create_3x_user_and_get_ss(uid, days)
            except Exception:
                logger.exception("Failed to create/generate ss link")
                ss_link = await create_3x_user_and_get_ss(uid, days)

            users_data[uid]["vpn_config_link"] = ss_link
            # Mark trial used if this was trial tariff
            pend = users_data[uid].get("pending_payment", {})
            if pend:
                # We stored metadata originally inside yookassa create; but here simpler:
                # if payment created for trial, user likely already had trial condition - mark it:
                # if there is "tariff" in pending metadata we could use it, but we didn't store metadata; for safety:
                # If price is 1 => trial
                if pend.get("amount") == PRICES["trial"]:
                    users_data[uid]["trial_used"] = True

            users_data[uid].pop("pending_payment", None)
            save_data()

            # Send message to user
            try:
                text = (
                    f"✅ Оплата принята! Ваша подписка активна до {users_data[uid]['subscription_end']}.\n\n"
                    f"Ссылка для импорта (Shadowsocks):\n{ss_link}\n\n"
                    "Скопируйте ссылку и импортируйте в Hiddify или v2RayTun."
                )
                await context.bot.send_message(chat_id=int(uid), text=text)
            except Exception:
                logger.exception("Failed to send success message to user")
            return

        # If payment was canceled or expired - stop
        if status in ("canceled", "expired", "failed"):
            logger.info("Payment %s finished with status %s", payment_id, status)
            try:
                await context.bot.send_message(chat_id=int(uid), text=f"Платёж завершён со статусом: {status}. Попробуйте ещё раз.")
            except Exception:
                pass
            users_data[uid].pop("pending_payment", None)
            save_data()
            return

    # timeout
    logger.info("Payment %s timeout", payment_id)
    try:
        await context.bot.send_message(chat_id=int(uid), text="Время ожидания оплаты истекло. Попробуйте создать платёж заново.")
    except Exception:
        pass
    users_data[uid].pop("pending_payment", None)
    save_data()


async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id, user.username)
    uid = str(user.id)
    rec = users_data[uid]
    if rec["is_paid"] and rec.get("subscription_end"):
        await update.message.reply_text(
            f"Ваша подписка активна до {rec['subscription_end']}.\n\nСсылка:\n{rec.get('vpn_config_link')}"
        )
    else:
        await update.message.reply_text("У вас нет активной подписки. Откройте /start чтобы купить.")


# ----------------- MAIN -----------------
def main():
    # Quick validation of config
    missing = []
    if TELEGRAM_TOKEN.startswith("YOUR_"):
        missing.append("TELEGRAM_TOKEN")
    if YOOKASSA_SHOP_ID.startswith("YOUR_") or YOOKASSA_SECRET_KEY.startswith("YOUR_"):
        # Yookassa creds can be filled later (for testing, you can also run without them, but payments won't work)
        logger.warning("YOOKASSA credentials are placeholder. Replace before creating real payments.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(CommandHandler("info", info_cmd))

    logger.info("Starting bot polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
