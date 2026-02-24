import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

_log_queue: Optional[asyncio.Queue] = None
_log_task: Optional[asyncio.Task] = None


async def _log_worker() -> None:
    while True:
        try:
            log_entry = await _log_queue.get()
            if log_entry is None:
                break
            logger.info(log_entry)
        except asyncio.CancelledError:
            break
        except Exception:
            pass


async def start_log_worker() -> None:
    global _log_queue, _log_task
    _log_queue = asyncio.Queue(maxsize=1000)
    _log_task = asyncio.create_task(_log_worker())


async def stop_log_worker() -> None:
    global _log_task, _log_queue
    if _log_queue:
        await _log_queue.put(None)
    if _log_task:
        try:
            await asyncio.wait_for(_log_task, timeout=2.0)
        except asyncio.TimeoutError:
            _log_task.cancel()


class UserActionLoggingMiddleware(BaseMiddleware):
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
    
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if not self.enabled:
            return await handler(event, data)
        
        log_entry = self._format_log(event, data)
        
        if log_entry and _log_queue:
            try:
                _log_queue.put_nowait(log_entry)
            except asyncio.QueueFull:
                pass
        
        return await handler(event, data)
    
    def _format_log(self, event: Any, data: dict[str, Any]) -> Optional[str]:
        if isinstance(event, CallbackQuery):
            user = event.from_user
            username = f"@{user.username}" if user and user.username else ""
            chat_id = event.message.chat.id if event.message else "?"
            return f"CB uid={user.id if user else '?'} {username} chat={chat_id} data={event.data}"
        
        elif isinstance(event, Message):
            user = event.from_user
            username = f"@{user.username}" if user and user.username else ""
            text = (event.text or event.caption or "")[:50]
            return f"MSG uid={user.id if user else '?'} {username} chat={event.chat.id} text={text}"
        
        return None
