# user_bot/keyboards.py
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Login",        callback_data="menu_login"),
         InlineKeyboardButton("📤 Start Ad",     callback_data="menu_start_ad")],
        [InlineKeyboardButton("⏹ Stop",          callback_data="menu_stop"),
         InlineKeyboardButton("📊 Ads Stats",    callback_data="menu_stats")],
        [InlineKeyboardButton("📋 Templates",    callback_data="menu_templates"),
         InlineKeyboardButton("🔍 Forum Finder", callback_data="menu_forum_finder")],
        [InlineKeyboardButton("👤 Profile",      callback_data="menu_profile"),
         InlineKeyboardButton("ℹ️ Support",      callback_data="menu_support")],
    ])


def login_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Account",    callback_data="login_add"),
         InlineKeyboardButton("🗑 Remove Account", callback_data="login_remove")],
        [InlineKeyboardButton("🔙 Back",           callback_data="back_main")],
    ])


def otp_pad_kb(digits: str = "") -> InlineKeyboardMarkup:
    """
    Digital PIN pad for OTP entry.
    digits = currently entered string e.g. "1234"
    Display row shows entered digits with cursor.
    """
    # Display bar — show entered digits or placeholder
    display = digits if digits else "_ _ _ _ _ _"
    display_label = f"🔢  {display}"

    rows = [
        # Display row (non-clickable — shows current input)
        [InlineKeyboardButton(display_label, callback_data="otp_noop")],
        # Number pad
        [
            InlineKeyboardButton("1", callback_data="otp_d:1"),
            InlineKeyboardButton("2", callback_data="otp_d:2"),
            InlineKeyboardButton("3", callback_data="otp_d:3"),
        ],
        [
            InlineKeyboardButton("4", callback_data="otp_d:4"),
            InlineKeyboardButton("5", callback_data="otp_d:5"),
            InlineKeyboardButton("6", callback_data="otp_d:6"),
        ],
        [
            InlineKeyboardButton("7", callback_data="otp_d:7"),
            InlineKeyboardButton("8", callback_data="otp_d:8"),
            InlineKeyboardButton("9", callback_data="otp_d:9"),
        ],
        [
            InlineKeyboardButton("⌫ Delete", callback_data="otp_back"),
            InlineKeyboardButton("0",         callback_data="otp_d:0"),
            InlineKeyboardButton("✅ Submit", callback_data="otp_submit"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="conv_cancel")],
    ]
    return InlineKeyboardMarkup(rows)


def account_select_kb(accounts: list[dict], prefix: str = "acct_select") -> InlineKeyboardMarkup:
    rows = []
    for acc in accounts:
        icon  = "🟢" if acc["is_running"] else "⚫"
        label = f"{icon} {acc['phone']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{prefix}:{acc['id']}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def ad_source_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 All Login Accounts",   callback_data="ad_src:all")],
        [InlineKeyboardButton("⏸ Non-Started Accounts", callback_data="ad_src:idle")],
        [InlineKeyboardButton("🔙 Back",                 callback_data="back_main")],
    ])


def ad_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Custom Message",   callback_data="ad_mode:custom")],
        [InlineKeyboardButton("📝 Saved Message",    callback_data="ad_mode:saved")],
        [InlineKeyboardButton("🔗 Post Link",         callback_data="ad_mode:link")],
        [InlineKeyboardButton("🔙 Back",              callback_data="ad_back_src")],
    ])


def file_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Text Only",  callback_data="ft:text"),
         InlineKeyboardButton("🖼 Photo",      callback_data="ft:photo")],
        [InlineKeyboardButton("🎥 Video",      callback_data="ft:video"),
         InlineKeyboardButton("🎞 GIF",        callback_data="ft:gif")],
        [InlineKeyboardButton("🔙 Back",       callback_data="ad_back_mode")],
    ])


def group_target_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 All My Groups",      callback_data="grp:all")],
        [InlineKeyboardButton("✅ Selected Groups",    callback_data="grp:selected")],
        [InlineKeyboardButton("🔙 Back",               callback_data="ad_back_mode")],
    ])


def topics_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip Topics", callback_data="topics:skip")],
        [InlineKeyboardButton("🔙 Back",        callback_data="ad_back_grp")],
    ])


def stop_kb(accounts: list[dict]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🛑 Stop All", callback_data="stop:all")]]
    for acc in accounts:
        if acc["is_running"]:
            rows.append([InlineKeyboardButton(
                f"⏹ Stop {acc['phone']}", callback_data=f"stop:acct:{acc['id']}"
            )])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh",         callback_data="stats:refresh"),
         InlineKeyboardButton("📡 Check Status",    callback_data="stats:status")],
        [InlineKeyboardButton("💬 Contact Support", callback_data="menu_support")],
        [InlineKeyboardButton("🔙 Back",             callback_data="back_main")],
    ])


def profile_accounts_kb(accounts: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for acc in accounts:
        icon  = "🟢" if acc["is_running"] else "⚫"
        rows.append([InlineKeyboardButton(
            f"{icon} {acc['phone']}", callback_data=f"prof:acct:{acc['id']}"
        )])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def profile_settings_kb(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ Group Delay",       callback_data=f"cfg:gdelay:{account_id}"),
         InlineKeyboardButton("⏳ Batch Delay",       callback_data=f"cfg:pdelay:{account_id}")],
        [InlineKeyboardButton("📢 Track Channel",    callback_data=f"cfg:track:{account_id}")],
        [InlineKeyboardButton("👤 Update Profile",   callback_data=f"pup:menu:{account_id}")],
        [InlineKeyboardButton("🔙 All Accounts",     callback_data="menu_profile")],
    ])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="conv_cancel")]
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")]
    ])


def forum_finder_kb() -> InlineKeyboardMarkup:
    """Entry keyboard for forum topic finder."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Find Forum Topics", callback_data="menu_forum_finder")],
        [InlineKeyboardButton("🔙 Back",              callback_data="back_main")],
    ])


def forum_account_kb(accounts: list[dict]) -> InlineKeyboardMarkup:
    """Pick which account to use for fetching forum topics."""
    rows = []
    for acc in accounts:
        icon = "🟢" if acc["is_running"] else "⚫"
        rows.append([InlineKeyboardButton(
            f"{icon} {acc['phone']}", callback_data=f"forum_acct:{acc['id']}"
        )])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="conv_cancel")])
    return InlineKeyboardMarkup(rows)


def template_list_kb(templates: list[dict], show_use: bool = True) -> InlineKeyboardMarkup:
    """Show saved templates with Use/Delete buttons."""
    rows = []
    for t in templates:
        rows.append([
            InlineKeyboardButton(f"📋 {t['name']}", callback_data=f"tmpl:use:{t['id']}"),
            InlineKeyboardButton("🗑", callback_data=f"tmpl:del:{t['id']}"),
        ])
    rows.append([InlineKeyboardButton("💾 Save Current as Template", callback_data="tmpl:save")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def template_menu_kb(has_templates: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_templates:
        rows.append([InlineKeyboardButton("📋 Use Template", callback_data="tmpl:list")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def profile_update_kb(acc_id: int) -> InlineKeyboardMarkup:
    """Buttons for profile update (name / bio / photo)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Change Name", callback_data=f"pup:name:{acc_id}"),
         InlineKeyboardButton("📝 Change Bio",  callback_data=f"pup:bio:{acc_id}")],
        [InlineKeyboardButton("🖼 Change Photo", callback_data=f"pup:photo:{acc_id}")],
        [InlineKeyboardButton("🔙 Back",         callback_data=f"prof:acct:{acc_id}")],
    ])
