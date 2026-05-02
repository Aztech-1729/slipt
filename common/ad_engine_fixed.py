"""
Ad Engine v10 — 100% Adbot Logic Match

KEY FIXES for 100% success rate:
  1. Source entity resolved ONCE before loop, cached — no repeated API calls
  2. FloodWait → cap 10s → skip, count as sent (not failed) — Adbot exact
  3. send_gap delay ONLY after successful send (not after fails) — Adbot exact
  4. Duplicate prevention — each group exactly once per cycle
  5. ALL groups via iter_dialogs full pagination
  6. Forward with tag preserved (premium emoji safe)
  7. sliced_sleep — fast cancel
  8. 30s timeout per send op
  9. Track channel: live "Sending X/Y (Topics: Z)" + cycle summary
"""

import os
import time
import random
import asyncio
import logging

from telethon import TelegramClient
from telethon.tl.functions.messages import ForwardMessagesRequest
from telethon.tl.types import Message
from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    ChannelPrivateError,
    SlowModeWaitError,
    PeerFloodError,
    ChatForwardsRestrictedError,
    ForbiddenError,
    MessageIdInvalidError,
)
from common.database import db_manager

logger = logging.getLogger(__name__)

# { account_id → asyncio.Task }
RUNNING_TASKS: dict[int, asyncio.Task] = {}

# { account_id → {ts, groups} }
DIALOGS_CACHE: dict[int, dict] = {}
CACHE_TTL = 300  # 5 minutes

# Adbot constants
FLOODWAIT_BACKOFF_CAP = 10   # Cap FloodWait per group (seconds)
ROUND_SLEEP_SLICE     = 1.0  # Slice long sleeps for fast cancel
TELETHON_OP_TIMEOUT   = 30   # Per-send timeout (seconds)

# FIX: Retry settings
FLOODWAIT_FULL_WAIT   = True  # Wait actual FloodWait seconds
FLOODWAIT_MAX_WAIT    = 600   # Max seconds to wait for FloodWait before skipping
PEERFLOOD_WAIT        = 60    # Wait 60s on PeerFlood then retry once
SLOWMODE_MAX_WAIT     = 300   # Max seconds to wait for SlowMode
MAX_RETRIES           = 3     # Increased retries for transient errors


# ══════════════════════════════════════════════════════════════════════════════
#  SLICED SLEEP
# ══════════════════════════════════════════════════════════════════════════════

async def sliced_sleep(seconds: float):
    remaining = float(seconds)
    while remaining > 0:
        chunk = min(ROUND_SLEEP_SLICE, remaining)
        await asyncio.sleep(chunk)
        remaining -= chunk


# ══════════════════════════════════════════════════════════════════════════════
#  CONTROL API
# ══════════════════════════════════════════════════════════════════════════════

def is_running(account_id: int) -> bool:
    t = RUNNING_TASKS.get(account_id)
    return t is not None and not t.done()


def stop_account(account_id: int):
    task = RUNNING_TASKS.pop(account_id, None)
    if task and not task.done():
        task.cancel()
    db_manager.set_account_running(account_id, False)
    DIALOGS_CACHE.pop(account_id, None)


def stop_all(account_ids: list):
    for aid in account_ids:
        stop_account(aid)


def launch_ad_task(loop: asyncio.AbstractEventLoop, account_id: int,
                   ad_config: dict, client: TelegramClient):
    if is_running(account_id):
        logger.warning(f"Account {account_id} already running — skipping")
        return RUNNING_TASKS[account_id]
    task = loop.create_task(_ad_loop(account_id, ad_config, client))
    RUNNING_TASKS[account_id] = task
    db_manager.set_account_running(account_id, True)
    return task


# ══════════════════════════════════════════════════════════════════════════════
#  DIALOG FETCH — ALL groups, full pagination
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_groups(account_id: int, client: TelegramClient) -> list[dict]:
    cached = DIALOGS_CACHE.get(account_id)
    if cached and (time.time() - cached["ts"]) < CACHE_TTL:
        logger.info(f"[acct {account_id}] Cache: {len(cached['groups'])} groups")
        return cached["groups"]

    logger.info(f"[acct {account_id}] Fetching ALL dialogs...")
    groups   = []
    seen_ids = set()

    try:
        async for dialog in client.iter_dialogs():
            ent     = dialog.entity
            is_mega = getattr(ent, "megagroup", False)
            is_giga = getattr(ent, "gigagroup", False)
            if not (is_mega or is_giga):
                continue
            norm_id = abs(ent.id)
            if norm_id in seen_ids:
                continue
            seen_ids.add(norm_id)
            try:
                groups.append({
                    "id":       ent.id,
                    "title":    getattr(ent, "title", str(ent.id)),
                    "forum":    bool(getattr(ent, "forum", False)),
                    "username": getattr(ent, "username", None),
                    "entity":   ent,
                    "peer":     dialog.input_entity,
                })
            except Exception as e:
                logger.debug(f"[acct {account_id}] Skip {ent.id}: {e}")

    except FloodWaitError as e:
        wait = min(e.seconds + 3, 60)
        logger.warning(f"[acct {account_id}] FloodWait {e.seconds}s during fetch")
        await sliced_sleep(wait)
        if not groups and cached:
            return cached["groups"]
    except Exception as e:
        logger.error(f"[acct {account_id}] iter_dialogs error: {e}")
        if cached:
            return cached["groups"]

    groups.sort(key=lambda g: g["title"].lower())
    DIALOGS_CACHE[account_id] = {"ts": time.time(), "groups": groups}
    logger.info(f"[acct {account_id}] Fetched {len(groups)} groups total")
    return groups


# ══════════════════════════════════════════════════════════════════════════════
#  BUILD TARGETS — unique, with topic mapping
# ══════════════════════════════════════════════════════════════════════════════

async def _build_targets(account_id: int, config: dict,
                          client: TelegramClient) -> list[dict]:
    group_mode = config.get("group_mode", "all")
    sel_ids    = set(config.get("sel_groups") or [])
    topics_map = config.get("topics_map") or {}

    all_groups = await _fetch_groups(account_id, client)
    targets    = []
    seen       = set()

    for g in all_groups:
        gid     = g["id"]
        norm_id = abs(gid)

        if group_mode == "choose" and norm_id not in {abs(x) for x in sel_ids}:
            continue
        if norm_id in seen:
            continue
        seen.add(norm_id)

        topic_id = None
        if topics_map:
            topic_id = (
                topics_map.get(str(gid)) or
                topics_map.get(str(norm_id))
            )
            if topic_id is None and g["username"]:
                topic_id = topics_map.get(f"u:{g['username'].lower()}")

        targets.append({
            "id":       gid,
            "title":    g["title"],
            "username": g["username"],
            "entity":   g["entity"],
            "peer":     g["peer"],
            "topic_id": topic_id,
        })

    return targets


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN AD LOOP — Adbot exact logic
# ══════════════════════════════════════════════════════════════════════════════

async def _ad_loop(account_id: int, config: dict, client: TelegramClient):
    try:
        # ── Resolve source entity ONCE before any sending ─────────────────
        # Adbot: saved_from_peer stored once, reused for all groups
        if config.get("mode") == "link" and config.get("content"):
            try:
                logger.info(f"[acct {account_id}] Resolving source entity once...")
                config["_src_entity"], config["_src_id"] = await _parse_post_link(
                    client, config["content"]
                )
                logger.info(f"[acct {account_id}] Source resolved: msg_id={config['_src_id']}")
            except Exception as e:
                logger.error(f"[acct {account_id}] Failed to resolve source link: {e}")
                db_manager.set_account_running(account_id, False)
                return

        while True:
            if not is_running(account_id):
                break

            try:
                targets = await _build_targets(account_id, config, client)
            except Exception as e:
                logger.exception(f"[acct {account_id}] Build targets failed: {e}")
                await sliced_sleep(60)
                continue

            if not targets:
                logger.warning(f"[acct {account_id}] No targets — stopping")
                break

            total        = len(targets)
            topics_count = sum(1 for t in targets if t["topic_id"] is not None)
            g_delay      = config.get("group_delay", 30)
            send_gap     = config.get("send_gap", 0)
            p_delay      = config.get("process_delay", 3600)

            # ETA
            total_secs = total * g_delay
            hrs  = total_secs // 3600
            mins = (total_secs % 3600) // 60
            eta  = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m"

            logger.info(
                f"[acct {account_id}] ━━ CYCLE START ━━ "
                f"{total} groups | {topics_count} forum topics | ETA ~{eta}"
            )

            # Notify start — show groups + topics count (Adbot style)
            if config.get("track_channel"):
                asyncio.ensure_future(_report_cycle_start(
                    client=client,
                    channel=config["track_channel"],
                    phone=config["phone"],
                    total=total,
                    topics_count=topics_count,
                    g_delay=g_delay,
                    eta=eta,
                ))

            success     = 0
            failed      = 0
            failed_list = []

            for i, target in enumerate(targets):
                if not is_running(account_id):
                    logger.info(f"[acct {account_id}] Stopped at {i+1}/{total}")
                    break

                grp_title = target["title"]
                topic_id  = target["topic_id"]
                peer      = target["peer"]
                grp_id    = target["id"]
                username  = target["username"]

                logger.info(
                    f"[acct {account_id}] [{i+1}/{total}] → {grp_title}"
                    + (f" (topic#{topic_id})" if topic_id else "")
                )

                ok, err, msg_id = await _send_one(client, config, peer, topic_id)

                # DB log
                try:
                    db_manager.log_ad(
                        user_id_db=config["user_id_db"],
                        account_id=account_id,
                        group_name=grp_title,
                        group_id=grp_id,
                        topic_name=f"Topic #{topic_id}" if topic_id else None,
                        message_id=msg_id,
                        status="success" if ok else "failed",
                        error_msg=err,
                    )
                except Exception:
                    pass

                if ok:
                    success += 1
                    logger.info(f"  ✅ sent msg_id={msg_id}")

                    # Live progress after each success (Adbot: "Sending X/Y (Topics: Z)")
                    if config.get("track_channel"):
                        asyncio.ensure_future(_report_progress(
                            client=client,
                            channel=config["track_channel"],
                            phone=config["phone"],
                            grp_title=grp_title,
                            grp_id=grp_id,
                            username=username,
                            topic_id=topic_id,
                            msg_id=msg_id,
                            sent=success,
                            total=total,
                            topics_count=topics_count,
                        ))

                    # send_gap ONLY after success (Adbot exact behavior)
                    if send_gap > 0 and i < total - 1:
                        # Add +/- 20% jitter
                        jitter = send_gap * 0.2
                        gap = random.uniform(max(0, send_gap - jitter), send_gap + jitter)
                        await sliced_sleep(gap)

                else:
                    failed += 1
                    failed_list.append((grp_title, err or "Unknown"))
                    logger.warning(f"  ❌ failed: {err}")

                # Group delay between ALL groups (success or fail)
                if i < total - 1:
                    # Add +/- 15% jitter to group_delay
                    jitter = g_delay * 0.15
                    wait_time = random.uniform(max(1, g_delay - jitter), g_delay + jitter)
                    await sliced_sleep(wait_time)

            # ── Cycle done ────────────────────────────────────────────────
            logger.info(
                f"[acct {account_id}] ━━ CYCLE DONE ━━ "
                f"✅{success} ❌{failed} / {total}"
            )

            # Format wait time
            w_hrs  = p_delay // 3600
            w_mins = (p_delay % 3600) // 60
            wait_fmt = f"{w_hrs}h {w_mins}m" if w_hrs > 0 else f"{w_mins}m"

            # Cycle summary + next round wait (Adbot: "Round complete — Waiting Xs")
            if config.get("track_channel"):
                await _report_summary(
                    client=client,
                    channel=config["track_channel"],
                    phone=config["phone"],
                    success=success,
                    failed=failed,
                    total=total,
                    topics_count=topics_count,
                    failed_list=failed_list,
                    wait_fmt=wait_fmt,
                )

            logger.info(f"[acct {account_id}] Waiting {p_delay}s before next cycle...")
            await sliced_sleep(p_delay)

    except asyncio.CancelledError:
        logger.info(f"[acct {account_id}] Cancelled")
    except Exception as e:
        logger.exception(f"[acct {account_id}] Loop crashed: {e}")
    finally:
        db_manager.set_account_running(account_id, False)
        RUNNING_TASKS.pop(account_id, None)
        _cleanup_file(config.get("file_path"))
        logger.info(f"[acct {account_id}] Task ended")


# ══════════════════════════════════════════════════════════════════════════════
#  SEND ONE — Adbot exact error handling
# ══════════════════════════════════════════════════════════════════════════════

async def _send_one(client, config, peer, topic_id):
    """
    FIX v12:
    - FloodWait → wait actual seconds (up to FLOODWAIT_MAX_WAIT) then RETRY
    - PeerFlood → wait 60s then retry
    - SlowMode → wait (up to SLOWMODE_MAX_WAIT) then retry
    - Timeout → retry up to MAX_RETRIES
    - Other errors → skip
    Returns: (ok, error_msg, msg_id)
    """
    mode = config.get("mode", "custom")

    for attempt in range(MAX_RETRIES):
        try:
            mid = await asyncio.wait_for(
                _do_send(client, mode, peer, config, topic_id),
                timeout=TELETHON_OP_TIMEOUT
            )
            return True, None, mid if mid else 0

        except asyncio.TimeoutError:
            logger.warning(f"Send timeout (attempt {attempt+1}/{MAX_RETRIES}) — retrying...")
            await sliced_sleep(5)
            continue

        except FloodWaitError as e:
            wait_secs = int(e.seconds) + 2
            if wait_secs <= FLOODWAIT_MAX_WAIT:
                logger.warning(f"FloodWait {e.seconds}s — waiting {wait_secs}s then retrying (attempt {attempt+1})")
                await sliced_sleep(wait_secs)
                continue
            else:
                logger.warning(f"FloodWait {e.seconds}s — too long, skipping")
                await sliced_sleep(10) # minimal backoff anyway
                return False, f"FloodWait {e.seconds}s (skipped)", None

        except PeerFloodError:
            if attempt < 2: # Max 2 retries for PeerFlood
                logger.warning(f"PeerFloodError — waiting {PEERFLOOD_WAIT}s then retrying")
                await sliced_sleep(PEERFLOOD_WAIT)
                continue
            logger.warning("PeerFloodError persistent — skipping")
            return False, "PeerFlood (skipped)", None

        except SlowModeWaitError as e:
            wait_secs = int(e.seconds) + 1
            if wait_secs <= SLOWMODE_MAX_WAIT:
                logger.warning(f"SlowMode {e.seconds}s — waiting {wait_secs}s then retrying")
                await sliced_sleep(wait_secs)
                continue
            return False, f"SlowMode {e.seconds}s", None

        except ChatForwardsRestrictedError:
            return False, "Forwards restricted", None

        except (ForbiddenError, ChatWriteForbiddenError, UserBannedInChannelError):
            return False, "Forbidden / banned", None

        except ChannelPrivateError:
            return False, "Private / not member", None

        except MessageIdInvalidError:
            return False, "Invalid message id", None

        except Exception as e:
            logger.warning(f"Send error: {e}")
            if "rpc_call_timeout" in str(e).lower() and attempt < MAX_RETRIES - 1:
                await sliced_sleep(5)
                continue
            return False, str(e)[:200], None

    return False, "Max retries reached", None


async def _do_send(client, mode, peer, config, topic_id) -> int:
    if mode == "custom":
        return await _send_custom(client, peer, config, topic_id)
    elif mode == "saved":
        return await _send_saved(client, peer, config, topic_id)
    elif mode == "link":
        return await _send_link(client, peer, config, topic_id)
    else:
        raise Exception(f"Unknown mode: {mode}")


# ══════════════════════════════════════════════════════════════════════════════
#  SEND MODES
# ══════════════════════════════════════════════════════════════════════════════

async def _send_custom(client, peer, config, topic_id) -> int:
    kwargs = {"reply_to": topic_id} if topic_id else {}
    ft, fp = config.get("file_type", "text"), config.get("file_path")
    if ft == "text" or not fp:
        msg = await client.send_message(peer, config.get("content", ""), **kwargs)
    else:
        msg = await client.send_file(
            peer, file=fp, caption=config.get("caption", ""), **kwargs
        )
    return msg.id


async def _send_saved(client, peer, config, topic_id) -> int:
    msgs = await client.get_messages("me", limit=1)
    if not msgs:
        raise Exception("Saved Messages empty")
    m: Message = msgs[0]
    kwargs = {"reply_to": topic_id} if topic_id else {}
    if m.media:
        msg = await client.send_file(peer, file=m.media, caption=m.message or "", **kwargs)
    else:
        msg = await client.send_message(peer, m.message or "", **kwargs)
    return msg.id


async def _send_link(client, peer, config, topic_id) -> int:
    """
    Forward with tag — preserves premium emoji.
    Uses pre-resolved cached entity (resolved once before loop).
    Adbot: send_forward_with_tag with saved_from_peer.
    """
    # Use cached entity — resolved once before loop starts
    src_entity = config["_src_entity"]
    src_id     = config["_src_id"]

    if topic_id is not None:
        result = await client(ForwardMessagesRequest(
            from_peer=src_entity,
            id=[src_id],
            to_peer=peer,
            top_msg_id=topic_id,
        ))
    else:
        result = await client(ForwardMessagesRequest(
            from_peer=src_entity,
            id=[src_id],
            to_peer=peer,
        ))

    # Extract new message ID from result
    new_id = None
    try:
        for upd in getattr(result, "updates", []):
            if hasattr(upd, "id") and hasattr(upd, "message"):
                new_id = upd.id
                break
        if new_id is None and hasattr(result, "id"):
            new_id = result.id
    except Exception:
        pass
    return new_id if new_id else 1


async def _parse_post_link(client, url: str):
    """Parse t.me post URL → (entity, message_id). Called ONCE before loop."""
    url   = url.strip().rstrip("/")
    parts = url.split("/")
    if "/c/" in url:
        i      = parts.index("c")
        cid    = int(f"-100{parts[i+1]}")
        msg_id = int(parts[i+2])
        entity = await client.get_entity(cid)
    else:
        username = parts[-2].lstrip("@")
        msg_id   = int(parts[-1])
        entity   = await client.get_entity(username)
    return entity, msg_id


# ══════════════════════════════════════════════════════════════════════════════
#  TRACK REPORTS — Adbot style
# ══════════════════════════════════════════════════════════════════════════════

def _make_group_link(grp_id: int, msg_id: int, username: str) -> str:
    if username:
        return f"https://t.me/{username}/{msg_id}"
    return f"https://t.me/c/{abs(int(grp_id))}/{msg_id}"


async def _report_cycle_start(client, channel, phone, total, topics_count, g_delay, eta):
    try:
        import html as _html
        text = (
            f"🟢 <b>Ads Started!</b>\n"
            f"I'll keep sending until you press STOP.\n\n"
            f"📱 <code>{_html.escape(str(phone))}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎯 Groups : <b>{total}</b>\n"
            f"🧵 Topics : <b>{topics_count}</b>\n"
            f"⏱ Delay  : <b>{g_delay}s</b>\n"
            f"⏳ ETA    : <b>~{eta}</b>"
        )
        await _safe_send(client, channel, text)
    except Exception as e:
        logger.warning(f"[track] Start report failed: {e}")


async def _report_progress(client, channel, phone,
                            grp_title, grp_id, username, topic_id, msg_id,
                            sent, total, topics_count):
    """Adbot style: 'Sending ads... X / Y (Topics: Z)'"""
    try:
        import html as _html
        safe_title = _html.escape(str(grp_title))
        safe_phone = _html.escape(str(phone))
        link_line  = ""
        topic_line = ""

        if msg_id and grp_id:
            url       = _make_group_link(grp_id, msg_id, username)
            link_line = f'\n🔗 <a href="{url}">View Message</a>'
        if topic_id:
            topic_line = f"\n🧵 Topic: #{topic_id}"

        text = (
            f"🚚 <b>Sending ads… {sent} / {total}</b> (Topics: {topics_count})\n"
            f"📱 <code>{safe_phone}</code>\n"
            f"👥 {safe_title}"
            f"{topic_line}"
            f"{link_line}"
        )
        await _safe_send(client, channel, text)
    except Exception as e:
        logger.warning(f"[track] Progress report failed: {e}")


async def _report_summary(client, channel, phone,
                            success, failed, total, topics_count,
                            failed_list, wait_fmt):
    """Adbot style: 'Round complete — sent to X/Y groups. Waiting Zs'"""
    try:
        import html as _html
        safe_phone  = _html.escape(str(phone))
        success_pct = round((success / total) * 100) if total else 0

        lines = [
            f"✅ <b>Round complete!</b>",
            f"📱 <code>{safe_phone}</code>",
            f"━━━━━━━━━━━━━━━",
            f"🎯 Groups  : <b>{total}</b>",
            f"🧵 Topics  : <b>{topics_count}</b>",
            f"✅ Success : <b>{success}</b> ({success_pct}%)",
            f"❌ Failed  : <b>{failed}</b>",
            f"━━━━━━━━━━━━━━━",
            f"⏳ Next round in: <b>{wait_fmt}</b>",
        ]

        if failed_list:
            lines.append("\n<b>❌ Failed Groups:</b>")
            for grp_name, err in failed_list[:20]:
                safe_name = _html.escape(str(grp_name))
                safe_err  = _html.escape(str(err)[:60]) if err else "Unknown"
                lines.append(f"• {safe_name} — <code>{safe_err}</code>")
            if len(failed_list) > 20:
                lines.append(f"...and {len(failed_list) - 20} more")

        await _safe_send(client, channel, "\n".join(lines))
    except Exception as e:
        logger.warning(f"[track] Summary failed: {e}")


async def _safe_send(client, channel, text: str):
    kwargs = {"parse_mode": "html"}
    try:
        await client.send_message(channel, text, **kwargs)
        return
    except Exception as e1:
        logger.warning(f"[track] Direct send failed: {e1}")
    try:
        ch = await client.get_entity(channel)
        await client.send_message(ch, text, **kwargs)
    except Exception as e2:
        logger.error(f"[track] Fallback failed: {e2}")


# ══════════════════════════════════════════════════════════════════════════════
#  UTILS
# ══════════════════════════════════════════════════════════════════════════════

def _cleanup_file(path):
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
