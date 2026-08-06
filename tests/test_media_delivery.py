import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest

from bot.services import admin_notifications, moderation, publish


class _FailingFileBot:
    async def send_message(self, chat_id, text, **kwargs):
        return SimpleNamespace(message_id=10, message_thread_id=None)

    async def send_document(self, chat_id, document, **kwargs):
        raise RuntimeError("file upload failed")


class AtomicModerationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_forum_card_is_removed_when_request_file_fails(self) -> None:
        bot = _FailingFileBot()
        entry = {"id": "plugin-id", "status": "pending", "payload": {}}

        with (
            patch.object(moderation, "moderation_config", return_value={"chat_id": -100, "topic_id": 7}),
            patch.object(moderation, "update_request_payload", return_value=entry),
            patch.object(moderation, "forum_text_with_votes", return_value="request"),
            patch.object(moderation, "_forum_reply_markup", return_value=None),
            patch.object(moderation.Path, "exists", return_value=True),
            patch.object(moderation, "blank_and_delete", AsyncMock()) as delete,
        ):
            with self.assertRaisesRegex(RuntimeError, "file upload failed"):
                await moderation.send_request_to_forum(bot, entry, "request", "/tmp/request.plugin")

        delete.assert_awaited_once_with(bot, -100, 10)

    async def test_admin_card_is_removed_when_request_file_fails(self) -> None:
        bot = _FailingFileBot()
        entry = {"id": "plugin-id", "payload": {"user_id": 5}}

        with (
            patch.object(admin_notifications.Path, "exists", return_value=True),
            patch.object(admin_notifications, "admin_review_kb", return_value=None),
            patch.object(admin_notifications, "blank_and_delete", AsyncMock()) as delete,
            self.assertLogs(admin_notifications.logger, level="WARNING"),
        ):
            await admin_notifications.send_review_notification(
                bot,
                123,
                entry,
                "request",
                "/tmp/request.plugin",
            )

        delete.assert_awaited_once_with(bot, 123, 10)


class _CaptionOverflowBot:
    def __init__(self) -> None:
        self.document_calls = 0

    async def send_document(self, chat_id, document, **kwargs):
        self.document_calls += 1
        if self.document_calls == 1:
            raise TelegramBadRequest(method=None, message="caption is too long")
        return SimpleNamespace(message_id=20)

    async def send_message(self, chat_id, text, **kwargs):
        raise RuntimeError("description failed")


class AtomicPublishDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bare_file_is_removed_when_overflow_text_fails(self) -> None:
        bot = _CaptionOverflowBot()

        with (
            patch.object(publish.Path, "exists", return_value=True),
            patch.object(publish, "blank_and_delete", AsyncMock()) as delete,
            self.assertLogs(publish.logger, level="ERROR"),
        ):
            with self.assertRaisesRegex(RuntimeError, "description failed"):
                await publish._send_channel_post(
                    bot,
                    -100,
                    "x" * 5000,
                    "/tmp/request.plugin",
                )

        delete.assert_awaited_once_with(bot, -100, 20)


if __name__ == "__main__":
    unittest.main()
