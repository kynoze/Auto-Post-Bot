"""
broadcast.py
Broadcast message to all users with Confirmation + Cancel support
Only Bot Owner / Admins can use
Automatically cleans blocked / deleted users + their rules from database
Kurigram (Pyrogram fork) | Python 3.14
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.errors import (
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    UserIsBlocked,
    UserDeactivated,
    ChatWriteForbidden,
)
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from pyrogram.enums import ParseMode

from AutoPost.bot import app
from AutoPost.Database.database import get_db

logger = logging.getLogger(__name__)
db = get_db()

# Active broadcasts tracker
# key = admin_user_id, value = {"cancel": bool, "to_send": Message, ...}
active_broadcasts: Dict[int, dict] = {}


# ---------------------------------------------------------------------------
# Helper: Check Bot Admin
# ---------------------------------------------------------------------------

async def is_bot_admin_check(client: Client, message: Message) -> bool:   
    user_id = message.from_user.id
    if not db.is_bot_admin(user_id):
        await message.reply_text(
            "❌ This command is only for <b>Bot Owner / Admins</b>.",
            parse_mode=ParseMode.HTML,
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Clean blocked / deleted user from database
# ---------------------------------------------------------------------------

def cleanup_dead_user(user_id: int) -> None:
    """
    Remove a blocked or deleted user from the database
    and also delete all forward rules owned by that user.
    """
    try:
        # Delete all rules of this user
        deleted_rules = db.delete_all_rules_of_user(user_id)

        # Delete the user document
        db.users.delete_one({"user_id": user_id})

        logger.info(
            f"Cleaned dead user {user_id} | Rules deleted: {deleted_rules}"
        )
    except Exception as e:
        logger.error(f"Failed to cleanup user {user_id}: {e}")


# ---------------------------------------------------------------------------
# Core Broadcast Function (with cancel support)
# ---------------------------------------------------------------------------

async def broadcast_message(
    client: Client,
    message: Message,
    users: list,
    admin_id: int,
    progress_msg: Message,
) -> dict:
    total = len(users)
    success = 0
    blocked = 0
    deleted = 0
    failed = 0
    cancelled = False

    for idx, user in enumerate(users, 1):
        # Check cancel flag
        if active_broadcasts.get(admin_id, {}).get("cancel"):
            cancelled = True
            break

        user_id = user["user_id"]

        try:
            await message.copy(user_id)
            success += 1

        except FloodWait as e:
            logger.warning(f"FloodWait {e.value}s during broadcast")
            await asyncio.sleep(e.value)
            try:
                await message.copy(user_id)
                success += 1
            except Exception:
                failed += 1

        except (UserIsBlocked, ChatWriteForbidden):
            blocked += 1
            cleanup_dead_user(user_id)          # ← delete from DB + rules

        except (InputUserDeactivated, UserDeactivated, PeerIdInvalid):
            deleted += 1
            cleanup_dead_user(user_id)          # ← delete from DB + rules

        except Exception as e:
            failed += 1
            logger.error(f"Broadcast failed for {user_id}: {e}")

        # Progress update every 15 users
        if idx % 15 == 0 or idx == total:
            try:
                cancel_btn = InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "❌ Cancel Broadcast",
                        callback_data=f"bc_cancel:{admin_id}"
                    )]
                ])
                await progress_msg.edit_text(
                    f"📡 <b>Broadcasting...</b>\n\n"
                    f"✅ Success: <code>{success}</code>\n"
                    f"🚫 Blocked: <code>{blocked}</code>\n"
                    f"🗑 Deleted: <code>{deleted}</code>\n"
                    f"❌ Failed: <code>{failed}</code>\n"
                    f"📊 Progress: <code>{idx}/{total}</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=cancel_btn,
                )
            except Exception:
                pass

        await asyncio.sleep(0.05)

    return {
        "total": total,
        "success": success,
        "blocked": blocked,
        "deleted": deleted,
        "failed": failed,
        "cancelled": cancelled,
        "processed": success + blocked + deleted + failed,
    }


# ---------------------------------------------------------------------------
# Callback Handlers (DEFINE FIRST)
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^bc_confirm:"))
async def confirm_broadcast_callback(client: Client, callback: CallbackQuery):
    admin_id = int(callback.data.split(":")[1])

    if callback.from_user.id != admin_id:
        return await callback.answer(
            "❌ This is not for you.", show_alert=True
        )

    if (
        admin_id not in active_broadcasts
        or active_broadcasts[admin_id]["status"] != "waiting_confirmation"
    ):
        return await callback.answer("Session expired.", show_alert=True)

    await callback.answer()

    data = active_broadcasts[admin_id]
    to_send = data["to_send"]
    confirm_msg = data["confirm_msg"]

    # Update status
    active_broadcasts[admin_id]["status"] = "running"
    active_broadcasts[admin_id]["cancel"] = False

    users = list(db.users.find({}, {"user_id": 1, "_id": 0}))
    total_users = len(users)

    # Start progress message
    cancel_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "❌ Cancel Broadcast",
            callback_data=f"bc_cancel:{admin_id}"
        )]
    ])

    progress_msg = await confirm_msg.edit_text(
        f"📡 <b>Broadcast started...</b>\n"
        f"Total Users: <code>{total_users}</code>\n\n"
        f"Please wait...",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_btn,
    )

    # Run broadcast
    stats = await broadcast_message(
        client=client,
        message=to_send,
        users=users,
        admin_id=admin_id,
        progress_msg=progress_msg,
    )

    # Final report
    if stats["cancelled"]:
        final_text = (
            "🛑 <b>Broadcast Cancelled!</b>\n\n"
            f"👥 Total Users: <code>{stats['total']}</code>\n"
            f"✅ Successfully Sent: <code>{stats['success']}</code>\n"
            f"🚫 Blocked (cleaned): <code>{stats['blocked']}</code>\n"
            f"🗑 Deleted (cleaned): <code>{stats['deleted']}</code>\n"
            f"❌ Failed: <code>{stats['failed']}</code>\n"
            f"📊 Processed before cancel: <code>{stats['processed']}</code>"
        )
    else:
        final_text = (
            "✅ <b>Broadcast Complete!</b>\n\n"
            f"👥 Total Users: <code>{stats['total']}</code>\n"
            f"✅ Successfully Sent: <code>{stats['success']}</code>\n"
            f"🚫 Blocked the Bot (cleaned from DB): <code>{stats['blocked']}</code>\n"
            f"🗑 Deleted / Deactivated (cleaned from DB): <code>{stats['deleted']}</code>\n"
            f"❌ Failed: <code>{stats['failed']}</code>"
        )

    try:
        await progress_msg.edit_text(final_text, parse_mode=ParseMode.HTML)
    except Exception:
        await client.send_message(admin_id, final_text, parse_mode=ParseMode.HTML)

    # Cleanup
    if admin_id in active_broadcasts:
        del active_broadcasts[admin_id]


@app.on_callback_query(filters.regex(r"^bc_cancel_confirm:"))
async def cancel_confirmation_callback(client: Client, callback: CallbackQuery):
    admin_id = int(callback.data.split(":")[1])

    if callback.from_user.id != admin_id:
        return await callback.answer(
            "❌ This is not for you.", show_alert=True
        )

    if admin_id in active_broadcasts:
        del active_broadcasts[admin_id]

    await callback.answer("Broadcast cancelled.")
    await callback.message.edit_text(
        "❌ <b>Broadcast cancelled.</b>",
        parse_mode=ParseMode.HTML,
    )


@app.on_callback_query(filters.regex(r"^bc_cancel:"))
async def cancel_running_broadcast_callback(client: Client, callback: CallbackQuery):
    admin_id = int(callback.data.split(":")[1])

    if callback.from_user.id != admin_id:
        return await callback.answer(
            "❌ This is not for you.", show_alert=True
        )

    if (
        admin_id in active_broadcasts
        and active_broadcasts[admin_id]["status"] == "running"
    ):
        active_broadcasts[admin_id]["cancel"] = True
        await callback.answer(
            "🛑 Cancelling broadcast... Please wait.", show_alert=True
        )
    else:
        await callback.answer("No active broadcast found.", show_alert=True)


# ---------------------------------------------------------------------------
# /broadcast Command → Shows Confirmation (DEFINE AFTER CALLBACKS)
# ---------------------------------------------------------------------------

@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_cmd(client: Client, message: Message):
    if not await is_bot_admin_check(client, message):
        return

    admin_id = message.from_user.id

    # Prevent multiple broadcasts at once
    if admin_id in active_broadcasts:
        return await message.reply_text(
            "⚠️ A broadcast is already running under your account.\n"
            "Please cancel it or wait for it to finish.",
            parse_mode=ParseMode.HTML,
        )

    # Determine message to broadcast
    if message.reply_to_message:
        to_send = message.reply_to_message
        preview = "Replied message"
    elif len(message.command) > 1:
        text = message.text.split(None, 1)[1]
        to_send = await message.reply_text(text)
        preview = text[:100] + ("..." if len(text) > 100 else "")
    else:
        return await message.reply_text(
            "❌ <b>Usage:</b>\n\n"
            "1. Reply to any message with <code>/broadcast</code>\n"
            "2. Or use <code>/broadcast &lt;your message&gt;</code>",
            parse_mode=ParseMode.HTML,
        )

    total_users = db.get_total_users()

    if total_users == 0:
        return await message.reply_text(
            "ℹ️ There are no users in the database yet.",
            parse_mode=ParseMode.HTML,
        )

    # Confirmation keyboard
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Confirm Broadcast",
                callback_data=f"bc_confirm:{admin_id}",
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=f"bc_cancel_confirm:{admin_id}",
            ),
        ]
    ])

    confirm_msg = await message.reply_text(
        f"⚠️ <b>Broadcast Confirmation</b>\n\n"
        f"👥 Total Users: <code>{total_users}</code>\n"
        f"📝 Preview: <code>{preview}</code>\n\n"
        f"Are you sure you want to send this message to <b>all users</b>?",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )

    # Temporarily store the message to broadcast
    active_broadcasts[admin_id] = {
        "cancel": False,
        "to_send": to_send,
        "confirm_msg": confirm_msg,
        "status": "waiting_confirmation",
    }
