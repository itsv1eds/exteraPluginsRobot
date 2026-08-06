import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.routers import admin_flow, user_flow
from bot.states import AdminFlow, UserFlow


class _State:
    def __init__(self, state_name=UserFlow.entering_admin_comment.state) -> None:
        self.data = {"pending_comment_media": []}
        self.state_name = state_name

    async def get_state(self):
        return self.state_name

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)


class CommentMediaGroupInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        user_flow._comment_media_group_buffers.clear()

    async def asyncTearDown(self) -> None:
        for pending in user_flow._comment_media_group_buffers.values():
            task = pending.get("task")
            if task and not task.done():
                task.cancel()
        user_flow._comment_media_group_buffers.clear()

    async def test_album_updates_are_saved_without_losing_photos(self) -> None:
        state = _State()
        first = SimpleNamespace(
            chat=SimpleNamespace(id=55),
            from_user=SimpleNamespace(id=77),
            media_group_id="album-1",
            caption_html="Album caption",
        )
        second = SimpleNamespace(
            chat=SimpleNamespace(id=55),
            from_user=SimpleNamespace(id=77),
            media_group_id="album-1",
            caption_html="",
        )

        with (
            patch.object(user_flow, "_COMMENT_MEDIA_GROUP_SETTLE_SECONDS", 0.01),
            patch.object(
                user_flow,
                "extract_html_text",
                side_effect=lambda message: message.caption_html,
            ),
            patch.object(user_flow, "get_language", AsyncMock(return_value="ru")),
            patch.object(user_flow, "_render_comment_state", AsyncMock()) as render,
        ):
            await user_flow._queue_comment_media_group(
                first, state, {"type": "photo", "file_id": "photo-1"},
            )
            await user_flow._queue_comment_media_group(
                second, state, {"type": "photo", "file_id": "photo-2"},
            )
            await asyncio.sleep(0.03)

        self.assertEqual(
            state.data["pending_comment_media"],
            [
                {"type": "photo", "file_id": "photo-1"},
                {"type": "photo", "file_id": "photo-2"},
            ],
        )
        self.assertEqual(state.data["pending_comment"], "Album caption")
        render.assert_awaited_once()

    async def test_immediate_submit_flushes_the_whole_pending_album(self) -> None:
        state = _State()
        first = SimpleNamespace(
            chat=SimpleNamespace(id=55),
            from_user=SimpleNamespace(id=77),
            media_group_id="album-fast",
            caption_html="",
        )
        second = SimpleNamespace(
            chat=SimpleNamespace(id=55),
            from_user=SimpleNamespace(id=77),
            media_group_id="album-fast",
            caption_html="",
        )

        with patch.object(
            user_flow,
            "extract_html_text",
            side_effect=lambda message: message.caption_html,
        ):
            await user_flow._queue_comment_media_group(
                first, state, {"type": "photo", "file_id": "photo-1"},
            )
            await user_flow._queue_comment_media_group(
                second, state, {"type": "photo", "file_id": "photo-2"},
            )
            await user_flow._flush_pending_comment_media_groups(first)

        self.assertEqual(
            [item["file_id"] for item in state.data["pending_comment_media"]],
            ["photo-1", "photo-2"],
        )
        self.assertFalse(user_flow._comment_media_group_buffers)

    async def test_reset_discards_pending_album_without_late_write(self) -> None:
        state = _State()
        message = SimpleNamespace(
            chat=SimpleNamespace(id=55),
            from_user=SimpleNamespace(id=77),
            media_group_id="album-reset",
            caption_html="",
        )

        with (
            patch.object(user_flow, "_COMMENT_MEDIA_GROUP_SETTLE_SECONDS", 0.01),
            patch.object(user_flow, "extract_html_text", return_value=""),
        ):
            await user_flow._queue_comment_media_group(
                message, state, {"type": "photo", "file_id": "photo-1"},
            )
            await user_flow._discard_pending_comment_media_groups(message)
            await asyncio.sleep(0.03)

        self.assertEqual(state.data["pending_comment_media"], [])


class RejectMediaGroupInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        admin_flow._reject_media_group_buffers.clear()

    async def asyncTearDown(self) -> None:
        for pending in admin_flow._reject_media_group_buffers.values():
            task = pending.get("task")
            if task and not task.done():
                task.cancel()
        admin_flow._reject_media_group_buffers.clear()

    async def test_admin_rejection_album_keeps_every_photo(self) -> None:
        state = _State(AdminFlow.entering_reject_comment.state)
        state.data = {"reject_media": []}
        first = SimpleNamespace(
            chat=SimpleNamespace(id=55),
            from_user=SimpleNamespace(id=77),
            media_group_id="album-2",
            html_text="",
            caption="Reason",
            answer=AsyncMock(),
        )
        second = SimpleNamespace(
            chat=SimpleNamespace(id=55),
            from_user=SimpleNamespace(id=77),
            media_group_id="album-2",
            html_text="",
            caption="",
            answer=AsyncMock(),
        )

        with (
            patch.object(admin_flow, "_REJECT_MEDIA_GROUP_SETTLE_SECONDS", 0.01),
            patch.object(admin_flow, "telegram_html", side_effect=lambda value: value),
            patch.object(admin_flow, "_tr", return_value="added"),
        ):
            await admin_flow._queue_reject_media_group(
                first, state, {"type": "photo", "file_id": "photo-1"},
            )
            await admin_flow._queue_reject_media_group(
                second, state, {"type": "photo", "file_id": "photo-2"},
            )
            await asyncio.sleep(0.03)

        self.assertEqual(
            state.data["reject_media"],
            [
                {"type": "photo", "file_id": "photo-1"},
                {"type": "photo", "file_id": "photo-2"},
            ],
        )
        self.assertEqual(state.data["reject_comment_draft"], "Reason")
        second.answer.assert_awaited_once_with("added")


if __name__ == "__main__":
    unittest.main()
