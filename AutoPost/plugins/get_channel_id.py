from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import Message
from AutoPost.bot import app

@app.on_message(filters.command("id"))
async def id_command(client: Client, message: Message):
    """
    /id command - works in private, group and channel
    """
    chat = message.chat

    if chat.type == ChatType.PRIVATE:
        text = (
            f"<b>Your User ID:</b> <code>{message.from_user.id}</code>\n"
            f"<b>Chat ID:</b> <code>{chat.id}</code>"
        )
    else:
        text = (
            f"<b>Chat Title:</b> {chat.title or 'N/A'}\n"
            f"<b>Chat ID:</b> <code>{chat.id}</code>\n"
            f"<b>Type:</b> {chat.type.name}"
        )

    await message.reply_text(text, parse_mode=ParseMode.HTML)


@app.on_message(filters.private & filters.forwarded)
async def forwarded_id_handler(client: Client, message: Message):
    """
    Jab koi user bot ko message forward kare (forward tag ke sath),
    to bot original chat/user ki ID bata de.
    Compatible with Kurigram 2.2.24
    """

    # 1. Forwarded from Channel / Group / Supergroup
    if message.forward_from_chat:
        chat = message.forward_from_chat
        text = (
            f"<b>Forwarded from Channel/Group</b>\n\n"
            f"<b>Title:</b> {chat.title or 'N/A'}\n"
            f"<b>Chat ID:</b> <code>{chat.id}</code>\n"
            f"<b>Type:</b> {chat.type.name if chat.type else 'N/A'}"
        )

    # 2. Forwarded from a normal User
    elif message.forward_from:
        user = message.forward_from
        name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        username = f"@{user.username}" if user.username else "None"

        text = (
            f"<b>Forwarded from User</b>\n\n"
            f"<b>Name:</b> {name}\n"
            f"<b>User ID:</b> <code>{user.id}</code>\n"
            f"<b>Username:</b> {username}"
        )

    # 3. Forwarded from hidden user (Privacy enabled)
    elif message.forward_sender_name:
        text = (
            f"<b>Forwarded from Hidden User</b>\n\n"
            f"<b>Name:</b> {message.forward_sender_name}\n"
            f"<i>User ID is hidden due to privacy settings.</i>"
        )

    else:
        return

    await message.reply_text(text, parse_mode=ParseMode.HTML)
