import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import CallbackQuery, ErrorEvent, Message

logger = logging.getLogger(__name__)

_STALE_QUERY_MARKERS = (
    "query is too old",
    "query id is invalid",
    "response timeout expired",
)


def is_stale_query_error(exc: BaseException) -> bool:
    return isinstance(exc, TelegramBadRequest) and any(m in str(exc).lower() for m in _STALE_QUERY_MARKERS)


async def on_transient_error(event: ErrorEvent) -> bool:
    exc = event.exception
    update_id = getattr(event.update, "update_id", None)

    if is_stale_query_error(exc):
        logger.warning("event=callback.stale_query update_id=%s", update_id)
        return True

    if isinstance(exc, (TelegramNetworkError, TelegramServerError, TelegramRetryAfter)):
        text = str(exc)
        logger.warning(
            "event=telegram.transient_error update_id=%s type=%s error=%s",
            update_id,
            type(exc).__name__,
            text[:200],
        )
        return True

    return False

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


class CallbackAckWatchdogMiddleware(BaseMiddleware):
    def __init__(self, delay: float = 1.5):
        self.delay = delay

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        async def _ack_later() -> None:
            try:
                await asyncio.sleep(self.delay)
                await event.answer()
            except Exception:
                pass

        watchdog = asyncio.create_task(_ack_later())
        try:
            return await handler(event, data)
        finally:
            watchdog.cancel()


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
