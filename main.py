import json
import uuid
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.ext import filters

# ===== ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ .env =====
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(admin_id) for admin_id in os.getenv("ADMINS", "").split(",") if admin_id.strip()]
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
DATA_FILE = os.getenv("DATA_FILE", "users.json")

# ===== ХРАНИЛКА =====
try:
    with open(DATA_FILE, "r") as f:
        users_data = json.load(f)
except FileNotFoundError:
    users_data = {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(users_data, f, indent=2)

# ===== ОБРАБОТЧИКИ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) not in users_data:
        users_data[str(user.id)] = {
            "username": user.username,
            "is_paid": False,
            "subscription_end": None,
            "vpn_config_link": None
        }
        save_data()

    keyboard = [
        [InlineKeyboardButton("🆓 Пробный (3 дня, 1₽)", callback_data="plan_trial")],
        [InlineKeyboardButton("💳 30 дней — 250₽", callback_data="plan_30")],
        [InlineKeyboardButton("💳 90 дней — 500₽", callback_data="plan_90")],
        [InlineKeyboardButton("💳 180 дней — 900₽", callback_data="plan_180")],
        [InlineKeyboardButton("📲 Моя подписка", callback_data="my_sub")]
    ]
    await update.message.reply_text(
        "Добро пожаловать! Выберите тариф:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = str(user.id)

    if query.data.startswith("plan_"):
        plan = query.data
        price = {"plan_trial": 1, "plan_30": 250, "plan_90": 500, "plan_180": 900}[plan]
        days = {"plan_trial": 3, "plan_30": 30, "plan_90": 90, "plan_180": 180}[plan]
        users_data[user_id]["pending"] = {"days": days, "price": price}
        save_data()

        keyboard = [[InlineKeyboardButton("✅ Я оплатил", callback_data="paid_confirm")]]
        await query.edit_message_text(
            f"Отправьте {price}₽ на номер: {PHONE_NUMBER}\n"
            "После перевода нажмите «Я оплатил».",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "paid_confirm":
        if "pending" not in users_data[user_id]:
            await query.edit_message_text("У вас нет ожидающих оплат.")
            return

        plan = users_data[user_id]["pending"]
        text = (
            f"💸 Новый платёж\n"
            f"Пользователь: @{user.username} ({user.id})\n"
            f"Тариф: {plan['days']} дней / {plan['price']}₽"
        )
        keyboard = [[InlineKeyboardButton("Подтвердить", callback_data=f"approve_{user_id}")]]
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except:
                pass
        await query.edit_message_text("Ожидаем подтверждение администратора.")

    elif query.data.startswith("approve_"):
        target_user_id = query.data.split("_")[1]
        if query.from_user.id not in ADMINS:
            await query.edit_message_text("Только админ может подтверждать платежи.")
            return

        plan = users_data[target_user_id].pop("pending", None)
        if not plan:
            await query.edit_message_text("Нет ожидающего платежа.")
            return

        end_date = (datetime.now() + timedelta(days=plan["days"])).strftime("%Y-%m-%d")
        vpn_link = f"ss://{uuid.uuid4()}@91.184.248.35:443#VPN-{target_user_id}"

        users_data[target_user_id]["is_paid"] = True
        users_data[target_user_id]["subscription_end"] = end_date
        users_data[target_user_id]["vpn_config_link"] = vpn_link
        save_data()

        await context.bot.send_message(
            chat_id=int(target_user_id),
            text=(
                f"✅ Оплата подтверждена!\n"
                f"Подписка активна до {end_date}.\n\n"
                f"Ваша VPN ссылка (нажмите, чтобы скопировать):\n`{vpn_link}`"
            ),
            parse_mode="Markdown"
        )
        await query.edit_message_text("Платёж подтвержден ✅")

    elif query.data == "my_sub":
        user_info = users_data.get(user_id, {})
        if user_info.get("is_paid"):
            await query.edit_message_text(
                f"📅 Подписка активна до {user_info['subscription_end']}\n\n"
                f"🔗 Ваша ссылка (нажмите, чтобы скопировать):\n`{user_info['vpn_config_link']}`",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("У вас нет активной подписки.")

# ===== ЗАПУСК =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()