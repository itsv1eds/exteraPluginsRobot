import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineQueryResultArticle, InlineQueryResultCachedPhoto

from bot.routers import poster_flow
from bot.services import poster
from bot.states import PosterFlow


class _PosterBot:
    def __init__(self, *, fail_text: bool = False) -> None:
        self.fail_text = fail_text
        self.groups = []
        self.deleted = []
        self.messages = []

    async def send_media_group(self, chat_id, media):
        self.groups.append((chat_id, media))
        return [SimpleNamespace(message_id=100 + idx) for idx in range(len(media))]

    async def send_photo(self, chat_id, file_id, **kwargs):
        return SimpleNamespace(message_id=200)

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_text:
            raise RuntimeError("text failed")
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=300)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class PosterDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_photos_are_sent_as_one_media_group(self) -> None:
        bot = _PosterBot()
        content = {
            "html_text": "Album caption",
            "media": [
                {"type": "photo", "file_id": "photo-1"},
                {"type": "photo", "file_id": "photo-2"},
                {"type": "photo", "file_id": "photo-3"},
            ],
            "buttons": [],
        }

        result = await poster.send_content(bot, -100, content)

        self.assertIsInstance(result, poster.SentContent)
        self.assertEqual(result.message_ids, [100, 101, 102])
        self.assertEqual(len(bot.groups), 1)
        self.assertEqual(len(bot.groups[0][1]), 3)
        self.assertEqual(bot.groups[0][1][0].caption, "Album caption")

    async def test_partial_media_send_is_rolled_back_when_text_fails(self) -> None:
        bot = _PosterBot(fail_text=True)
        content = {
            "html_text": "x" * 1500,
            "media": [{"type": "photo", "file_id": "photo-1"}],
            "buttons": [],
        }

        with self.assertRaisesRegex(RuntimeError, "text failed"):
            await poster.send_content(bot, -100, content)

        self.assertEqual(bot.deleted, [(-100, 200)])

    async def test_album_buttons_use_a_tracked_companion_message(self) -> None:
        bot = _PosterBot()
        content = {
            "html_text": "Album caption",
            "media": [
                {"type": "photo", "file_id": "photo-1"},
                {"type": "video", "file_id": "video-1"},
            ],
            "buttons": [[{"text": "Open", "url": "https://example.com"}]],
        }

        result = await poster.send_content(bot, -100, content)

        self.assertEqual(result.message_ids, [100, 101, 300])
        self.assertEqual(len(bot.messages), 1)
        self.assertIsNotNone(bot.messages[0][2]["reply_markup"])

    async def test_incompatible_media_rollback_is_atomic(self) -> None:
        class _FailingMixedBot(_PosterBot):
            async def send_audio(self, chat_id, file_id, **kwargs):
                raise RuntimeError("audio failed")

        bot = _FailingMixedBot()
        content = {
            "html_text": "Mixed media",
            "media": [
                {"type": "photo", "file_id": "photo-1"},
                {"type": "audio", "file_id": "audio-1"},
            ],
            "buttons": [],
        }

        with self.assertRaisesRegex(RuntimeError, "audio failed"):
            await poster.send_content(bot, -100, content)

        self.assertEqual(bot.deleted, [(-100, 200)])

    async def test_userbot_markup_failure_creates_tracked_controls(self) -> None:
        class _MarkupFallbackBot(_PosterBot):
            async def edit_message_reply_markup(self, **kwargs):
                raise RuntimeError("markup edit failed")

        bot = _MarkupFallbackBot()
        content = {
            "html_text": "x" * 1100,
            "media": [{"type": "photo", "file_id": "photo-1"}],
            "buttons": [[{"text": "Open", "url": "https://example.com"}]],
        }

        with (
            patch.object(poster, "_userbot_available", AsyncMock(return_value=True)),
            patch.object(poster, "_try_userbot_edit", AsyncMock(return_value=True)),
            self.assertLogs(poster.logger, level="WARNING"),
        ):
            result = await poster._send_via_premium_userbot(
                bot, -100, content, content["html_text"],
            )

        self.assertIsInstance(result, poster.SentContent)
        self.assertEqual(result.message_ids, [200, 300])
        self.assertIsNotNone(bot.messages[0][2]["reply_markup"])

    async def test_long_text_buttons_are_attached_after_userbot_edit(self) -> None:
        class _LongTextBot(_PosterBot):
            def __init__(self):
                super().__init__()
                self.markup_calls = []

            async def edit_message_reply_markup(self, **kwargs):
                self.markup_calls.append(kwargs)

        bot = _LongTextBot()
        content = {
            "html_text": "x" * 5000,
            "media": [],
            "buttons": [[{"text": "Open", "url": "https://example.com"}]],
        }

        with (
            patch.object(poster, "_userbot_available", AsyncMock(return_value=True)),
            patch.object(poster, "_try_userbot_edit", AsyncMock(return_value=True)),
        ):
            result = await poster._send_via_premium_userbot(
                bot, -100, content, content["html_text"],
            )

        self.assertEqual(result.message_id, 300)
        self.assertNotIn("reply_markup", bot.messages[0][2])
        self.assertEqual(len(bot.markup_calls), 1)
        self.assertIsNotNone(bot.markup_calls[0]["reply_markup"])

    async def test_delivery_persists_every_created_message_id(self) -> None:
        result = poster.SentContent(
            primary=SimpleNamespace(message_id=12),
            messages=[SimpleNamespace(message_id=11), SimpleNamespace(message_id=12)],
        )
        post = {"id": "post-1", "chat_id": -100, "content": {}}

        with (
            patch.object(poster, "_claim_post", return_value=True),
            patch.object(poster, "_send_content_for_delivery", AsyncMock(return_value=result)),
            patch.object(poster, "_update_post") as update,
            patch.object(poster, "_schedule_repeat"),
        ):
            delivered = await poster.deliver_post(_PosterBot(), post)

        self.assertTrue(delivered)
        self.assertEqual(update.call_args.kwargs["sent_message_id"], 12)
        self.assertEqual(update.call_args.kwargs["sent_message_ids"], [11, 12])

    async def test_auto_delete_removes_all_created_messages(self) -> None:
        bot = _PosterBot()
        post = {
            "id": "post-1",
            "chat_id": -100,
            "sent_message_id": 12,
            "sent_message_ids": [11, 12],
        }

        with (
            patch("bot.helpers.blank_and_delete", AsyncMock()) as delete,
            patch.object(poster, "_update_post"),
        ):
            await poster.delete_sent_post(bot, post)

        self.assertEqual(
            [call.args for call in delete.await_args_list],
            [(bot, -100, 11), (bot, -100, 12)],
        )

    async def test_failed_auto_delete_stays_pending_for_retry(self) -> None:
        bot = _PosterBot()
        post = {
            "id": "post-1",
            "chat_id": -100,
            "sent_message_ids": [11, 12],
        }

        with (
            patch(
                "bot.helpers.blank_and_delete",
                AsyncMock(side_effect=[True, False]),
            ),
            patch.object(poster, "_update_post") as update,
        ):
            await poster.delete_sent_post(bot, post)

        self.assertEqual(update.call_args.kwargs["status"], "sent")
        self.assertEqual(update.call_args.kwargs["sent_message_ids"], [12])
        self.assertIn("pending", update.call_args.kwargs["error"])


class PosterMediaGroupInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        poster_flow._media_group_buffers.clear()

    async def asyncTearDown(self) -> None:
        for item in poster_flow._media_group_buffers.values():
            task = item.get("task")
            if task and not task.done():
                task.cancel()
        poster_flow._media_group_buffers.clear()

    async def test_album_updates_are_collected_before_advancing_state(self) -> None:
        state = object()
        first = SimpleNamespace(
            chat=SimpleNamespace(id=55),
            from_user=SimpleNamespace(id=77),
            media_group_id="album-1",
        )
        second = SimpleNamespace(
            chat=SimpleNamespace(id=55),
            from_user=SimpleNamespace(id=77),
            media_group_id="album-1",
        )

        with (
            patch.object(poster_flow, "_MEDIA_GROUP_SETTLE_SECONDS", 0.01),
            patch.object(poster_flow, "_complete_media_selection", AsyncMock()) as complete,
        ):
            await poster_flow._queue_media_group(
                first, state, {"type": "photo", "file_id": "photo-1"},
            )
            await poster_flow._queue_media_group(
                second, state, {"type": "photo", "file_id": "photo-2"},
            )
            await asyncio.sleep(0.03)

        complete.assert_awaited_once()
        self.assertEqual(
            complete.await_args.args[2],
            [
                {"type": "photo", "file_id": "photo-1"},
                {"type": "photo", "file_id": "photo-2"},
            ],
        )

    async def test_late_album_does_not_overwrite_next_fsm_step(self) -> None:
        state = SimpleNamespace(
            get_state=AsyncMock(return_value=PosterFlow.composing_buttons.state),
            update_data=AsyncMock(),
        )
        target = SimpleNamespace()

        await poster_flow._complete_media_selection(
            target,
            state,
            [{"type": "photo", "file_id": "late-photo"}],
        )

        state.update_data.assert_not_awaited()


class PosterInlineFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_cached_file_id_falls_back_to_text(self) -> None:
        query = SimpleNamespace(
            query="post-1",
            from_user=SimpleNamespace(id=77),
            bot=SimpleNamespace(id=123),
            answer=AsyncMock(
                side_effect=[
                    TelegramBadRequest(method=None, message="wrong file identifier"),
                    None,
                ]
            ),
        )
        post = {
            "id": "post-1",
            "content": {
                "html_text": "<b>Hello &amp; goodbye</b>",
                "media": [{"type": "photo", "file_id": "old-file-id"}],
                "buttons": [],
            },
        }

        with (
            patch.object(poster_flow.poster, "get_post", return_value=post),
            patch.object(poster_flow, "get_lang", return_value="ru"),
            self.assertLogs(poster_flow.logger, level="WARNING"),
        ):
            await poster_flow.on_inline_post(query)

        self.assertEqual(query.answer.await_count, 2)
        first_result = query.answer.await_args_list[0].args[0][0]
        fallback_result = query.answer.await_args_list[1].args[0][0]
        self.assertIsInstance(first_result, InlineQueryResultCachedPhoto)
        self.assertIsInstance(fallback_result, InlineQueryResultArticle)
        self.assertEqual(
            fallback_result.input_message_content.message_text,
            "Hello & goodbye",
        )

    async def test_corrupt_media_owner_uses_text_without_crashing(self) -> None:
        query = SimpleNamespace(
            query="post-1",
            from_user=SimpleNamespace(id=77),
            bot=SimpleNamespace(id=123),
            answer=AsyncMock(),
        )
        post = {
            "id": "post-1",
            "content": {
                "html_text": "Safe fallback",
                "media": [
                    {"type": "photo", "file_id": "old-file-id", "bot_id": "broken"},
                ],
                "buttons": [],
            },
        }

        with (
            patch.object(poster_flow.poster, "get_post", return_value=post),
            patch.object(poster_flow, "get_lang", return_value="ru"),
        ):
            await poster_flow.on_inline_post(query)

        result = query.answer.await_args.args[0][0]
        self.assertIsInstance(result, InlineQueryResultArticle)


if __name__ == "__main__":
    unittest.main()
