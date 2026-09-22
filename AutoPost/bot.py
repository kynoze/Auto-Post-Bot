import importlib
import logging

from pyrogram import Client, enums

from AutoPost import API_ID, API_HASH, BOT_TOKEN
from AutoPost.helper.user_clients import get_user_client_manager
from AutoPost.helper.user_bots import get_user_bot_manager
from AutoPost.plugins import all_modules

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger("Auto-Forward-Bot")


class AutoForwardBot(Client):
    def __init__(self):
        super().__init__(
            name="AutoForwardBot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            # plugins= removed — manual load (Option 2)
            workers=25,
            sleep_threshold=10,
            max_concurrent_transmissions=20,
        )
        self.LOGGER = LOGGER

    async def start(self, *args, **kwargs):
        await super().start(*args, **kwargs)

        me = await self.get_me()
        self.username = me.username
        self.mention = me.mention
        self.user_id = me.id

        self.set_parse_mode(enums.ParseMode.HTML)

        self.LOGGER.info("=" * 50)
        self.LOGGER.info(f"Bot Started Successfully as @{me.username}")
        self.LOGGER.info(f"Bot ID: {me.id}")
        self.LOGGER.info("=" * 50)

        # Manual plugin import (nested OK). One failure does not block others.
        ok, failed = self._load_plugins()
        self.LOGGER.info(
            "Plugins: %d ok | %d failed | %d total → %s",
            ok,
            failed,
            len(all_modules),
            sorted(all_modules),
        )

        try:
            bot_manager = get_user_bot_manager()
            await bot_manager.startup_all()
        except Exception as e:
            self.LOGGER.error(f"Failed to start user bots: {e}")

        try:
            session_manager = get_user_client_manager()
            await session_manager.startup_all()
        except Exception as e:
            self.LOGGER.error(f"Failed to start user sessions: {e}")

    def _load_plugins(self) -> tuple[int, int]:
        ok = failed = 0
        for module in sorted(all_modules):
            name = f"AutoPost.plugins.{module}"
            try:
                importlib.import_module(name)
                ok += 1
                self.LOGGER.info("  ✓ %s", module)
            except Exception as e:
                failed += 1
                self.LOGGER.error(
                    "  ✖ %s: %s", module, e, exc_info=True
                )
        return ok, failed

    async def stop(self, *args, **kwargs):
        try:
            await get_user_bot_manager().stop_all()
        except Exception as e:
            self.LOGGER.warning(f"Error stopping user bots: {e}")

        try:
            await get_user_client_manager().stop_all()
        except Exception as e:
            self.LOGGER.warning(f"Error stopping user sessions: {e}")

        await super().stop(*args, **kwargs)
        self.LOGGER.info("Bot stopped. Bye!")


# Singleton — plugins must use: from AutoPost.bot import app
app = AutoForwardBot()
