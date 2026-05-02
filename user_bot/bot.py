# user_bot/bot.py
import os
import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)
from dotenv import load_dotenv

from common.database import db_manager
from common.session_manager import (
    start_login, submit_otp, submit_2fa,
    cancel_pending_login, get_running_client, get_session_path,
    remove_account_client
)
from common.ad_engine_fixed import launch_ad_task, stop_account, stop_all, is_running
from user_bot.states import *
from user_bot.keyboards import *
import json as _json

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

USER_BOT_TOKEN  = os.getenv("USER_BOT_TOKEN")
OWNER_API_ID    = os.getenv("OWNER_API_ID", "")
OWNER_API_HASH  = os.getenv("OWNER_API_HASH", "")


# ══════════════════════════════════════════════════════════════
#   HELPERS
# ══════════════════════════════════════════════════════════════

def _db_user(tg_id: int, username=None, first_name=None) -> dict:
    return db_manager.create_or_update_user(tg_id, username=username, first_name=first_name)


async def _guard(update: Update) -> bool:
    """Return True if user is allowed; send alert and return False otherwise."""
    uid = update.effective_user.id
    if db_manager.get_settings().get("maintenance_mode"):
        if update.callback_query:
            await update.callback_query.answer("🔧 Bot is under maintenance.", show_alert=True)
        return False
    if not db_manager.is_user_valid(uid):
        if update.callback_query:
            await update.callback_query.answer(
                "⚠️ Access denied or subscription expired.\nContact support.", show_alert=True
            )
        return False
    return True


async def safe_edit_message_text(query, text, **kwargs):
    """Helper to edit message text safely, ignoring 'Message is not modified' errors."""
    try:
        return await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return query.message
        raise e


async def _menu(update: Update, text: str = None):
    """Show main menu -- works from both message and callback contexts."""
    msg = text or "🤖 *Slipt User Bot*\nChoose an option below:"
    if update.callback_query:
        try:
            await safe_edit_message_text(
                update.callback_query, msg, reply_markup=main_menu_kb(), parse_mode="Markdown"
            )
        except Exception:
            try:
                await update.callback_query.message.reply_text(
                    msg, reply_markup=main_menu_kb(), parse_mode="Markdown"
                )
            except Exception:
                pass
    else:
        await update.message.reply_text(msg, reply_markup=main_menu_kb(), parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════
#   /start  and  /help
# ══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    _db_user(tg.id, tg.username, tg.first_name)
    settings = db_manager.get_settings()

    if settings.get("maintenance_mode"):
        await update.message.reply_text("🔧 Bot is under maintenance. Please try later.")
        return

    if not db_manager.is_user_valid(tg.id):
        await update.message.reply_text(
            f"👋 Hello *{tg.first_name}*!\n\n"
            f"{settings.get('rules_text', 'Welcome to Slipt Bot!')}\n\n"
            f"⚠️ You need an active subscription.\n"
            f"Contact: {settings.get('support_username', '@Sliptplug')}",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        f"👋 Welcome back, *{tg.first_name}*!\n\n🤖 *Slipt User Bot*\nReady to send ads 🚀",
        reply_markup=main_menu_kb(), parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Slipt Bot Help*\n\n"
        "Use the buttons in the main menu to navigate:\n\n"
        "➕ *Login* -- Add or remove your Telegram accounts\n"
        "📤 *Start Ad* -- Launch an ad campaign\n"
        "⏹ *Stop* -- Stop running campaigns\n"
        "📊 *Stats* -- View your ad delivery stats\n"
        "👤 *Profile* -- Configure account settings\n"
        "ℹ️ *Support* -- Contact support\n\n"
        "Send /start to return to the main menu at any time.",
        reply_markup=main_menu_kb(), parse_mode="Markdown"
    )


# ══════════════════════════════════════════════════════════════
#   GENERAL CALLBACK HANDLER  (non-conversation)
# ══════════════════════════════════════════════════════════════

async def general_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass  # prevents Telegram timeout

    data = query.data

    # ── Support -- accessible WITHOUT subscription ──────────────
    if data == "menu_support":
        settings = db_manager.get_settings()
        support  = settings.get("support_username", "@Sliptplug")
        await safe_edit_message_text(query, 
            f"ℹ️ *Support*\n\n"
            f"For help, access or subscription contact:\n"
            f"{support}",
            reply_markup=back_kb(), parse_mode="Markdown"
        )
        return

    # ── All other actions require valid subscription ───────────
    if not await _guard(update):
        return

    # ── Navigation ────────────────────────────────────────────
    if data == "back_main":
        await _menu(update)

    elif data == "conv_cancel":
        # Cancel pressed outside any active conversation
        await _menu(update, "❌ Cancelled.")

    elif data in ("ad_back_src", "ad_back_mode", "ad_back_grp"):
        # Back buttons from inside ad flow -- if conv is lost, return to menu
        await _menu(update, "↩️ Returned to main menu.")

    # ── Login submenu ─────────────────────────────────────────
    elif data == "menu_login":
        await safe_edit_message_text(query, 
            "🔐 *Account Management*\nManage your Telegram accounts:",
            reply_markup=login_kb(), parse_mode="Markdown"
        )

    elif data == "login_remove":
        await _show_remove_accounts(update, context)

    elif data.startswith("remove_acct:"):
        await _do_remove_account(update, context, int(data.split(":")[1]))

    # ── Stop ──────────────────────────────────────────────────
    elif data == "menu_stop":
        await _show_stop_panel(update, context)

    elif data == "stop:all":
        u    = _db_user(query.from_user.id)
        accs = db_manager.get_accounts(u["id"])
        ids  = [a["id"] for a in accs if a["is_running"]]
        stop_all(ids)
        await safe_edit_message_text(query, 
            f"⏹ *Stopped {len(ids)} account(s).*",
            reply_markup=back_kb(), parse_mode="Markdown"
        )

    elif data.startswith("stop:acct:"):
        stop_account(int(data.split(":")[2]))
        await query.edit_message_text("⏹ *Account stopped.*", reply_markup=back_kb(), parse_mode="Markdown")

    # ── Stats ─────────────────────────────────────────────────
    elif data in ("menu_stats", "stats:refresh"):
        await _show_stats(update, context)

    elif data == "stats:status":
        await _show_live_status(update, context)

    # ── Profile ───────────────────────────────────────────────
    elif data == "menu_profile":
        await _show_profile_list(update, context)

    elif data.startswith("prof:acct:"):
        await _show_profile_detail(update, context, int(data.split(":")[2]))

    # ── Templates ─────────────────────────────────────────────
    elif data == "menu_templates":
        await _show_templates(update, context)

    elif data == "tmpl:list":
        await _show_templates(update, context)

    elif data.startswith("tmpl:use:"):
        await template_use(update, context, int(data.split(":")[2]))

    elif data.startswith("tmpl:del:"):
        await template_delete(update, context, int(data.split(":")[2]))

    # ── Profile Update menu (pup:menu -- no conv, just show buttons) ──
    elif data.startswith("pup:menu:"):
        await prof_update_entry(update, context)

    # ── Support handled before guard (accessible to all users) ───

    else:
        # FIX: Handle unknown callbacks so user gets a response instead of Telegram timeout
        logger.warning(f"[general_cb] Unhandled callback data: {data!r}")
        await _menu(update)


# ══════════════════════════════════════════════════════════════
#   FALLBACK: unhandled text outside conversations
# ══════════════════════════════════════════════════════════════

async def unhandled_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catches text messages sent outside any conversation -- prevents silent ignore."""
    if not db_manager.is_user_valid(update.effective_user.id):
        return   # silently ignore invalid users
    await update.message.reply_text(
        "👇 Please use the menu buttons below:",
        reply_markup=main_menu_kb()
    )


# ══════════════════════════════════════════════════════════════
#   LOGIN CONVERSATION
# ══════════════════════════════════════════════════════════════

async def login_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Login using owner's fixed API ID/Hash -- user only provides phone + OTP."""
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END
    context.user_data.clear()
    if not OWNER_API_ID or not OWNER_API_HASH:
        await safe_edit_message_text(query, 
            "❌ *Bot not configured.*\nOwner has not set OWNER_API_ID and OWNER_API_HASH in .env.",
            reply_markup=back_kb(), parse_mode="Markdown"
        )
        return ConversationHandler.END
    await safe_edit_message_text(query, 
        "➕ *Add Account*\n\n📱 Send your *Phone Number* (with country code):\nExample: `+91XXXXXXXXXX`",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return STATE_LOGIN_PHONE


# login_api_id and login_api_hash kept as stubs (not used -- owner API is fixed)
async def login_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return ConversationHandler.END

async def login_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return ConversationHandler.END


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone    = update.message.text.strip()
    api_id   = OWNER_API_ID    # always use owner's credentials
    api_hash = OWNER_API_HASH

    u     = _db_user(update.effective_user.id)
    limit = u.get("account_limit", 1)
    count = db_manager.get_user_account_count(u["id"])
    if count >= limit:
        await update.message.reply_text(
            f"⚠️ Account limit reached ({count}/{limit}).\nRemove an account first.",
            reply_markup=main_menu_kb()
        )
        return ConversationHandler.END

    sending_msg = await update.message.reply_text("📲 Sending OTP…")
    result = await start_login(update.effective_user.id, api_id, api_hash, phone)

    if result["status"] == "otp_sent":
        context.user_data["phone"]   = phone
        context.user_data["otp_buf"] = ""   # digit buffer for PIN pad
        await sending_msg.edit_text(
            f"🔐 *OTP Sent!*\n"
            f"📱 Number: `{phone}`\n\n"
            f"👇 Enter your OTP using the keypad below:",
            reply_markup=otp_pad_kb(""),
            parse_mode="Markdown"
        )
        return STATE_LOGIN_OTP

    elif result["status"] == "already_logged_in":
        _save_account(u, phone, api_id, api_hash, get_session_path(phone))
        await update.message.reply_text("✅ Account already active -- saved!", reply_markup=main_menu_kb())
        return ConversationHandler.END

    else:
        await update.message.reply_text(result["message"], reply_markup=main_menu_kb())
        return ConversationHandler.END


async def login_otp_pad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles PIN pad button presses for OTP entry."""
    query = update.callback_query
    await query.answer()

    data    = query.data
    buf     = context.user_data.get("otp_buf", "")
    phone   = context.user_data.get("phone", "")

    if data == "otp_noop":
        # Display button -- do nothing
        return STATE_LOGIN_OTP

    elif data.startswith("otp_d:"):
        # Digit pressed -- max 6 digits
        digit = data.split(":")[1]
        if len(buf) < 5:
            buf += digit
        context.user_data["otp_buf"] = buf
        await query.edit_message_reply_markup(reply_markup=otp_pad_kb(buf))
        return STATE_LOGIN_OTP

    elif data == "otp_back":
        # Backspace
        buf = buf[:-1]
        context.user_data["otp_buf"] = buf
        await query.edit_message_reply_markup(reply_markup=otp_pad_kb(buf))
        return STATE_LOGIN_OTP

    elif data == "otp_submit":
        # Submit OTP
        if not buf:
            await query.answer("⚠️ Please enter OTP first!", show_alert=True)
            return STATE_LOGIN_OTP

        await safe_edit_message_text(query, 
            f"🔄 Verifying OTP `{buf}`…",
            parse_mode="Markdown"
        )
        result = await submit_otp(update.effective_user.id, buf)

        if result["status"] == "success":
            u = _db_user(update.effective_user.id)
            _save_account(u, result["phone"], result["api_id"], result["api_hash"], result["session_file"])
            await safe_edit_message_text(query, 
                "✅ *Account added successfully!*\n\nYou can now start sending ads.",
                reply_markup=main_menu_kb(), parse_mode="Markdown"
            )
            context.user_data.clear()
            return ConversationHandler.END

        elif result["status"] == "2fa_required":
            await safe_edit_message_text(query, 
                "🔐 *2FA Required*\n\nSend your Two-Step Verification password as a text message:",
                reply_markup=cancel_kb(), parse_mode="Markdown"
            )
            return STATE_LOGIN_2FA

        else:
            # Wrong OTP -- reset pad
            context.user_data["otp_buf"] = ""
            await safe_edit_message_text(query, 
                f"🔐 *OTP Sent!*\n"
                f"📱 Number: `{phone}`\n\n"
                f"❌ {result.get('message', 'Wrong OTP')}\n\n"
                f"👇 Try again:",
                reply_markup=otp_pad_kb(""),
                parse_mode="Markdown"
            )
            return STATE_LOGIN_OTP

    return STATE_LOGIN_OTP


async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback: if user types OTP as text (instead of using pad)."""
    otp    = update.message.text.strip()
    phone  = context.user_data.get("phone", "")
    result = await submit_otp(update.effective_user.id, otp)
    if result["status"] == "success":
        u = _db_user(update.effective_user.id)
        _save_account(u, result["phone"], result["api_id"], result["api_hash"], result["session_file"])
        await update.message.reply_text("✅ Account added successfully!", reply_markup=main_menu_kb())
        context.user_data.clear()
        return ConversationHandler.END
    elif result["status"] == "2fa_required":
        await update.message.reply_text(
            "🔐 *2FA Required*\n\nEnter your Two-Step Verification password:",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_LOGIN_2FA
    else:
        context.user_data["otp_buf"] = ""
        await update.message.reply_text(
            f"❌ {result.get('message','Wrong OTP')}\n\nSend OTP pad message again or type OTP:",
            reply_markup=cancel_kb()
        )
        return STATE_LOGIN_OTP


async def login_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = await submit_2fa(update.effective_user.id, update.message.text.strip())
    if result["status"] == "success":
        u = _db_user(update.effective_user.id)
        _save_account(u, result["phone"], result["api_id"], result["api_hash"], result["session_file"])
        await update.message.reply_text("✅ Account added with 2FA!", reply_markup=main_menu_kb())
        return ConversationHandler.END
    else:
        await update.message.reply_text(result["message"] + "\n\nTry again:", reply_markup=cancel_kb())
        return STATE_LOGIN_2FA


async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cancel_pending_login(query.from_user.id)
    context.user_data.clear()
    await _menu(update, "❌ Login cancelled.")
    return ConversationHandler.END


def _save_account(u: dict, phone: str, api_id: str, api_hash: str, session_file: str):
    db_manager.add_account(
        user_id_db=u["id"], phone=phone,
        api_id=str(api_id), api_hash=api_hash, session_file=session_file
    )


# ══════════════════════════════════════════════════════════════
#   ACCOUNT REMOVAL
# ══════════════════════════════════════════════════════════════

async def _show_remove_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    u        = _db_user(query.from_user.id)
    accounts = db_manager.get_accounts(u["id"])

    if not accounts:
        await query.edit_message_text("❌ No accounts to remove.", reply_markup=back_kb())
        return

    rows = [[InlineKeyboardButton(f"🗑 {a['phone']}", callback_data=f"remove_acct:{a['id']}")]
            for a in accounts]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu_login")])
    await safe_edit_message_text(query, 
        "🗑 *Remove Account*\nSelect account to remove:",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown"
    )


async def _do_remove_account(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: int):
    query = update.callback_query
    u     = _db_user(query.from_user.id)
    if is_running(account_id):
        stop_account(account_id)
    # FIX: Clean up client from memory to prevent memory leak
    await remove_account_client(account_id)
    session_file = db_manager.remove_account(account_id, u["id"])
    if session_file:
        try:
            os.remove(session_file)
        except Exception:
            pass
    await query.edit_message_text("✅ Account removed successfully!", reply_markup=back_kb())


# ══════════════════════════════════════════════════════════════
#   AD FLOW -- UNIFIED CONVERSATION
# ══════════════════════════════════════════════════════════════

async def ad_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END
    context.user_data.clear()
    await safe_edit_message_text(query, 
        "📤 *Start Ad Campaign*\n\nChoose which accounts to use:",
        reply_markup=ad_source_kb(), parse_mode="Markdown"
    )
    return STATE_AD_PICK_ACCOUNTS


async def ad_pick_src(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END

    src      = query.data.split(":")[1]
    u        = _db_user(query.from_user.id)
    accounts = db_manager.get_accounts(u["id"], only_idle=(src == "idle"))

    if not accounts:
        await safe_edit_message_text(query, 
            "❌ No accounts found. Please add an account first via *Login*.",
            reply_markup=back_kb(), parse_mode="Markdown"
        )
        return ConversationHandler.END

    context.user_data["u_id"]     = u["id"]
    context.user_data["accounts"] = accounts
    await safe_edit_message_text(query, 
        "✅ *Select Account* to use for this campaign:",
        reply_markup=account_select_kb(accounts, prefix="ad_acct"), parse_mode="Markdown"
    )
    return STATE_AD_PICK_ACCOUNT


async def ad_pick_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    acc_id = int(query.data.split(":")[1])
    context.user_data["account_id"] = acc_id

    account = db_manager.get_account(acc_id)
    if not account:
        await query.edit_message_text("❌ Account not found.", reply_markup=back_kb())
        return ConversationHandler.END
    context.user_data["account"] = account

    await safe_edit_message_text(query, 
        f"📱 Account: `{account['phone']}`\n\n📤 *Choose Ad Mode:*",
        reply_markup=ad_mode_kb(), parse_mode="Markdown"
    )
    return STATE_AD_PICK_MODE


async def ad_pick_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode  = query.data.split(":")[1]
    context.user_data["ad_mode"] = mode

    if mode == "custom":
        await safe_edit_message_text(query, 
            "✏️ *Custom Message*\n\nChoose content type:",
            reply_markup=file_type_kb(), parse_mode="Markdown"
        )
        return STATE_AD_FILE_TYPE

    elif mode == "saved":
        await safe_edit_message_text(query, 
            "🎯 *Target Groups*\n\nChoose which groups to target:",
            reply_markup=group_target_kb(), parse_mode="Markdown"
        )
        return STATE_AD_GROUP_TARGET

    elif mode == "link":
        await safe_edit_message_text(query, 
            "🔗 *Post Link*\n\nSend the post URL:\n"
            "Public: `https://t.me/channel/123`\n"
            "Private: `https://t.me/c/123456789/42`",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_AD_POST_LINK

    return STATE_AD_PICK_MODE


async def ad_file_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ft    = query.data.split(":")[1]
    context.user_data["file_type"] = ft
    prompt = "✏️ Send your *text message* now:" if ft == "text" else f"✏️ Send your *{ft}* (caption optional):"
    await query.edit_message_text(prompt, reply_markup=cancel_kb(), parse_mode="Markdown")
    return STATE_AD_CUSTOM_CONTENT


async def ad_custom_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    ft  = context.user_data.get("file_type", "text")

    if ft == "text":
        if not msg.text:
            await msg.reply_text("❌ Please send a text message:", reply_markup=cancel_kb())
            return STATE_AD_CUSTOM_CONTENT
        context.user_data.update({"content": msg.text, "file_path": None, "caption": None})

    elif ft == "photo":
        if not msg.photo:
            await msg.reply_text("❌ Please send a photo:", reply_markup=cancel_kb())
            return STATE_AD_CUSTOM_CONTENT
        tg_file = await msg.photo[-1].get_file()
        os.makedirs("tmp", exist_ok=True)
        path    = f"tmp/{tg_file.file_id}.jpg"
        await tg_file.download_to_drive(path)
        context.user_data.update({"file_path": path, "caption": msg.caption or "", "content": ""})

    elif ft == "video":
        if not msg.video:
            await msg.reply_text("❌ Please send a video:", reply_markup=cancel_kb())
            return STATE_AD_CUSTOM_CONTENT
        tg_file = await msg.video.get_file()
        os.makedirs("tmp", exist_ok=True)
        path    = f"tmp/{tg_file.file_id}.mp4"
        await tg_file.download_to_drive(path)
        context.user_data.update({"file_path": path, "caption": msg.caption or "", "content": ""})

    elif ft == "gif":
        if not msg.animation:
            await msg.reply_text("❌ Please send a GIF/animation:", reply_markup=cancel_kb())
            return STATE_AD_CUSTOM_CONTENT
        tg_file = await msg.animation.get_file()
        os.makedirs("tmp", exist_ok=True)
        path    = f"tmp/{tg_file.file_id}.gif"
        await tg_file.download_to_drive(path)
        context.user_data.update({"file_path": path, "caption": msg.caption or "", "content": ""})

    await msg.reply_text(
        "🎯 *Target Groups*\n\nChoose which groups to target:",
        reply_markup=group_target_kb(), parse_mode="Markdown"
    )
    return STATE_AD_GROUP_TARGET


async def ad_post_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    if "t.me" not in link:
        await update.message.reply_text(
            "❌ Invalid link. Must be a t.me URL.\nExample: `https://t.me/c/123456789/42`",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_AD_POST_LINK
    context.user_data["content"] = link
    await update.message.reply_text(
        "🎯 *Target Groups*\n\nChoose which groups to target:",
        reply_markup=group_target_kb(), parse_mode="Markdown"
    )
    return STATE_AD_GROUP_TARGET


async def ad_group_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    grp   = query.data.split(":")[1]

    if grp == "all":
        context.user_data["group_mode"] = "all"
        await safe_edit_message_text(query, 
            "📍 *Forum Topics (Optional)*\n\n"
            "Send topic links (one per line) to target forum threads,\n"
            "OR press *Skip* to send to main chat only:\n"
            "Example: `https://t.me/c/123456/789`",
            reply_markup=topics_kb(), parse_mode="Markdown"
        )
        return STATE_AD_TOPIC_INPUT

    elif grp == "selected":
        context.user_data["group_mode"] = "choose"
        await safe_edit_message_text(query, 
            "✅ *Selected Groups*\n\n"
            "Send usernames or links (one per line):\n"
            "Example:\n`@mygroup1\nhttps://t.me/mygroup2`",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_AD_SELECT_GROUPS

    return STATE_AD_GROUP_TARGET


async def ad_select_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = [g.strip() for g in update.message.text.strip().split("\n") if g.strip()]
    if not groups:
        await update.message.reply_text(
            "❌ No valid groups found. Send at least one:", reply_markup=cancel_kb()
        )
        return STATE_AD_SELECT_GROUPS
    context.user_data["groups"] = groups
    await update.message.reply_text(
        "📍 *Forum Topics (Optional)*\n\nSend topic links OR press *Skip*:",
        reply_markup=topics_kb(), parse_mode="Markdown"
    )
    return STATE_AD_TOPIC_INPUT


async def ad_topics_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["topics_map"] = None
    await safe_edit_message_text(query, 
        "⏱ *Group Delay*\n\nSeconds to wait between each group send (e.g. `30`):",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return STATE_AD_GROUP_DELAY


async def ad_topic_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Parse forum topic links → builds {group_id_str: topic_id} mapping.

    Accepted formats per line:
      https://t.me/c/GROUP_ID/TOPIC_ID           (private group)
      https://t.me/c/GROUP_ID/TOPIC_ID/MSG_ID    (private group, msg in topic)
      https://t.me/USERNAME/TOPIC_ID             (public group -- needs resolve)

    Each group gets exactly ONE topic. Duplicates for same group → last one wins.
    """
    topics_map = {}   # {str(group_id): topic_id}
    errors     = []

    for line in update.message.text.strip().split("\n"):
        line = line.strip().rstrip("/")
        if not line:
            continue

        try:
            parts = [p for p in line.split("/") if p]

            # Private group link: t.me/c/GROUP_ID/TOPIC_ID[/MSG_ID]
            if "c" in parts:
                c_idx    = parts.index("c")
                after_c  = parts[c_idx + 1:]
                # after_c = [GROUP_ID, TOPIC_ID] or [GROUP_ID, TOPIC_ID, MSG_ID]
                if len(after_c) >= 2:
                    group_id = int(after_c[0])
                    topic_id = int(after_c[1])
                    topics_map[str(group_id)] = topic_id
                else:
                    errors.append(line)
                continue

            # Public group link: t.me/USERNAME/TOPIC_ID
            if len(parts) >= 2:
                # Last part is topic_id, second-to-last is username
                topic_id = int(parts[-1])
                username = parts[-2].lstrip("@")
                # Store by lowercase username for consistent matching
                topics_map[f"u:{username.lower()}"] = topic_id
                continue

            errors.append(line)

        except (ValueError, IndexError):
            errors.append(line)

    context.user_data["topics_map"] = topics_map if topics_map else None

    reply = ""
    if errors:
        reply = f"⚠️ Could not parse these lines (skipped):\n{chr(10).join(errors)}\n\n"

    parsed_count = len(topics_map)
    await update.message.reply_text(
        reply + f"✅ Parsed *{parsed_count}* forum topic(s)\n\n"
        f"⏱ *Group Delay*\n\nSeconds to wait between each group send (e.g. `30`):",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return STATE_AD_GROUP_DELAY


async def ad_group_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        delay = int(update.message.text.strip())
        if delay < 1: raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Enter a positive number (e.g. `30`):",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_AD_GROUP_DELAY
    context.user_data["group_delay"] = delay
    await update.message.reply_text(
        "⏳ *Batch Delay*\n\nSeconds to wait after all groups before repeating\n(e.g. `3600` = 1 hour):",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return STATE_AD_PROCESS_DELAY


async def ad_process_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        delay = int(update.message.text.strip())
        if delay < 1: raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Enter a positive number (e.g. `3600`):",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_AD_PROCESS_DELAY
    context.user_data["process_delay"] = delay
    await _launch_campaign(update, context)
    return ConversationHandler.END


async def _launch_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id      = update.effective_user.id
    u_id       = context.user_data.get("u_id") or _db_user(tg_id)["id"]
    account_id = context.user_data.get("account_id")
    account    = context.user_data.get("account") or db_manager.get_account(account_id)

    # FIX: Safe reply helper -- works whether triggered from message or callback context
    async def _reply(text: str, **kwargs):
        if update.message:
            await update.message.reply_text(text, **kwargs)
        elif update.callback_query:
            try:
                await update.callback_query.edit_message_text(text, **kwargs)
            except Exception:
                await update.callback_query.message.reply_text(text, **kwargs)

    if not account:
        await _reply("❌ Account not found. Please start again.", reply_markup=main_menu_kb())
        return

    client = await get_running_client(
        account_id, account["api_id"], account["api_hash"], account["session_file"]
    )
    if not client:
        await _reply(
            "❌ Could not connect to this account. Session may have expired.\n"
            "Please remove and re-add the account.",
            reply_markup=main_menu_kb()
        )
        return

    # Groups are resolved lazily inside ad_engine via dialog cache
    # Here we just pass the mode + selected IDs
    group_mode = context.user_data.get("group_mode", "all")

    # For "choose" mode -- resolve usernames/links to integer IDs now
    sel_groups = []
    if group_mode == "choose":
        raw_groups = context.user_data.get("groups", [])
        await _reply(f"⏳ Resolving {len(raw_groups)} groups…")
        for g in raw_groups:
            try:
                ent = await client.get_entity(g)
                gid = getattr(ent, "id", None)
                if gid is not None:
                    sel_groups.append(gid)
                # FIX: Small delay to avoid FloodWait during bulk resolution
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"Could not resolve group {g}: {e}")
        if not sel_groups:
            await _reply(
                "❌ None of the selected groups could be resolved.", reply_markup=main_menu_kb()
            )
            return

    group_delay   = context.user_data.get("group_delay",   account["group_delay"])
    process_delay = context.user_data.get("process_delay", account["process_delay"])
    file_path     = context.user_data.get("file_path")

    ad_config = {
        "mode":          context.user_data.get("ad_mode", "custom"),
        "content":       context.user_data.get("content", ""),
        "file_type":     context.user_data.get("file_type", "text"),
        "file_path":     file_path,
        "caption":       context.user_data.get("caption", ""),
        "group_mode":    group_mode,          # "all" or "choose"
        "sel_groups":    sel_groups,          # list of int IDs (only for "choose" mode)
        "topics_map":    context.user_data.get("topics_map"),   # {group_id_str: topic_id}
        "group_delay":   group_delay,
        "process_delay": process_delay,
        "track_channel": account["track_channel"],
        "user_id_db":    u_id,
        "phone":         account["phone"],
    }

    loop = asyncio.get_running_loop()
    launch_ad_task(loop, account_id, ad_config, client)

    mode_labels = {"custom": "✏️ Custom", "saved": "📝 Saved", "link": "🔗 Link"}

    # Build a saveable template config from current ad settings
    template_cfg = {
        "ad_mode":           context.user_data.get("ad_mode", "custom"),
        "ad_mode_label":     mode_labels.get(context.user_data.get("ad_mode", "custom"), "?"),
        "group_target_label": group_mode.upper(),
        "ad_group_delay":    group_delay,
        "ad_process_delay":  process_delay,
        "group_mode":        group_mode,
        "account_ids_label": f"{account['phone']}",
    }
    context.user_data["last_ad_config"] = template_cfg

    # Show save-as-template button alongside main menu
    from telegram import InlineKeyboardButton as _IKB, InlineKeyboardMarkup as _IKM
    launched_kb = _IKM([
        [_IKB("💾 Save as Template", callback_data="tmpl:save")],
        [_IKB("🔙 Main Menu",        callback_data="back_main")],
    ])

    await _reply(
        f"🚀 *Campaign Started!*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📱 Account: `{account['phone']}`\n"
        f"📤 Mode: {mode_labels.get(context.user_data.get('ad_mode','custom'))}\n"
        f"🎯 Group Mode: {group_mode.upper()}\n"
        f"⏱ Group Delay: {group_delay}s\n"
        f"⏳ Batch Delay: {process_delay}s\n"
        f"📢 Track: {account['track_channel'] or 'Not set'}\n\n"
        f"_💾 Save these settings as a template for 1-click reuse!_",
        reply_markup=launched_kb, parse_mode="Markdown"
    )

    # NOTE: Do NOT clear last_ad_config here -- needed for template save
    # Only clear ad flow keys, not the template config
    for k in ["ad_mode", "content", "file_type", "file_path", "caption",
              "group_mode", "sel_groups", "topics_map", "ad_group_delay",
              "ad_process_delay", "otp_buf", "phone", "api_id", "api_hash",
              "u_id", "account_id", "from_template"]:
        context.user_data.pop(k, None)


async def ad_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await _menu(update, "❌ Ad campaign cancelled.")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   STOP PANEL
# ══════════════════════════════════════════════════════════════

async def _show_stop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    u        = _db_user(query.from_user.id)
    accounts = db_manager.get_accounts(u["id"])

    if not accounts:
        await query.edit_message_text("❌ No accounts found.", reply_markup=back_kb())
        return

    await safe_edit_message_text(query, 
        "⏹ *Stop Campaigns*\nSelect which to stop:",
        reply_markup=stop_kb(accounts), parse_mode="Markdown"
    )


# ══════════════════════════════════════════════════════════════
#   STATS
# ══════════════════════════════════════════════════════════════

async def _show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    u     = _db_user(query.from_user.id)
    stats = db_manager.get_stats(u["id"])
    name  = update.effective_user.first_name

    text = (
        f"📊 *Ads Stats -- {name}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ Total Success  : `{stats['total_success']}`\n"
        f"❌ Total Failed   : `{stats['total_failed']}`\n"
        f"📅 Today's Success: `{stats['daily_success']}`\n"
        f"━━━━━━━━━━━━━━━"
    )

    await safe_edit_message_text(query, text, reply_markup=stats_kb(), parse_mode="Markdown")
    if query.data == "stats:refresh":
        await query.answer("Refreshed")


async def _show_live_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    u        = _db_user(query.from_user.id)
    accounts = db_manager.get_accounts(u["id"])

    if not accounts:
        await safe_edit_message_text(query, "📡 No accounts found.", reply_markup=stats_kb())
        return

    lines = ["📡 *Live Status*\n"]
    for acc in accounts:
        status = "🟢 Running" if is_running(acc["id"]) else "🔴 Stopped"
        lines.append(f"`{acc['phone']}` -- {status}")

    text = "\n".join(lines)
    await safe_edit_message_text(query, text, reply_markup=stats_kb(), parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════
#   PROFILE
# ══════════════════════════════════════════════════════════════

async def _show_profile_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    u        = _db_user(query.from_user.id)
    accounts = db_manager.get_accounts(u["id"])

    if not accounts:
        await safe_edit_message_text(query, 
            "👤 No accounts added yet.\nGo to *Login* to add one.",
            reply_markup=back_kb(), parse_mode="Markdown"
        )
        return

    await safe_edit_message_text(query, 
        "👤 *Profile Dashboard*\n\nSelect an account to view or configure:",
        reply_markup=profile_accounts_kb(accounts), parse_mode="Markdown"
    )


async def _show_profile_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, acc_id: int):
    query   = update.callback_query
    account = db_manager.get_account(acc_id)
    if not account:
        await query.edit_message_text("❌ Account not found.", reply_markup=back_kb())
        return

    # Get user expiry (account doesn't have its own expiry -- it follows the user's sub)
    tg_id     = query.from_user.id
    u         = _db_user(tg_id)
    expires   = u["expires_at"].strftime("%Y-%m-%d") if u.get("expires_at") else "N/A"
    status    = "🟢 Running" if is_running(acc_id) else "🔴 Offline"
    track     = account["track_channel"] or "Not set"

    await safe_edit_message_text(query, 
        f"📱 *Account Details*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📱 Phone: `{account['phone']}`\n"
        f"🔄 Status: {status}\n"
        f"⏳ Sub Expires: {expires}\n"
        f"⏱️ Group Delay: `{account['group_delay']}s`\n"
        f"⏳ Batch Delay: `{account['process_delay']}s`\n"
        f"📢 Track Channel: `{track}`",
        reply_markup=profile_settings_kb(acc_id), parse_mode="Markdown"
    )


# ══════════════════════════════════════════════════════════════
#   SETTINGS CONVERSATION
# ══════════════════════════════════════════════════════════════

async def cfg_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END

    parts  = query.data.split(":")
    action = parts[1]
    acc_id = int(parts[2])
    context.user_data["setting_account_id"] = acc_id

    if action == "gdelay":
        await safe_edit_message_text(query, 
            "⏱ *Set Group Delay*\n\nSend delay in seconds (e.g. `30`):",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_SET_GROUP_DELAY

    elif action == "pdelay":
        await safe_edit_message_text(query, 
            "⏳ *Set Batch Delay*\n\nSend delay in seconds (e.g. `3600`):",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_SET_PROCESS_DELAY

    elif action == "track":
        await safe_edit_message_text(query, 
            "📢 *Set Track Channel*\n\nSend channel username (e.g. `@mychannel`):\n"
            "_(Make sure this bot is admin in that channel)_",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_SET_TRACK_CHANNEL

    return ConversationHandler.END


async def cfg_group_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        delay  = int(update.message.text.strip())
        if delay < 1: raise ValueError
        db_manager.update_account_settings(context.user_data.get("setting_account_id"), group_delay=delay)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Group delay set to *{delay}s*", reply_markup=main_menu_kb(), parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Enter a positive number:", reply_markup=cancel_kb())
        return STATE_SET_GROUP_DELAY
    return ConversationHandler.END


async def cfg_process_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        delay  = int(update.message.text.strip())
        if delay < 1: raise ValueError
        db_manager.update_account_settings(context.user_data.get("setting_account_id"), process_delay=delay)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Batch delay set to *{delay}s*", reply_markup=main_menu_kb(), parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Enter a positive number:", reply_markup=cancel_kb())
        return STATE_SET_PROCESS_DELAY
    return ConversationHandler.END


async def cfg_track_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel = update.message.text.strip()
    if not channel.startswith("@") and "t.me/" not in channel:
        await update.message.reply_text(
            "❌ Must be a @username or t.me link (e.g. `@mychannel`):",
            reply_markup=cancel_kb(), parse_mode="Markdown"
        )
        return STATE_SET_TRACK_CHANNEL
    db_manager.update_account_settings(context.user_data.get("setting_account_id"), track_channel=channel)
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ Track channel set to *{channel}*", reply_markup=main_menu_kb(), parse_mode="Markdown"
    )
    return ConversationHandler.END


async def cfg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await _menu(update, "❌ Cancelled.")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   FORUM TOPIC FINDER CONVERSATION
# ══════════════════════════════════════════════════════════════

async def forum_finder_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry: user pressed Forum Finder button -- pick account."""
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END

    u        = _db_user(query.from_user.id)
    accounts = db_manager.get_accounts(u["id"])

    if not accounts:
        await safe_edit_message_text(query, 
            "❌ No accounts found. Add an account via *Login* first.",
            reply_markup=back_kb(), parse_mode="Markdown"
        )
        return ConversationHandler.END

    context.user_data["u_id"] = u["id"]
    await safe_edit_message_text(query, 
        "🔍 *Forum Topic Finder*\n\n"
        "Select which account to use for fetching forums:",
        reply_markup=forum_account_kb(accounts), parse_mode="Markdown"
    )
    return STATE_FORUM_PICK_ACCOUNT


async def forum_pick_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Account selected -- ask for keyword."""
    query  = update.callback_query
    await query.answer()
    acc_id = int(query.data.split(":")[1])
    context.user_data["forum_account_id"] = acc_id

    account = db_manager.get_account(acc_id)
    if not account:
        await query.edit_message_text("❌ Account not found.", reply_markup=back_kb())
        return ConversationHandler.END
    context.user_data["forum_account"] = account

    await safe_edit_message_text(query, 
        f"📱 Account: `{account['phone']}`\n\n"
        f"🔍 *Forum Topic Finder*\n\n"
        f"Send the *keyword* to search in topic names:\n"
        f"_(Example: `instagram`, `buy`, `sell`)_",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return STATE_FORUM_SEARCH_KEYWORD


async def forum_search_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Keyword received -- fetch all forum groups and search topics."""
    keyword = update.message.text.strip().lower()
    if not keyword:
        await update.message.reply_text("❌ Please send a valid keyword:", reply_markup=cancel_kb())
        return STATE_FORUM_SEARCH_KEYWORD

    account = context.user_data.get("forum_account")
    if not account:
        await update.message.reply_text("❌ Session lost. Please start again.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    acc_id = account["id"]

    # Show searching message
    status_msg = await update.message.reply_text(
        f"⏳ *Searching...*\n"
        f"📱 Account: `{account['phone']}`\n"
        f"🔍 Keyword: `{keyword}`\n\n"
        f"Fetching all forum groups...",
        parse_mode="Markdown"
    )

    try:
        client = await get_running_client(
            acc_id, account["api_id"], account["api_hash"], account["session_file"]
        )
        if not client:
            await status_msg.edit_text(
                "❌ Could not connect to this account. Session may have expired.\n"
                "Please remove and re-add the account.",
                reply_markup=main_menu_kb()
            )
            return ConversationHandler.END

        # Step 1: Get all dialogs and filter forum groups
        await status_msg.edit_text(
            f"⏳ Fetching all groups from account...\n"
            f"🔍 Keyword: `{keyword}`",
            parse_mode="Markdown"
        )

        from telethon.tl.functions.channels import GetForumTopicsRequest as TelethonGetForumTopics

        dialogs = await client.get_dialogs(limit=500)
        forum_groups = []
        for d in dialogs:
            ent = d.entity
            if getattr(ent, "forum", False):
                forum_groups.append(ent)

        if not forum_groups:
            await status_msg.edit_text(
                "❌ *No forum groups found* in this account.\n\n"
                "Make sure you are a member of some forum groups.",
                reply_markup=back_kb(), parse_mode="Markdown"
            )
            return ConversationHandler.END

        await status_msg.edit_text(
            f"✅ Found *{len(forum_groups)}* forum groups\n"
            f"🔍 Searching for topic: `{keyword}`\n"
            f"⏳ Please wait...",
            parse_mode="Markdown"
        )

        # Step 2: For each forum group, get topics and search keyword
        matched_links = []
        checked = 0

        for ent in forum_groups:
            checked += 1
            try:
                # Get forum topics
                result = await client(TelethonGetForumTopics(
                    channel=ent,
                    offset_date=0,
                    offset_id=0,
                    offset_topic=0,
                    limit=100,
                ))

                group_username = getattr(ent, "username", None)
                group_id       = ent.id

                for topic in result.topics:
                    topic_title = getattr(topic, "title", "").lower()
                    if keyword in topic_title:
                        topic_id = topic.id
                        # Build direct link
                        if group_username:
                            link = f"https://t.me/{group_username}/{topic_id}"
                        else:
                            # Private group -- use t.me/c/ format
                            link = f"https://t.me/c/{group_id}/{topic_id}"
                        matched_links.append(
                            f"📌 {getattr(ent, 'title', str(group_id))} → {topic.title}\n{link}"
                        )

            except Exception:
                pass  # Skip groups we can't access topics from

            # Update status every 10 groups
            if checked % 10 == 0:
                try:
                    await status_msg.edit_text(
                        f"⏳ Checked {checked}/{len(forum_groups)} forums...\n"
                        f"🎯 Found {len(matched_links)} matching topics so far",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        # Step 3: Send results
        if not matched_links:
            await status_msg.edit_text(
                f"❌ *No topics found* with keyword `{keyword}`\n\n"
                f"Checked {checked} forum groups.\n"
                f"Try a different keyword.",
                reply_markup=back_kb(), parse_mode="Markdown"
            )
            return ConversationHandler.END

        # Send summary message
        await status_msg.edit_text(
            f"✅ *Search Complete!*\n"
            f"📊 Forums checked: {checked}\n"
            f"🎯 Topics found: {len(matched_links)}\n"
            f"🔍 Keyword: `{keyword}`\n\n"
            f"Links being sent below 👇",
            parse_mode="Markdown"
        )

        # Split into chunks of 20 links per message (Telegram 4096 char limit)
        chunk_size = 20
        for i in range(0, len(matched_links), chunk_size):
            chunk = matched_links[i:i + chunk_size]
            chunk_num = (i // chunk_size) + 1
            total_chunks = (len(matched_links) + chunk_size - 1) // chunk_size

            header = (
                f"🔗 *Forum Topic Links* ({chunk_num}/{total_chunks})\n"
                f"Keyword: `{keyword}`\n"
                f"━━━━━━━━━━━━━━━\n"
            )

            # Extract just the URLs for easy copy-paste
            urls_only = []
            for entry in chunk:
                lines = entry.split("\n")
                if len(lines) >= 2:
                    urls_only.append(lines[1])

            links_text = "\n".join(urls_only)
            # Send header with Markdown, then links as plain text to avoid
            # parse errors when URLs contain underscores or special chars
            await update.message.reply_text(header, parse_mode="Markdown")
            await update.message.reply_text(links_text)

        # Final message with copy-paste ready block
        await update.message.reply_text(
            f"✅ *Done! {len(matched_links)} topic links sent.*\n\n"
            f"💡 Copy these links and paste them in the\n"
            f"*Forum Topics* field when starting an ad campaign.",
            reply_markup=main_menu_kb(), parse_mode="Markdown"
        )

    except Exception as e:
        await status_msg.edit_text(
            f"❌ Error: {str(e)[:300]}",
            reply_markup=back_kb()
        )

    context.user_data.clear()
    return ConversationHandler.END


async def forum_finder_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await _menu(update, "❌ Forum search cancelled.")
    return ConversationHandler.END



# ══════════════════════════════════════════════════════════════
#   AD TEMPLATES -- Save / Load / Delete
# ══════════════════════════════════════════════════════════════

async def _show_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show saved templates list -- called from general_cb or menu."""
    query = update.callback_query
    u = _db_user(query.from_user.id)
    templates = db_manager.get_templates(u["id"])
    if not templates:
        await safe_edit_message_text(query, 
            "📋 *Ad Templates*\n\nYou have no saved templates yet.\n\n"
            "_After setting up an ad, you can save it as a template for 1-click reuse._",
            reply_markup=back_kb(), parse_mode="Markdown"
        )
        return
    lines = ["📋 *Saved Templates*\n"]
    for t in templates:
        lines.append(f"• {t['name']}")
    await safe_edit_message_text(query, 
        "\n".join(lines) + "\n\nTap a template to use it, or 🗑 to delete:",
        reply_markup=template_list_kb(templates), parse_mode="Markdown"
    )


async def template_use(update: Update, context: ContextTypes.DEFAULT_TYPE, tmpl_id: int):
    """Load a template and pre-fill ad context, then launch ad flow."""
    query = update.callback_query
    u = _db_user(query.from_user.id)
    templates = db_manager.get_templates(u["id"])
    tmpl = next((t for t in templates if t["id"] == tmpl_id), None)
    if not tmpl:
        await query.answer("❌ Template not found.", show_alert=True)
        return
    cfg = tmpl["config"]
    context.user_data.update(cfg)
    context.user_data["from_template"] = tmpl["name"]
    # Confirm to user
    accounts_info = cfg.get("account_ids_label", "All accounts")
    await safe_edit_message_text(query, 
        f"✅ *Template Loaded: {tmpl['name']}*\n\n"
        f"📤 Accounts: {accounts_info}\n"
        f"💬 Ad mode: {cfg.get('ad_mode_label', '?')}\n"
        f"🌐 Groups: {cfg.get('group_target_label', '?')}\n"
        f"⏱️ Group delay: {cfg.get('ad_group_delay', '?')}s\n"
        f"⏳ Batch delay: {cfg.get('ad_process_delay', '?')}s\n\n"
        f"_Ready! Go to 📤 Start Ad to launch with this template._",
        reply_markup=back_kb(), parse_mode="Markdown"
    )


async def template_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, tmpl_id: int):
    """Delete a template."""
    query = update.callback_query
    u = _db_user(query.from_user.id)
    deleted = db_manager.delete_template(tmpl_id, u["id"])
    if deleted:
        await query.answer("🗑 Template deleted.", show_alert=False)
    else:
        await query.answer("❌ Not found.", show_alert=True)
    await _show_templates(update, context)


async def template_save_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for template name -- entry for save-template conversation."""
    query = update.callback_query
    await query.answer()
    # Must have a completed ad config in context
    if not context.user_data.get("last_ad_config"):
        await safe_edit_message_text(query, 
            "⚠️ *No recent ad to save.*\n\nRun an ad first, then save it as a template.",
            reply_markup=back_kb(), parse_mode="Markdown"
        )
        return ConversationHandler.END
    await safe_edit_message_text(query, 
        "💾 *Save as Template*\n\nSend a name for this template:\n_(e.g. Daily Ad, Morning Promo)_",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return STATE_TEMPLATE_SAVE_NAME


async def template_save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive template name and save it."""
    name = update.message.text.strip()
    if not name or len(name) > 50:
        await update.message.reply_text(
            "❌ Name must be 1–50 characters. Try again:", reply_markup=cancel_kb()
        )
        return STATE_TEMPLATE_SAVE_NAME
    u = _db_user(update.effective_user.id)
    cfg = context.user_data.get("last_ad_config", {})
    db_manager.save_template(u["id"], name, cfg)
    context.user_data.pop("last_ad_config", None)
    await update.message.reply_text(
        f"✅ *Template '{name}' saved!*\n\nUse it anytime from 📋 Templates menu.",
        reply_markup=main_menu_kb(), parse_mode="Markdown"
    )
    return ConversationHandler.END


async def template_save_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("last_ad_config", None)
    await _menu(update, "❌ Save cancelled.")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   PROFILE UPDATE -- Name / Bio / Photo via Telethon
# ══════════════════════════════════════════════════════════════

async def prof_update_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show profile update menu for selected account."""
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END
    acc_id = int(query.data.split(":")[2])
    account = db_manager.get_account(acc_id)
    if not account:
        await query.edit_message_text("❌ Account not found.", reply_markup=back_kb())
        return ConversationHandler.END
    context.user_data["pup_account_id"] = acc_id
    await safe_edit_message_text(query, 
        f"👤 *Profile Update*\n"
        f"📱 Account: `{account['phone']}`\n\n"
        f"Choose what to update:",
        reply_markup=profile_update_kb(acc_id), parse_mode="Markdown"
    )
    return ConversationHandler.END


async def prof_update_name_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END
    acc_id = int(query.data.split(":")[2])
    context.user_data["pup_account_id"] = acc_id
    await safe_edit_message_text(query, 
        "✏️ *Change Name*\n\nSend the new *display name* for this account:",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return STATE_PROF_UPDATE_NAME


async def prof_update_bio_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END
    acc_id = int(query.data.split(":")[2])
    context.user_data["pup_account_id"] = acc_id
    await safe_edit_message_text(query, 
        "📝 *Change Bio*\n\nSend the new *bio* for this account:\n_(Max 70 characters)_",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return STATE_PROF_UPDATE_BIO


async def prof_update_photo_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END
    acc_id = int(query.data.split(":")[2])
    context.user_data["pup_account_id"] = acc_id
    await safe_edit_message_text(query, 
        "🖼 *Change Profile Photo*\n\nSend your new *profile photo*:",
        reply_markup=cancel_kb(), parse_mode="Markdown"
    )
    return STATE_PROF_UPDATE_PHOTO


async def _get_pup_client(acc_id: int):
    """Get telethon client for profile update."""
    account = db_manager.get_account(acc_id)
    if not account:
        return None, None
    client = await get_running_client(acc_id, account["api_id"], account["api_hash"], account["session_file"])
    return client, account


async def prof_do_update_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    if not new_name:
        await update.message.reply_text("❌ Name cannot be empty. Try again:", reply_markup=cancel_kb())
        return STATE_PROF_UPDATE_NAME
    acc_id = context.user_data.get("pup_account_id")
    wait_msg = await update.message.reply_text("⏳ Updating name…")
    try:
        client, account = await _get_pup_client(acc_id)
        if not client:
            await wait_msg.edit_text("❌ Could not connect to account. Try again.", reply_markup=back_kb())
            return ConversationHandler.END
        from telethon.tl.functions.account import UpdateProfileRequest
        # Split into first/last name on first space
        parts = new_name.split(" ", 1)
        first = parts[0]
        last  = parts[1] if len(parts) > 1 else ""
        await client(UpdateProfileRequest(first_name=first, last_name=last))
        await wait_msg.edit_text(
            f"✅ *Name updated!*\n`{new_name}`",
            reply_markup=profile_update_kb(acc_id), parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Profile name update error")
        await wait_msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    context.user_data.pop("pup_account_id", None)
    return ConversationHandler.END


async def prof_do_update_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bio = update.message.text.strip()
    if len(bio) > 70:
        await update.message.reply_text("❌ Bio max 70 characters. Try again:", reply_markup=cancel_kb())
        return STATE_PROF_UPDATE_BIO
    acc_id = context.user_data.get("pup_account_id")
    wait_msg = await update.message.reply_text("⏳ Updating bio…")
    try:
        client, account = await _get_pup_client(acc_id)
        if not client:
            await wait_msg.edit_text("❌ Could not connect to account.", reply_markup=back_kb())
            return ConversationHandler.END
        from telethon.tl.functions.account import UpdateProfileRequest
        await client(UpdateProfileRequest(about=bio))
        await wait_msg.edit_text(
            f"✅ *Bio updated!*\n`{bio}`",
            reply_markup=profile_update_kb(acc_id), parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Profile bio update error")
        await wait_msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    context.user_data.pop("pup_account_id", None)
    return ConversationHandler.END


async def prof_do_update_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.photo:
        await msg.reply_text("❌ Please send a *photo* (not a file/document):", reply_markup=cancel_kb(), parse_mode="Markdown")
        return STATE_PROF_UPDATE_PHOTO
    acc_id = context.user_data.get("pup_account_id")
    wait_msg = await msg.reply_text("⏳ Uploading photo…")
    try:
        client, account = await _get_pup_client(acc_id)
        if not client:
            await wait_msg.edit_text("❌ Could not connect to account.", reply_markup=back_kb())
            return ConversationHandler.END
        # Download photo bytes from Telegram
        tg_file = await msg.photo[-1].get_file()
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)
        # Upload via Telethon
        from telethon.tl.functions.photos import UploadProfilePhotoRequest
        uploaded = await client.upload_file(tmp_path)
        await client(UploadProfilePhotoRequest(file=uploaded))
        _os.unlink(tmp_path)
        await wait_msg.edit_text(
            "✅ *Profile photo updated!*",
            reply_markup=profile_update_kb(acc_id), parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Profile photo update error")
        await wait_msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    context.user_data.pop("pup_account_id", None)
    return ConversationHandler.END


async def prof_update_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("pup_account_id", None)
    await _menu(update, "❌ Profile update cancelled.")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
#   BUILD APP
# ══════════════════════════════════════════════════════════════

def build_user_bot():
    app = ApplicationBuilder().token(USER_BOT_TOKEN).build()

    login_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(login_entry, pattern="^login_add$")],
        states={
            # Owner API is fixed -- user only needs to enter phone + OTP (+ 2FA if set)
            STATE_LOGIN_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
            STATE_LOGIN_OTP: [
                CallbackQueryHandler(login_otp_pad, pattern="^otp_(d:|back|submit|noop)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp),
            ],
            STATE_LOGIN_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_2fa)],
        },
        fallbacks=[
            CallbackQueryHandler(login_cancel, pattern="^conv_cancel$"),
            CommandHandler("start", cmd_start),
        ],
        per_message=False, allow_reentry=True,
    )

    ad_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ad_entry, pattern="^menu_start_ad$")],
        states={
            STATE_AD_PICK_ACCOUNTS: [
                CallbackQueryHandler(ad_pick_src,     pattern="^ad_src:"),
                CallbackQueryHandler(ad_cancel,       pattern="^conv_cancel$"),
            ],
            STATE_AD_PICK_ACCOUNT: [
                CallbackQueryHandler(ad_pick_account, pattern="^ad_acct:"),
                CallbackQueryHandler(ad_cancel,       pattern="^conv_cancel$"),
            ],
            STATE_AD_PICK_MODE: [
                CallbackQueryHandler(ad_pick_mode,    pattern="^ad_mode:"),
                CallbackQueryHandler(ad_cancel,       pattern="^conv_cancel$"),
            ],
            STATE_AD_FILE_TYPE: [
                CallbackQueryHandler(ad_file_type,    pattern="^ft:"),
                CallbackQueryHandler(ad_cancel,       pattern="^conv_cancel$"),
            ],
            STATE_AD_CUSTOM_CONTENT: [
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.ANIMATION)
                    & ~filters.COMMAND,
                    ad_custom_content
                ),
                CallbackQueryHandler(ad_cancel, pattern="^conv_cancel$"),
            ],
            STATE_AD_POST_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ad_post_link),
                CallbackQueryHandler(ad_cancel, pattern="^conv_cancel$"),
            ],
            STATE_AD_GROUP_TARGET: [
                CallbackQueryHandler(ad_group_target, pattern="^grp:"),
                CallbackQueryHandler(ad_cancel,       pattern="^conv_cancel$"),
            ],
            STATE_AD_SELECT_GROUPS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ad_select_groups),
                CallbackQueryHandler(ad_cancel, pattern="^conv_cancel$"),
            ],
            STATE_AD_TOPIC_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ad_topic_input),
                CallbackQueryHandler(ad_topics_skip,  pattern="^topics:skip$"),
                CallbackQueryHandler(ad_cancel,       pattern="^conv_cancel$"),
            ],
            STATE_AD_GROUP_DELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ad_group_delay),
                CallbackQueryHandler(ad_cancel, pattern="^conv_cancel$"),
            ],
            STATE_AD_PROCESS_DELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ad_process_delay),
                CallbackQueryHandler(ad_cancel, pattern="^conv_cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(ad_cancel, pattern="^conv_cancel$"),
            CommandHandler("start", cmd_start),
        ],
        per_message=False, allow_reentry=True,
    )

    settings_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cfg_entry, pattern=r"^cfg:(gdelay|pdelay|track):\d+$")],
        states={
            STATE_SET_GROUP_DELAY:   [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_group_delay),
                CallbackQueryHandler(cfg_cancel, pattern="^conv_cancel$"),
            ],
            STATE_SET_PROCESS_DELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_process_delay),
                CallbackQueryHandler(cfg_cancel, pattern="^conv_cancel$"),
            ],
            STATE_SET_TRACK_CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_track_channel),
                CallbackQueryHandler(cfg_cancel, pattern="^conv_cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cfg_cancel, pattern="^conv_cancel$"),
            CommandHandler("start", cmd_start),
        ],
        per_message=False, allow_reentry=True,
    )

    forum_finder_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(forum_finder_entry, pattern="^menu_forum_finder$")],
        states={
            STATE_FORUM_PICK_ACCOUNT: [
                CallbackQueryHandler(forum_pick_account, pattern=r"^forum_acct:\d+$"),
                CallbackQueryHandler(forum_finder_cancel, pattern="^conv_cancel$"),
            ],
            STATE_FORUM_SEARCH_KEYWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, forum_search_keyword),
                CallbackQueryHandler(forum_finder_cancel, pattern="^conv_cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(forum_finder_cancel, pattern="^conv_cancel$"),
            CommandHandler("start", cmd_start),
        ],
        per_message=False, allow_reentry=True,
    )

    # ── Template save conversation ────────────────────────────
    template_save_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(template_save_entry, pattern="^tmpl:save$")],
        states={
            STATE_TEMPLATE_SAVE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, template_save_name),
                CallbackQueryHandler(template_save_cancel, pattern="^conv_cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(template_save_cancel, pattern="^conv_cancel$"),
            CommandHandler("start", cmd_start),
        ],
        per_message=False, allow_reentry=True,
    )

    # ── Profile Update conversation ────────────────────────────
    profile_update_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(prof_update_name_entry,  pattern=r"^pup:name:\d+$"),
            CallbackQueryHandler(prof_update_bio_entry,   pattern=r"^pup:bio:\d+$"),
            CallbackQueryHandler(prof_update_photo_entry, pattern=r"^pup:photo:\d+$"),
        ],
        states={
            STATE_PROF_UPDATE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, prof_do_update_name),
                CallbackQueryHandler(prof_update_cancel, pattern="^conv_cancel$"),
            ],
            STATE_PROF_UPDATE_BIO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, prof_do_update_bio),
                CallbackQueryHandler(prof_update_cancel, pattern="^conv_cancel$"),
            ],
            STATE_PROF_UPDATE_PHOTO: [
                MessageHandler(filters.PHOTO, prof_do_update_photo),
                CallbackQueryHandler(prof_update_cancel, pattern="^conv_cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(prof_update_cancel, pattern="^conv_cancel$"),
            CommandHandler("start", cmd_start),
        ],
        per_message=False, allow_reentry=True,
    )

    # Handler registration -- ORDER MATTERS (convs before general, specific before catch-all)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(login_conv)
    app.add_handler(ad_conv)
    app.add_handler(settings_conv)
    app.add_handler(forum_finder_conv)
    app.add_handler(template_save_conv)
    app.add_handler(profile_update_conv)
    app.add_handler(CallbackQueryHandler(general_cb))
    # Catch stray text messages sent outside any conversation
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unhandled_text))

    return app


if __name__ == "__main__":
    build_user_bot().run_polling(drop_pending_updates=True)
