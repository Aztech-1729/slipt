# admin_bot/keyboards.py
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 User Management",     callback_data="admin_users"),
         InlineKeyboardButton("📢 Broadcast",           callback_data="admin_broadcast")],
        [InlineKeyboardButton("⚙️ Bot Settings",        callback_data="admin_settings"),
         InlineKeyboardButton("📊 Stats Overview",      callback_data="admin_stats")],
        [InlineKeyboardButton("🔔 Check Expiries",      callback_data="admin_check_expiry")],
    ])


def user_management_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Grant Access",        callback_data="admin_grant"),
         InlineKeyboardButton("🚫 Revoke Access",       callback_data="admin_revoke")],
        [InlineKeyboardButton("📅 Extend Validity",     callback_data="admin_extend"),
         InlineKeyboardButton("👁 View All Users",      callback_data="admin_view_users")],
        [InlineKeyboardButton("🔙 Back",                callback_data="admin_back")],
    ])


def settings_kb(maintenance: bool) -> InlineKeyboardMarkup:
    toggle_label = "🟢 Maintenance: ON" if maintenance else "🔴 Maintenance: OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label,             callback_data="admin_toggle_maintenance")],
        [InlineKeyboardButton("📝 Edit Rules",          callback_data="admin_edit_rules"),
         InlineKeyboardButton("💬 Edit Support",        callback_data="admin_edit_support")],
        [InlineKeyboardButton("🔙 Back",                callback_data="admin_back")],
    ])


def broadcast_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 Global Broadcast",   callback_data="admin_global_bc"),
         InlineKeyboardButton("⏰ Expiry Reminder",    callback_data="admin_expiry_remind")],
        [InlineKeyboardButton("🔙 Back",               callback_data="admin_back")],
    ])


def extend_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("+7 Days",  callback_data="extend_7"),
         InlineKeyboardButton("+30 Days", callback_data="extend_30")],
        [InlineKeyboardButton("Custom",   callback_data="extend_custom")],
        [InlineKeyboardButton("❌ Cancel", callback_data="admin_cancel")],
    ])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="admin_cancel")]
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]
    ])
