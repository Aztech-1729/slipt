# admin_bot/bot.py
# ============================================================
#   SLIPT ADMIN BOT (Owner Only) — Fixed & Complete
#   Python 3.12 | python-telegram-bot 20.x
#
#   Fixed:
#     - All ConvHandlers use DEDICATED entry_point handlers
#     - handle_extend_custom_days is a proper standalone function
#     - deny() does NOT double-answer the query
#     - Auto-expiry reminder tracks notified users (no spam)
#     - All return values properly handled
# ============================================================

import os
import asyncio
import logging
from datetime import datetime

from telegram import Update, Bot
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)
from dotenv import load_dotenv

from common.database import db_manager
from admin_bot.keyboards import *
from admin_bot.states import *

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
OWNER_ID        = int(os.getenv("OWNER_ID", "0"))


# ══════════════════════════════════════════════════════════════
#   AUTH
# ══════════════════════════════════════════════════════════════

def is_owner(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid == OWNER_ID


async def deny(update: Update):
    """Deny without double-answering (caller answers query first)."""
    if update.message:
        await update.message.reply_text("⛔ Access denied.")


async def safe_edit_message_text(query, text, **kwargs):
    """Helper to edit message text safely, ignoring 'Message is not modified' errors."""
    try:
        return await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return query.message
        raise e


# ══════════════════════════════════════════════════════════════
#   /start
# ══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("⛔ Access denied.")
        return
    await update.message.reply_text(
        "👑 *Slipt Admin Panel*\n━━━━━━━━━━━━━━━\nWelcome, Owner!",
        reply_markup=admin_main_kb(), parse_mode="Markdown"
    )


# ══════════════════════════════════════════════════════════════
#   GENERAL CALLBACK ROUTER  (non-conversation buttons)
# ══════════════════════════════════════════════════════════════

async def general_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_owner(update):
        # query.answer() already called above — don't call again
        await safe_edit_message_text(query, "⛔ Access denied.")
        return

    data = query.data

    if data in ("admin_back", "admin_cancel"):
        context.user_data.clear()
        await safe_edit_message_text(
            query, "👑 *Slipt Admin Panel*", reply_markup=admin_main_kb(), parse_mode="Markdown"
        )

    elif data == "admin_users":
        await safe_edit_message_text(
            query, "👥 *User Management*", reply_markup=user_management_kb(), parse_mode="Markdown"
        )

    elif data == "admin_view_users":
        await _show_all_users(update, context)

    elif data == "admin_broadcast":
        await safe_edit_message_text(
            query, "📢 *Broadcast*", reply_markup=broadcast_kb(), parse_mode="Markdown"
        )

    elif data == "admin_expiry_remind":
        await _send_expiry_reminders(update, context)

    elif data == "admin_settings":
        s = db_manager.get_settings()
        await safe_edit_message_text(
            query, "⚙️ *Bot Settings*", reply_markup=settings_kb(s["maintenance_mode"]),
            parse_mode="Markdown"
        )

    elif data == "admin_toggle_maintenance":
        s        = db_manager.get_settings()
        new_mode = not s["maintenance_mode"]
        db_manager.update_settings(maintenance_mode=new_mode)
        label = "🟢 ON" if new_mode else "🔴 OFF"
        await safe_edit_message_text(
            query, f"⚙️ Maintenance mode: *{label}*",
            reply_markup=settings_kb(new_mode), parse_mode="Markdown"
        )

    elif data == "admin_stats":
        await _show_global_stats(update, context)

    elif data == "admin_check_expiry":
        await _show_expiring_users(update, context)


# ══════════════════════════════════════════════════════════════
#   GRANT CONVERSATION
# ══════════════════════════════════════════════════════════════

async def grant_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        return ConversationHandler.END

    await safe_edit_message_text(query, 
        "➕ *Grant Access*\n\nFormat:\n`USER_ID | ACCOUNT_LIMIT | AMOUNT_PAID | DAYS`\n\n"
        "Example: `123456789 | 3 | 500 | 30`",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return ADMIN_GRANT_INPUT


async def handle_grant_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END

    try:
        parts       = [p.strip() for p in update.message.text.split("|")]
        user_id     = int(parts[0])
        acct_limit  = int(parts[1])
        amount_paid = float(parts[2])
        days        = int(parts[3])
    except Exception:
        await update.message.reply_text(
            "❌ Wrong format. Use:\n`USER_ID | ACCOUNT_LIMIT | AMOUNT_PAID | DAYS`\n\nTry again:",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return ADMIN_GRANT_INPUT

    expires = db_manager.grant_access(user_id, acct_limit, amount_paid, days)

    try:
        await context.bot.send_message(
            user_id,
            f"🎉 *Access Granted!*\n"
            f"📦 Account Limit: {acct_limit}\n"
            f"📅 Valid for: {days} days\n"
            f"⏳ Expires: {expires.strftime('%Y-%m-%d')}\n\n"
            f"Send /start to begin!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Could not notify user {user_id}: {e}")

    await update.message.reply_text(
        f"✅ Access granted to `{user_id}`\n"
        f"Limit: {acct_limit} accounts | Expires: {expires.strftime('%Y-%m-%d')}",
        reply_markup=admin_main_kb(), parse_mode="Markdown"
    )
    return ConversationHandler.END


async def grant_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await safe_edit_message_text(query, 
"❌ Cancelled.", reply_markup=admin_main_kb())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   REVOKE CONVERSATION
# ══════════════════════════════════════════════════════════════

async def revoke_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        return ConversationHandler.END

    await safe_edit_message_text(query, 
        "🚫 *Revoke Access*\n\nSend the User's Telegram ID:",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return ADMIN_REVOKE_INPUT


async def handle_revoke_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END

    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Try again:", reply_markup=cancel_kb())
        return ADMIN_REVOKE_INPUT

    ok = db_manager.revoke_access(user_id)
    if ok:
        try:
            await context.bot.send_message(
                user_id, "⚠️ Your access has been revoked. Contact support."
            )
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ Access revoked for `{user_id}`",
            reply_markup=admin_main_kb(), parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"❌ User `{user_id}` not found.",
            reply_markup=admin_main_kb(), parse_mode="Markdown"
        )
    return ConversationHandler.END


async def revoke_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await safe_edit_message_text(query, 
"❌ Cancelled.", reply_markup=admin_main_kb())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   EXTEND CONVERSATION
# ══════════════════════════════════════════════════════════════

async def extend_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        return ConversationHandler.END

    await safe_edit_message_text(query, 
        "📅 *Extend Validity*\n\nSend the User's Telegram ID:",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return ADMIN_EXTEND_USER


async def handle_extend_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END

    try:
        user_id = int(update.message.text.strip())
        context.user_data["extend_user_id"] = user_id
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Send a numeric Telegram ID:")
        return ADMIN_EXTEND_USER

    await update.message.reply_text(
        f"📅 Extending for `{user_id}`\n\nChoose how many days to add:",
        reply_markup=extend_kb(), parse_mode="Markdown"
    )
    return ADMIN_EXTEND_PICK


async def handle_extend_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        return ConversationHandler.END

    data = query.data

    if data == "extend_7":
        await _do_extend(update, context, 7)
        return ConversationHandler.END

    elif data == "extend_30":
        await _do_extend(update, context, 30)
        return ConversationHandler.END

    elif data == "extend_custom":
        await safe_edit_message_text(query, 
            "📅 Enter number of days to add:", reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return ADMIN_EXTEND_CUSTOM_DAYS

    elif data == "admin_cancel":
        context.user_data.clear()
        await safe_edit_message_text(query, 
"❌ Cancelled.", reply_markup=admin_main_kb())
        return ConversationHandler.END

    return ADMIN_EXTEND_PICK


async def handle_extend_custom_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END

    try:
        days = int(update.message.text.strip())
        if days < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a positive number of days:")
        return ADMIN_EXTEND_CUSTOM_DAYS

    user_id = context.user_data.get("extend_user_id")
    if not user_id:
        await update.message.reply_text("❌ Session expired. Start again.", reply_markup=admin_main_kb())
        return ConversationHandler.END

    new_exp = db_manager.extend_validity(user_id, days)
    if new_exp:
        try:
            await context.bot.send_message(
                user_id,
                f"🎉 Your subscription extended by *{days} days*!\n"
                f"New expiry: {new_exp.strftime('%Y-%m-%d')}",
                parse_mode="Markdown"
            )
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ Extended `{user_id}` by {days} days.\nExpires: {new_exp.strftime('%Y-%m-%d')}",
            reply_markup=admin_main_kb(), parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ User `{user_id}` not found.", reply_markup=admin_main_kb(), parse_mode="Markdown")

    context.user_data.clear()
    return ConversationHandler.END


async def _do_extend(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int):
    query   = update.callback_query
    user_id = context.user_data.get("extend_user_id")

    if not user_id:
        await safe_edit_message_text(query, 
"❌ No user selected. Start again.", reply_markup=admin_main_kb())
        return

    new_exp = db_manager.extend_validity(user_id, days)
    if new_exp:
        try:
            await context.bot.send_message(
                user_id,
                f"🎉 Subscription extended by *{days} days*!\nExpires: {new_exp.strftime('%Y-%m-%d')}",
                parse_mode="Markdown"
            )
        except Exception:
            pass
        await safe_edit_message_text(query, 
            f"✅ Extended `{user_id}` by {days} days.\nExpires: {new_exp.strftime('%Y-%m-%d')}",
            reply_markup=admin_main_kb(), parse_mode="Markdown"
        )
    else:
        await safe_edit_message_text(query, 
f"❌ User `{user_id}` not found.", reply_markup=admin_main_kb(), parse_mode="Markdown")
    context.user_data.clear()


async def extend_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await safe_edit_message_text(query, 
"❌ Cancelled.", reply_markup=admin_main_kb())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   BROADCAST CONVERSATION
# ══════════════════════════════════════════════════════════════

async def broadcast_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        return ConversationHandler.END

    await safe_edit_message_text(query, 
        "📨 *Global Broadcast*\n\nSend the message to broadcast to ALL active users:",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return ADMIN_BROADCAST_MSG


async def handle_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END

    text  = update.message.text or update.message.caption or ""
    users = db_manager.get_all_users()

    await update.message.reply_text(f"📤 Broadcasting to {len(users)} users…")

    sent = failed = 0
    for user in users:
        if not user["is_active"] or user["is_banned"]:
            continue
        try:
            await context.bot.send_message(
                user["telegram_id"],
                f"📢 *Announcement*\n\n{text}",
                parse_mode="Markdown"
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await update.message.reply_text(
        f"✅ Broadcast done!\n✅ Sent: {sent} | ❌ Failed: {failed}",
        reply_markup=admin_main_kb(), parse_mode="Markdown"
    )
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, 
"❌ Cancelled.", reply_markup=admin_main_kb())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   RULES CONVERSATION
# ══════════════════════════════════════════════════════════════

async def rules_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        return ConversationHandler.END

    current = db_manager.get_settings().get("rules_text", "")
    await safe_edit_message_text(query, 
        f"📝 *Edit Rules*\n\nCurrent:\n`{current[:400]}`\n\nSend new rules text:",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return ADMIN_EDIT_RULES


async def handle_edit_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    db_manager.update_settings(rules_text=update.message.text)
    await update.message.reply_text("✅ Rules updated!", reply_markup=admin_main_kb())
    return ConversationHandler.END


async def rules_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, 
"❌ Cancelled.", reply_markup=admin_main_kb())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   SUPPORT CONVERSATION
# ══════════════════════════════════════════════════════════════

async def support_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        return ConversationHandler.END

    current = db_manager.get_settings().get("support_username", "")
    await safe_edit_message_text(query, 
        f"💬 *Edit Support Username*\n\nCurrent: `{current}`\n\nSend new username (e.g. `@Sliptplug`):",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return ADMIN_EDIT_SUPPORT


async def handle_edit_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    db_manager.update_settings(support_username=update.message.text.strip())
    await update.message.reply_text("✅ Support username updated!", reply_markup=admin_main_kb())
    return ConversationHandler.END


async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, 
"❌ Cancelled.", reply_markup=admin_main_kb())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   INFO PANELS
# ══════════════════════════════════════════════════════════════

async def _show_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    users = db_manager.get_all_users()

    if not users:
        await safe_edit_message_text(query, 
"👥 No users found.", reply_markup=back_kb())
        return

    lines = [f"👥 *All Users* ({len(users)} total)\n"]
    for u in users[:20]:
        name    = u.get("first_name") or u.get("username") or "Unknown"
        status  = "🟢" if u["is_active"] and not u["is_banned"] else "🔴"
        expires = u["expires_at"].strftime("%Y-%m-%d") if u["expires_at"] else "N/A"
        lines.append(f"{status} `{u['telegram_id']}` — {name} | Exp: {expires}")

    if len(users) > 20:
        lines.append(f"\n_...and {len(users) - 20} more_")

    await safe_edit_message_text(query, 
"\n".join(lines), reply_markup=back_kb(), parse_mode="Markdown")


async def _show_global_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    users  = db_manager.get_all_users()
    active = sum(1 for u in users if u["is_active"] and not u["is_banned"])

    await safe_edit_message_text(query, 
        f"📊 *Global Stats*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👥 Total Users:     {len(users)}\n"
        f"✅ Active:          {active}\n"
        f"🚫 Banned/Inactive: {len(users) - active}",
        reply_markup=back_kb(), parse_mode="Markdown"
    )


async def _show_expiring_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    expiring = db_manager.get_expiring_users(hours=24)

    if not expiring:
        await safe_edit_message_text(query, 
"✅ No users expiring within 24 hours.", reply_markup=back_kb())
        return

    lines = [f"⏰ *Expiring in 24h* ({len(expiring)} users)\n"]
    for u in expiring:
        name = u.get("first_name") or str(u["telegram_id"])
        exp  = u["expires_at"].strftime("%Y-%m-%d %H:%M")
        lines.append(f"👤 `{u['telegram_id']}` — {name} | {exp}")

    await safe_edit_message_text(query, 
"\n".join(lines), reply_markup=back_kb(), parse_mode="Markdown")


async def _send_expiry_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    expiring = db_manager.get_expiring_users(hours=24)

    sent = skipped = 0
    for u in expiring:
        if db_manager.was_expiry_notified_today(u["telegram_id"]):
            skipped += 1
            continue
        try:
            await context.bot.send_message(
                u["telegram_id"],
                f"⚠️ *Subscription Reminder*\n\n"
                f"Your subscription expires on `{u['expires_at'].strftime('%Y-%m-%d')}`.\n"
                f"Please renew to continue using Slipt Bot.",
                parse_mode="Markdown"
            )
            db_manager.mark_expiry_notified(u["telegram_id"])
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)

    await safe_edit_message_text(query, 
        f"✅ Reminders sent: {sent} | Already notified today: {skipped}",
        reply_markup=back_kb()
    )


# ══════════════════════════════════════════════════════════════
#   AUTO EXPIRY REMINDER TASK
# ══════════════════════════════════════════════════════════════

async def auto_expiry_reminder_task(bot: Bot):
    """Hourly check — sends reminder once per day per user (no spam)."""
    while True:
        await asyncio.sleep(3600)
        try:
            expiring = db_manager.get_expiring_users(hours=24)
            for u in expiring:
                if db_manager.was_expiry_notified_today(u["telegram_id"]):
                    continue
                try:
                    await bot.send_message(
                        u["telegram_id"],
                        f"⚠️ *Subscription Expiry Reminder!*\n\n"
                        f"Your plan expires on `{u['expires_at'].strftime('%Y-%m-%d')}`.\n"
                        f"Renew now to avoid interruption!",
                        parse_mode="Markdown"
                    )
                    db_manager.mark_expiry_notified(u["telegram_id"])
                except Exception:
                    pass
                await asyncio.sleep(0.05)
        except Exception as e:
            logger.exception(f"Auto expiry task error: {e}")
        try:
            db_manager.cleanup_old_notifications()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#   BUILD APP
# ══════════════════════════════════════════════════════════════

def build_admin_bot():
    app = ApplicationBuilder().token(ADMIN_BOT_TOKEN).build()

    # Grant Conversation
    grant_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(grant_entry, pattern="^admin_grant$")],
        states={
            ADMIN_GRANT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_grant_input)],
        },
        fallbacks=[CallbackQueryHandler(grant_cancel, pattern="^admin_cancel$")],
        per_message=False, allow_reentry=True,
    )

    # Revoke Conversation
    revoke_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(revoke_entry, pattern="^admin_revoke$")],
        states={
            ADMIN_REVOKE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_revoke_input)],
        },
        fallbacks=[CallbackQueryHandler(revoke_cancel, pattern="^admin_cancel$")],
        per_message=False, allow_reentry=True,
    )

    # Extend Conversation
    extend_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(extend_entry, pattern="^admin_extend$")],
        states={
            ADMIN_EXTEND_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_extend_user),
            ],
            ADMIN_EXTEND_PICK: [
                CallbackQueryHandler(handle_extend_pick,
                                     pattern="^(extend_7|extend_30|extend_custom|admin_cancel)$"),
            ],
            ADMIN_EXTEND_CUSTOM_DAYS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_extend_custom_days),
            ],
        },
        fallbacks=[CallbackQueryHandler(extend_cancel, pattern="^admin_cancel$")],
        per_message=False, allow_reentry=True,
    )

    # Broadcast Conversation
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_entry, pattern="^admin_global_bc$")],
        states={
            ADMIN_BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_msg)],
        },
        fallbacks=[CallbackQueryHandler(broadcast_cancel, pattern="^admin_cancel$")],
        per_message=False, allow_reentry=True,
    )

    # Rules Conversation
    rules_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(rules_entry, pattern="^admin_edit_rules$")],
        states={
            ADMIN_EDIT_RULES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_rules)],
        },
        fallbacks=[CallbackQueryHandler(rules_cancel, pattern="^admin_cancel$")],
        per_message=False, allow_reentry=True,
    )

    # Support Conversation
    support_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(support_entry, pattern="^admin_edit_support$")],
        states={
            ADMIN_EDIT_SUPPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_support)],
        },
        fallbacks=[CallbackQueryHandler(support_cancel, pattern="^admin_cancel$")],
        per_message=False, allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(grant_conv)
    app.add_handler(revoke_conv)
    app.add_handler(extend_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(rules_conv)
    app.add_handler(support_conv)
    app.add_handler(CallbackQueryHandler(general_cb))  # catch-all

    return app


if __name__ == "__main__":
    async def main():
        app = build_admin_bot()
        await app.initialize()
        await app.start()
        asyncio.create_task(auto_expiry_reminder_task(app.bot))
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()

    asyncio.run(main())
