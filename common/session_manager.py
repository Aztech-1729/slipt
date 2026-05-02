# common/session_manager.py
import asyncio
import logging
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, PasswordHashInvalidError,
    FloodWaitError, PhoneNumberInvalidError
)

logger = logging.getLogger(__name__)

# Use absolute path relative to this file so sessions/ is always in the project root
_BASE_DIR   = Path(__file__).resolve().parent.parent
SESSIONS_DIR = _BASE_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

_active_clients: dict[int, TelegramClient] = {}
_pending_logins: dict[int, dict] = {}


def get_session_path(phone: str) -> str:
    safe = phone.replace("+", "").replace(" ", "")
    return str(SESSIONS_DIR / f"{safe}.session")


async def start_login(telegram_user_id: int, api_id: str, api_hash: str, phone: str) -> dict:
    try:
        session_path = get_session_path(phone)
        client = TelegramClient(session_path.replace(".session", ""), int(api_id), api_hash)
        await client.connect()

        if await client.is_user_authorized():
            await client.disconnect()
            return {"status": "already_logged_in", "message": "Account already logged in."}

        result = await client.send_code_request(phone)
        _pending_logins[telegram_user_id] = {
            "phase": "otp", "client": client, "phone": phone,
            "api_id": api_id, "api_hash": api_hash,
            "phone_code_hash": result.phone_code_hash,
        }
        return {"status": "otp_sent", "message": f"OTP sent to {phone}"}

    except PhoneNumberInvalidError:
        return {"status": "error", "message": "❌ Invalid phone number format."}
    except FloodWaitError as e:
        return {"status": "error", "message": f"❌ Too many attempts. Wait {e.seconds}s."}
    except Exception as e:
        logger.exception("Login error")
        return {"status": "error", "message": f"❌ Error: {e}"}


async def submit_otp(telegram_user_id: int, otp: str) -> dict:
    state = _pending_logins.get(telegram_user_id)
    if not state or state["phase"] != "otp":
        return {"status": "error", "message": "No pending login. Start again."}

    client = state["client"]
    try:
        await client.sign_in(
            phone=state["phone"],
            code=otp,
            phone_code_hash=state["phone_code_hash"]
        )
        # Keep client connected — do NOT disconnect here.
        # Telethon needs to flush the session before we can reopen it.
        # We'll disconnect after saving so session file is written properly.
        session_file = get_session_path(state["phone"])
        phone    = state["phone"]
        api_id   = state["api_id"]
        api_hash = state["api_hash"]
        _pending_logins.pop(telegram_user_id, None)
        # Save session by disconnecting (this flushes the .session file)
        await client.disconnect()
        return {"status": "success", "session_file": session_file,
                "phone": phone, "api_id": api_id, "api_hash": api_hash}

    except SessionPasswordNeededError:
        state["phase"] = "2fa"
        return {"status": "2fa_required", "message": "2FA enabled."}

    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        _pending_logins.pop(telegram_user_id, None)
        try: await client.disconnect()
        except: pass
        return {"status": "error", "message": "❌ Invalid or expired OTP. Please start again."}

    except Exception as e:
        logger.exception("OTP submit error")
        _pending_logins.pop(telegram_user_id, None)
        try: await client.disconnect()
        except: pass
        return {"status": "error", "message": f"❌ Error: {e}"}


async def submit_2fa(telegram_user_id: int, password: str) -> dict:
    state = _pending_logins.get(telegram_user_id)
    if not state or state["phase"] != "2fa":
        return {"status": "error", "message": "No pending 2FA. Start again."}

    client = state["client"]
    try:
        await client.sign_in(password=password)
        session_file = get_session_path(state["phone"])
        phone    = state["phone"]
        api_id   = state["api_id"]
        api_hash = state["api_hash"]
        _pending_logins.pop(telegram_user_id, None)
        await client.disconnect()
        return {"status": "success", "session_file": session_file,
                "phone": phone, "api_id": api_id, "api_hash": api_hash}

    except PasswordHashInvalidError:
        return {"status": "error", "message": "❌ Wrong 2FA password. Try again."}
    except Exception as e:
        logger.exception("2FA error")
        _pending_logins.pop(telegram_user_id, None)
        try: await client.disconnect()
        except: pass
        return {"status": "error", "message": f"❌ Error: {e}"}


def cancel_pending_login(telegram_user_id: int):
    state = _pending_logins.pop(telegram_user_id, None)
    if state and state.get("client"):
        client = state["client"]
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_safe_disconnect(client))
        except RuntimeError:
            pass   # no running loop — client will be GC'd


async def _safe_disconnect(client: TelegramClient):
    try:
        await client.disconnect()
    except Exception:
        pass


async def get_running_client(account_id: int, api_id: str, api_hash: str,
                              session_file: str) -> TelegramClient | None:
    if account_id in _active_clients:
        client = _active_clients[account_id]
        if client.is_connected():
            return client
        _active_clients.pop(account_id, None)

    try:
        # Strip .session suffix — TelegramClient adds it automatically
        session_name = session_file
        if session_name.endswith(".session"):
            session_name = session_name[:-8]
        client = TelegramClient(session_name, int(api_id), api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None
        _active_clients[account_id] = client
        return client
    except Exception:
        logger.exception(f"Failed to connect account {account_id}")
        return None


async def disconnect_client(account_id: int):
    client = _active_clients.pop(account_id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass


def get_pending_phase(telegram_user_id: int) -> str | None:
    state = _pending_logins.get(telegram_user_id)
    return state["phase"] if state else None


async def remove_account_client(account_id: int):
    """FIX: Call this when account is removed — cleans up session from memory to prevent leak."""
    await disconnect_client(account_id)
    logger.info(f"[session] Cleaned up client for account {account_id}")


def get_active_client_count() -> int:
    """Returns number of currently active (connected) clients."""
    return len(_active_clients)
