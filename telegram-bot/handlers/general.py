"""
handlers/general.py — General commands: /start, /help, /ping.
Uses python-telegram-bot v21 (async).
"""

import time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message with feature overview."""
    await update.message.reply_text(
        "👋 *أهلاً\\! أنا بوت إدارة المجموعات\\.*\n\n"
        "يمكنك استخدامي لـ:\n\n"
        "🛡 *إدارة المجموعة* — حظر، طرد، كتم، ورفع الحظر\\.\n"
        "🎮 *لودو كلوب* — استخراج أكواد الدعوة تلقائياً\\.\n\n"
        "اكتب /help لعرض قائمة الأوامر الكاملة\\.",
        parse_mode="MarkdownV2",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full command reference — moderation + utilities."""
    await update.message.reply_text(
        "📖 *دليل الأوامر*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "🛡 *أوامر الإدارة* \\(للمشرفين فقط\\)\n\n"

        "🔨 *الحظر*\n"
        "`/ban` \\| `بان` \\| `حظر`\n"
        "حظر مستخدم نهائياً من المجموعة\\.\n"
        "_رد على رسالته أو اكتب_ `بان @username`\n\n"

        "✅ *رفع الحظر*\n"
        "`/unban` \\| `الغاء الحظر` \\| `الغاء بان`\n"
        "رفع الحظر والسماح للمستخدم بالعودة\\.\n"
        "_رد على رسالته أو اكتب_ `الغاء الحظر @username`\n\n"

        "👢 *الطرد*\n"
        "`/kick` \\| `طرد`\n"
        "طرد المستخدم مع إمكانية العودة عبر رابط دعوة\\.\n"
        "_رد على رسالته أو اكتب_ `طرد @username`\n\n"

        "🔇 *الكتم*\n"
        "`/mute` \\| `كتم`\n"
        "منع المستخدم من إرسال الرسائل\\.\n"
        "_رد على رسالته أو اكتب_ `كتم @username`\n\n"

        "🔊 *إلغاء الكتم*\n"
        "`/unmute` \\| `الغاء الكتم` \\| `الغاء كتم`\n"
        "إعادة صلاحية الإرسال للمستخدم\\.\n"
        "_رد على رسالته أو اكتب_ `الغاء الكتم @username`\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎮 *لودو كلوب*\n"
        "يقوم البوت تلقائياً باستخراج كود الدعوة من أي رسالة تحتوي على رابط لودو كلوب أو نص مثل `enter my Code: WAKL189`\\.\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "🛠 *الأوامر العامة*\n"
        "`/start` — رسالة الترحيب\n"
        "`/ping` — اختبار سرعة الاستجابة\n"
        "`/help` — هذه القائمة",
        parse_mode="MarkdownV2",
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Measure and report round-trip latency."""
    t0 = time.monotonic()
    msg = await update.message.reply_text("🏓 Pong\\!", parse_mode="MarkdownV2")
    elapsed_ms = (time.monotonic() - t0) * 1000
    await msg.edit_text(
        f"🏓 Pong\\! `{elapsed_ms:.1f} ms`",
        parse_mode="MarkdownV2",
    )


def register(app: Application) -> None:
    """Attach general command handlers to the application."""
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ping", ping))
