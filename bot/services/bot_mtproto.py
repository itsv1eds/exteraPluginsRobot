import asyncio
import logging
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import AuthKeyDuplicatedError

from bot.cache import get_config

logger = logging.getLogger(__name__)
_client = None
_lock = asyncio.Lock()


async def get_file_client():
    global _client
    async with _lock:
        cfg = get_config()
        credentials = cfg.get("bot_mtproto") or cfg.get("userbot") or {}
        if not credentials.get("api_id") or not credentials.get("api_hash"):
            raise ValueError("large_file_transfer_unavailable")
        token = cfg.get("bot_token") or ""
        bot_id = int(token.split(":", 1)[0])
        if _client is None:
            from storage import DATA_DIR
            directory = Path(str(credentials.get("bot_session_dir") or DATA_DIR / "bot_sessions"))
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            _client = TelegramClient(
                str(directory / f"file_bot_{bot_id}"), int(credentials["api_id"]),
                str(credentials["api_hash"]), receive_updates=False,
            )
            os.chmod(directory / f"file_bot_{bot_id}.session", 0o600)
        try:
            if not _client.is_connected():
                await asyncio.wait_for(_client.connect(), timeout=30)
            me = await asyncio.wait_for(_client.get_me(), timeout=30)
            if me is None:
                await asyncio.wait_for(_client.sign_in(bot_token=token), timeout=30)
                me = await _client.get_me()
            if not me or not me.bot or me.id != bot_id:
                raise ValueError("large_file_transfer_unavailable")
        except AuthKeyDuplicatedError:
            logger.error("event=bot_file_session.revoked create_a_separate_session_on_each_host")
            await _client.disconnect()
            _client = None
            raise ValueError("large_file_transfer_unavailable") from None
        return _client


async def stop_file_client():
    global _client
    async with _lock:
        if _client:
            await _client.disconnect()
            _client = None
