import os
import re
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")  # Google Sheet ID (між /d/ і /edit)
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME", "Interviews")
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "service_account.json")

# -------------- QUESTIONS ---------------
QUESTIONS_TEXT = [
    ("candidate", "1) Вкажіть, будь ласка, повне ПІБ:"),
    ("phone", "2) Вкажіть Ваш контактний телефон у форматі: 380XXXXXXXXX"),
    ("city", "3) Місто проживання:"),
    ("age", "4) Скільки Вам повних років:"),
    ("education", "5) Який навчальний заклад Ви закінчували? Яка спеціальність?"),
    (
        "equipment",
        "6) Для роботи потрібен ноутбук, телефон і безперебійний інтернет. Як у Вас з цим? Скільки тримає батарея ноутбука?",
    ),
    ("sales_experience", "7) Коротко: який у Вас досвід у продажах і що продавали?"),
    (
        "auto_business",
        "8) Чи працювали Ви в автобізнесі? (Так/Ні). Якщо так, що саме входило в обовʼязки?",
    ),
    ("crm", "9) З якими CRM системами працювали?"),
    ("salary_from", "10) Очікувана з/п від:"),
    ("why_software", "11) Чому хочете продавати програмне забезпечення?"),
    ("case1", "12) Кейс: клієнт каже “В нас уже є програма”. Що відповісте?"),
    ("case2", "13) Кейс: “Дорого”. Як аргументуєте цінність?"),
    ("case3", "14) Кейс: клієнт не відповідає 5 днів. Ваші дії?"),
    ("needs_qs", "15) Які 2–3 питання ви поставите власнику СТО, щоб виявити потреби?"),
    ("why_you", "16) Чому ми маємо обрати саме вас?"),
]

# ---------------- STATES ----------------
(S_TEXT_Q, S_REVIEW, S_ADD_NOTE) = range(3)

# ------------- Google Sheets -------------
def gs_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=scopes)
    return gspread.authorize(creds)


def open_ws():
    gc = gs_client()
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet(WORKSHEET_NAME)


# ------------- Validation -------------
def is_valid_phone(phone: str) -> bool:
    # 380XXXXXXXXX (12 цифр, починається з 380)
    return bool(re.match(r"^380\d{9}$", phone.strip()))


# ------------- UI helpers -------------
def review_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Додати примітку", callback_data="review:add_note")],
            [InlineKeyboardButton("📤 Відправити відповіді", callback_data="review:send")],
            [InlineKeyboardButton("❌ Скасувати", callback_data="review:cancel")],
        ]
    )


def build_review_text(answers: dict, note: str) -> str:
    lines = ["*Перевірте відповіді кандидата:*\n"]
    for key, question in QUESTIONS_TEXT:
        ans = (answers.get(key) or "").strip()
        if not ans:
            ans = "—"
        lines.append(f"*{question}*\n{ans}\n")
    lines.append("*Примітка:*\n" + (note.strip() if note.strip() else "—"))
    return "\n".join(lines)


# ------------- Handlers -------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я аcистент HR менеджера Carbook. Це перший етап співбесіди.\n\n"
        "Команди:\n"
        "/interview — почати\n"
        "/cancel — скасувати"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Скасовано. /interview — щоб почати знову.")
    return ConversationHandler.END


async def interview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["answers"] = {}
    context.user_data["note"] = ""
    context.user_data["q_idx"] = 0

    _key, prompt = QUESTIONS_TEXT[0]
    await update.message.reply_text(prompt)
    return S_TEXT_Q


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q_idx = context.user_data.get("q_idx", 0)
    key, _prompt = QUESTIONS_TEXT[q_idx]
    text = update.message.text.strip()

    # Валідація телефону
    if key == "phone":
        if not is_valid_phone(text):
            await update.message.reply_text(
                "❌ Невірний формат телефону.\n"
                "Введіть у форматі: 380XXXXXXXXX (12 цифр, без пробілів)"
            )
            return S_TEXT_Q

    context.user_data["answers"][key] = text

    q_idx += 1
    context.user_data["q_idx"] = q_idx

    # Є ще питання
    if q_idx < len(QUESTIONS_TEXT):
        _next_key, next_prompt = QUESTIONS_TEXT[q_idx]
        await update.message.reply_text(next_prompt)
        return S_TEXT_Q

    # Питання закінчились -> показуємо review
    review_text = build_review_text(
        context.user_data["answers"], context.user_data.get("note", "")
    )
    await update.message.reply_text(
        review_text, parse_mode="Markdown", reply_markup=review_keyboard()
    )
    return S_REVIEW


async def on_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data

    if action == "review:cancel":
        context.user_data.clear()
        await query.edit_message_text("Скасовано. /interview — щоб почати знову.")
        return ConversationHandler.END

    if action == "review:add_note":
        await query.edit_message_text(
            "Напишіть примітку (коментар менеджера). Якщо не потрібно — напишіть просто `-`.",
            parse_mode="Markdown",
        )
        return S_ADD_NOTE

    if action == "review:send":
        # Записуємо у Google Sheet
        answers = context.user_data.get("answers", {})
        note = context.user_data.get("note", "")

        payload = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "candidate": answers.get("candidate", ""),
            "phone": answers.get("phone", ""),
            "city": answers.get("city", ""),
            "age": answers.get("age", ""),
            "education": answers.get("education", ""),
            "equipment": answers.get("equipment", ""),
            "sales_experience": answers.get("sales_experience", ""),
            "auto_business": answers.get("auto_business", ""),
            "crm": answers.get("crm", ""),
            "salary_from": answers.get("salary_from", ""),
            "why_software": answers.get("why_software", ""),
            "case1": answers.get("case1", ""),
            "case2": answers.get("case2", ""),
            "case3": answers.get("case3", ""),
            "needs_qs": answers.get("needs_qs", ""),
            "why_you": answers.get("why_you", ""),
            "note": note,
        }

        try:
            ws = open_ws()
            header = ws.row_values(1)
            row_values = [payload.get(h, "") for h in header]
            ws.append_row(row_values, value_input_option="USER_ENTERED")

            await query.edit_message_text(
                "Дякуємо, що відгукнулись на нашу вакансію. "
                "Наш HR відділ опрацює відповіді і звʼяжеться з Вами. Гарного дня!\n\n"
                "Для додаткової інформації можете ознайомитись з нашою програмою Carbook на сайті:\n"
                "https://carbook.mobi/"
            )
        except Exception as e:
            await query.edit_message_text(
                f"❌ Помилка запису в Google Sheets:\n`{e}`", parse_mode="Markdown"
            )

        context.user_data.clear()
        return ConversationHandler.END

    return S_REVIEW


async def on_note_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    if note == "-":
        note = ""
    context.user_data["note"] = note

    # Показуємо review з приміткою
    review_text = build_review_text(context.user_data["answers"], context.user_data.get("note", ""))
    await update.message.reply_text(review_text, parse_mode="Markdown", reply_markup=review_keyboard())
    return S_REVIEW


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задано BOT_TOKEN")
    if not SHEET_ID:
        raise RuntimeError("Не задано SHEET_ID")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("interview", interview)],
        states={
            S_TEXT_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)],
            S_REVIEW: [
                CallbackQueryHandler(
                    on_review_callback, pattern=r"^review:(add_note|send|cancel)$"
                )
            ],
            S_ADD_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_note_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cancel))

    app.run_polling()


if __name__ == "__main__":
    main()
