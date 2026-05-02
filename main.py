#!/usr/bin/env python3
# main.py
# ============================================================
#   SLIPT BOT LAUNCHER
#   Runs both User Bot and Admin Bot concurrently
#   Python 3.12.0
# ============================================================

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("slipt_bot.log")
    ]
)
logger = logging.getLogger("main")


async def run_user_bot():
    from user_bot.bot import build_user_bot
    app = build_user_bot()
    await app.initialize()
    await app.start()
    logger.info("✅ User Bot started!")
    await app.updater.start_polling(drop_pending_updates=True)
    return app


async def run_admin_bot():
    from admin_bot.bot import build_admin_bot, auto_expiry_reminder_task
    app = build_admin_bot()
    await app.initialize()
    await app.start()
    logger.info("✅ Admin Bot started!")
    # FIX: Store task reference so it doesn't get garbage collected,
    # and add a done callback to log any crash instead of silently failing
    reminder_task = asyncio.create_task(auto_expiry_reminder_task(app.bot))
    def _on_reminder_done(t: asyncio.Task):
        if not t.cancelled() and t.exception():
            logger.error(f"❌ auto_expiry_reminder_task crashed: {t.exception()}")
    reminder_task.add_done_callback(_on_reminder_done)
    app._reminder_task = reminder_task  # keep reference alive on app object
    await app.updater.start_polling(drop_pending_updates=True)
    return app


async def main():
    logger.info("🚀 Starting Slipt Bot System...")

    user_token  = os.getenv("USER_BOT_TOKEN")
    admin_token = os.getenv("ADMIN_BOT_TOKEN")
    owner_id    = os.getenv("OWNER_ID")

    if not user_token:
        logger.error("❌ USER_BOT_TOKEN not set in .env!")
        sys.exit(1)
    if not admin_token:
        logger.error("❌ ADMIN_BOT_TOKEN not set in .env!")
        sys.exit(1)
    if not owner_id or owner_id == "YOUR_TELEGRAM_USER_ID_HERE":
        logger.error("❌ OWNER_ID not set in .env!")
        sys.exit(1)

    user_app  = await run_user_bot()
    admin_app = await run_admin_bot()

    logger.info("✅ Both bots running! Press Ctrl+C to stop.")

    try:
        await asyncio.Event().wait()  # Run forever
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        await user_app.updater.stop()
        await user_app.stop()
        await user_app.shutdown()
        # FIX: Cancel reminder task before shutting down admin bot
        reminder = getattr(admin_app, "_reminder_task", None)
        if reminder and not reminder.done():
            reminder.cancel()
            try:
                await reminder
            except asyncio.CancelledError:
                pass
        await admin_app.updater.stop()
        await admin_app.stop()
        await admin_app.shutdown()
        logger.info("✅ Bots stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
