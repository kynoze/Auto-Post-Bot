"""
command_helpers.py
Shared helpers for management commands (not handlers).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.types import Message

from AutoPost.Database.database import get_db
from AutoPost.helper.user_bots import get_user_bot_manager
from AutoPost.helper.user_clients import get_user_client_manager

db = get_db()


# ---------------------------------------------------------------------------
# Chat / admin checks
# ---------------------------------------------------------------------------

async def is_user_admin_of_chat(
    client: Client, chat_id: int, user_id: int
) -> bool:
    """
    Check if user is admin/owner of the chat. Updates admin cache.

    Tries the given client first (usually the main management bot).
    If that fails (e.g. main bot is not in the chat), falls back to the
    user's own forwarding bot or connected account client when available.
    This way rule creation does not require the management bot to be
    admin of the target — only the forwarder (user bot / account) does.
    """
    # 1) Try with the provided client (main bot)
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
        ):
            is_owner = member.status == ChatMemberStatus.OWNER
            db.set_channel_admin(
                chat_id,
                user_id,
                is_owner=is_owner,
                status=str(member.status.value),
            )
            return True
        return False
    except Exception:
        pass

    # 2) Fallback: user's forwarding bot
    try:
        if db.has_active_bot(user_id):
            manager = get_user_bot_manager()
            bot_client = await manager.get_bot(user_id)
            if bot_client and bot_client.is_connected:
                member = await bot_client.get_chat_member(chat_id, user_id)
                if member.status in (
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.ADMINISTRATOR,
                ):
                    is_owner = member.status == ChatMemberStatus.OWNER
                    db.set_channel_admin(
                        chat_id,
                        user_id,
                        is_owner=is_owner,
                        status=str(member.status.value),
                    )
                    return True
                return False
    except Exception:
        pass

    # 3) Fallback: user's connected account
    try:
        if db.has_active_session(user_id):
            manager = get_user_client_manager()
            uc = await manager.get_client(user_id)
            if uc and uc.is_connected:
                member = await uc.get_chat_member(chat_id, user_id)
                if member.status in (
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.ADMINISTRATOR,
                ):
                    is_owner = member.status == ChatMemberStatus.OWNER
                    db.set_channel_admin(
                        chat_id,
                        user_id,
                        is_owner=is_owner,
                        status=str(member.status.value),
                    )
                    return True
                return False
    except Exception:
        pass

    return False


async def is_bot_admin_of_chat(client: Client, chat_id: int) -> bool:
    """Check if *this* client (main bot) is admin/owner in the chat."""
    try:
        me = await client.get_chat_member(chat_id, "me")
        return me.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception:
        return False


async def check_admin_status(
    client: Client,
    source_id: int,
    target_id: int,
    user_id: int,
    *,
    require_bot_in_source: bool = True,
) -> Optional[str]:
    """
    Legacy main-bot checks.
    Returns error HTML string or None if OK.
    """
    has_user_session = db.has_active_session(user_id)

    if require_bot_in_source or not has_user_session:
        if not await is_bot_admin_of_chat(client, source_id):
            return (
                "❌ <b>Bot is not an Admin</b> in the <b>Source</b> channel/group.\n\n"
                "Please add the bot as <b>Administrator</b> in the source chat first, "
                "or connect your own Telegram account with /connect."
            )

    if not await is_bot_admin_of_chat(client, target_id):
        return (
            "❌ <b>Bot is not an Admin</b> in the <b>Target</b> channel/group.\n\n"
            "Please add the bot as <b>Administrator</b> in the target chat first."
        )

    if not await is_user_admin_of_chat(client, target_id, user_id):
        return (
            "❌ You must be an <b>Admin/Owner</b> of the <b>Target</b> channel/group."
        )

    if not has_user_session:
        if not await is_user_admin_of_chat(client, source_id, user_id):
            return (
                "❌ You must be an <b>Admin/Owner</b> of the <b>Source</b> channel/group.\n\n"
                "Or connect your own Telegram account with /connect "
                "(then you only need to be a member of the source)."
            )

    return None


async def check_user_bot_permissions(
    user_id: int,
    source_id: int,
    target_id: int,
) -> Optional[str]:
    """User's forwarding bot must be in source + admin on target."""
    if not db.has_active_bot(user_id):
        return (
            "❌ <b>Forwarding bot required</b> for this method.\n"
            "Add one with <code>/addbot</code>"
        )

    manager = get_user_bot_manager()
    bot_client = await manager.get_bot(user_id)
    if not bot_client or not bot_client.is_connected:
        return (
            "❌ Your forwarding bot is offline.\n"
            "Check <code>/mybot</code> or re-add with /addbot."
        )

    try:
        src_me = await bot_client.get_chat_member(source_id, "me")
        src_status = getattr(src_me.status, "value", str(src_me.status)).lower()
        if src_status in ("left", "kicked", "banned"):
            return (
                "❌ Your <b>bot</b> is not in the <b>Source</b> chat.\n"
                "Add it as Admin there."
            )
    except Exception as e:
        return (
            f"❌ Source check failed (bot): <code>{type(e).__name__}</code>\n"
            "Add your bot as Admin in source."
        )

    try:
        tgt_me = await bot_client.get_chat_member(target_id, "me")
        if tgt_me.status not in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            return (
                "❌ Your <b>bot</b> must be <b>Admin</b> in the <b>Target</b> "
                "(post permission)."
            )
    except Exception as e:
        return (
            f"❌ Target check failed (bot): <code>{type(e).__name__}</code>\n"
            "Add your bot as Admin in target."
        )

    return None


async def check_user_account_permissions(
    user_id: int,
    source_id: int,
    target_id: int,
) -> Optional[str]:
    """User account: member of source, can post to target."""
    if not db.has_active_session(user_id):
        return (
            "❌ <b>User account required</b> for this method.\n"
            "Link with <code>/connect</code>"
        )

    manager = get_user_client_manager()
    uc = await manager.get_client(user_id)
    if not uc or not uc.is_connected:
        return (
            "❌ Your user account session is offline.\n"
            "Use /account → Reconnect or /connect again."
        )

    try:
        src_me = await uc.get_chat_member(source_id, "me")
        src_status = getattr(src_me.status, "value", str(src_me.status)).lower()
        if src_status in ("left", "kicked", "banned"):
            return (
                "❌ Your <b>account</b> is not a member of the <b>Source</b> chat."
            )
    except Exception as e:
        return (
            f"❌ Source check failed (account): <code>{type(e).__name__}</code>\n"
            "Join the source chat with your account."
        )

    try:
        tgt_me = await uc.get_chat_member(target_id, "me")
        tgt_status = getattr(tgt_me.status, "value", str(tgt_me.status)).lower()
        if tgt_status in ("left", "kicked", "banned"):
            return (
                "❌ Your <b>account</b> is not in the <b>Target</b> chat."
            )
        chat = await uc.get_chat(target_id)
        chat_type = getattr(chat.type, "name", str(chat.type)).upper()
        if chat_type == "CHANNEL" and tgt_me.status not in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            return (
                "❌ For a <b>channel</b> target your account must be "
                "<b>Admin</b> (post messages)."
            )
    except Exception as e:
        return (
            f"❌ Target check failed (account): <code>{type(e).__name__}</code>"
        )

    return None


# ---------------------------------------------------------------------------
# Resolve / format
# ---------------------------------------------------------------------------

async def resolve_chat_id(client: Client, text: str) -> Optional[int]:
    """Resolve username, invite link or numeric id to chat_id."""
    text = text.strip()
    if text.lstrip("-").isdigit():
        return int(text)
    try:
        chat = await client.get_chat(text)
        return chat.id
    except Exception:
        return None


def parse_buttons(text: str) -> List[List[dict]]:
    """
    Parse button text into rows.
    Format:
    Button1 - https://t.me/xxx | Button2 - https://t.me/yyy
    Button3 - https://example.com
    """
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        buttons = []
        for part in line.split("|"):
            part = part.strip()
            if " - " in part:
                btn_text, url = part.split(" - ", 1)
                btn_text = btn_text.strip()
                url = url.strip()
                if btn_text and url.startswith(("http://", "https://", "tg://")):
                    buttons.append({"text": btn_text, "url": url})
        if buttons:
            rows.append(buttons)
    return rows


def _format_word_list(items: list) -> str:
    if not items:
        return "None"
    parts = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            pattern = item.get("pattern", "")
            flag = " (regex)" if item.get("is_regex") else ""
            parts.append(f"{pattern}{flag}")
    return ", ".join(parts)


def format_rule(rule: dict) -> str:
    """Pretty print a single forward rule (HTML)."""
    src = rule["source_chat_id"]
    tgt = rule["target_chat_id"]
    status = "✅ ON" if rule.get("enabled", True) else "❌ OFF"
    via = rule.get("forward_via") or "user_bot"
    via_label = "🤖 User Bot" if via == "user_bot" else "👤 User Account"
    caption = rule.get("add_caption") or "None"
    pos = rule.get("caption_position", "end")
    custom = rule.get("custom_caption")
    remove_old = "✅ ON" if rule.get("remove_old_caption") else "❌ OFF"
    remove_links = "✅ ON" if rule.get("remove_links") else "❌ OFF"
    tag = "✅ ON" if rule.get("forward_tag") else "❌ OFF"
    types = ", ".join(rule.get("allowed_types", ["all"]))
    delay = rule.get("delay", 0)
    anti_dupe = "✅ ON" if rule.get("anti_dupe") else "❌ OFF"
    reps = rule.get("replacements", [])
    blocks = rule.get("block_words", [])
    whitelist = rule.get("whitelist_words", [])
    buttons = rule.get("buttons", [])

    from AutoPost.helper.content_type import content_type_label
    from AutoPost.helper.media_size import format_bytes
    content = content_type_label(rule.get("content_type", "all"))
    sz_on = bool(rule.get("size_filter_enabled"))
    min_b = int(rule.get("min_media_size") or 0)
    if sz_on:
        size_line = f"<b>Media Size Filter:</b> ✅ ON · Min <code>{format_bytes(min_b)}</code>\n"
    else:
        saved = f" · saved <code>{format_bytes(min_b)}</code>" if min_b else ""
        size_line = f"<b>Media Size Filter:</b> ❌ OFF{saved}\n"
    cs_on = "✅ ON" if rule.get("completion_sticker_enabled") else "❌ OFF"
    cs_n = len(rule.get("completion_stickers") or [])

    if custom:
        custom_preview = custom if len(custom) <= 80 else custom[:77] + "..."
        custom_line = f"<b>Custom Caption:</b>\n<code>{custom_preview}</code>\n"
        add_line = ""
        pos_line = ""
    else:
        custom_line = "<b>Custom Caption:</b> <code>None</code>\n"
        add_line = f"<b>Add Caption:</b> <code>{caption}</code>\n"
        pos_line = f"<b>Caption Position:</b> <code>{pos}</code>\n"

    return (
        f"<b>Source:</b> <code>{src}</code>\n"
        f"<b>Target:</b> <code>{tgt}</code>\n"
        f"<b>Status:</b> {status}\n"
        f"<b>Forward via:</b> {via_label}\n"
        f"<b>Forward Tag:</b> {tag}\n"
        f"<b>Allowed Types:</b> <code>{types}</code>\n"
        f"<b>Content:</b> {content}\n"
        f"{size_line}"
        f"<b>Completion Sticker:</b> {cs_on} · <code>{cs_n}</code>\n"
        f"{custom_line}"
        f"{add_line}"
        f"{pos_line}"
        f"<b>Remove Old Caption:</b> {remove_old}\n"
        f"<b>Remove Links:</b> {remove_links}\n"
        f"<b>Delay:</b> <code>{delay}</code> seconds\n"
        f"<b>Anti-Duplication:</b> {anti_dupe}\n"
        f"<b>Replacements:</b> <code>{len(reps)}</code> items\n"
        f"<b>Block Words:</b> <code>{len(blocks)}</code> items\n"
        f"<b>Whitelist Words:</b> <code>{len(whitelist)}</code> items\n"
        f"<b>Buttons:</b> <code>{len(buttons)}</code> rows\n"
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def register_user(client: Client, message: Message) -> None:
    if not db.get_user(message.from_user.id):
        db.add_or_update_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )


async def can_manage_rule(
    client: Client,
    message: Message,
    source: int,
    target: int,
    rule: Optional[dict] = None,
) -> bool:
    user_id = message.from_user.id

    if db.is_bot_owner(user_id):
        return True

    if rule is None:
        rule = db.get_forward_rule(source, target)

    if not rule:
        await message.reply_text("❌ Rule not found.", parse_mode=ParseMode.HTML)
        return False

    if rule.get("owner_id") == user_id:
        return True

    source_status = None
    target_status = None
    try:
        source_member = await client.get_chat_member(source, user_id)
        source_status = source_member.status
    except Exception:
        pass
    try:
        target_member = await client.get_chat_member(target, user_id)
        target_status = target_member.status
    except Exception:
        pass

    privileged = {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}
    if (
        source_status == ChatMemberStatus.OWNER and target_status in privileged
    ) or (
        target_status == ChatMemberStatus.OWNER and source_status in privileged
    ):
        return True

    await message.reply_text(
        "❌ <b>Access Denied!</b>\n"
        "You don't have enough permissions to modify or delete this rule.\n"
        "<i>Required: Rule Owner OR Chat Creator with Admin rights "
        "in the secondary chat.</i>",
        parse_mode=ParseMode.HTML,
    )
    return False


async def resolve_and_authorize(
    client: Client,
    message: Message,
    need_manage: bool = True,
) -> Optional[Tuple[int, int, dict]]:
    if len(message.command) < 3:
        await message.reply_text(
            "❌ <b>Usage:</b> command requires "
            "<code>&lt;source&gt; &lt;target&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return None

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])

    if not source or not target:
        await message.reply_text(
            "❌ Invalid source or target chat.",
            parse_mode=ParseMode.HTML,
        )
        return None

    rule = db.get_forward_rule(source, target)
    if not rule:
        await message.reply_text("❌ Rule not found.", parse_mode=ParseMode.HTML)
        return None

    if need_manage:
        if not await can_manage_rule(client, message, source, target, rule):
            return None

    return source, target, rule


async def create_rule_with_via(
    message: Message,
    source: int,
    target: int,
    user_id: int,
    forward_via: str,
) -> None:
    """Create rule and reply with formatted result."""
    try:
        rule = db.create_forward_rule(
            source_chat_id=source,
            target_chat_id=target,
            owner_id=user_id,
            enabled=True,
            forward_tag=False,
            allowed_types=["all"],
            remove_old_caption=False,
            remove_links=False,
            delay=0,
            anti_dupe=False,
            forward_via=forward_via,
        )
        via_label = (
            "🤖 Your Bot" if forward_via == "user_bot" else "👤 Your Account"
        )
        await message.reply_text(
            f"✅ <b>Rule created</b> — forwarding via <b>{via_label}</b>\n\n"
            f"{format_rule(rule)}",
            parse_mode=ParseMode.HTML,
        )
    except ValueError as e:
        await message.reply_text(f"❌ {e}", parse_mode=ParseMode.HTML)
