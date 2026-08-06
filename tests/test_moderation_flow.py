import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.keyboards import (
    moderation_inline_vote_url_kb,
    moderation_vote_kb,
    moderation_vote_reason_kb,
    moderation_vote_template_kb,
)
from bot.routers import moderation_flow
from bot.services.moderation import can_accept_vote
from bot.states import UserFlow
import request_store


class _Bot:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=42)


class _RequestCacheMixin:
    def setUp(self) -> None:
        self.previous_cache = request_store._requests_cache
        self.previous_index = request_store._id_index
        request_store._requests_cache = []
        request_store._id_index = {}
        self.save_patcher = patch.object(request_store, "_save_requests_list")
        self.save_patcher.start()

    def tearDown(self) -> None:
        self.save_patcher.stop()
        request_store._requests_cache = self.previous_cache
        request_store._id_index = self.previous_index

    def register_request(self, request_id: str) -> dict:
        entry = {"id": request_id, "status": "pending", "payload": {}}
        request_store._requests_cache.append(entry)
        request_store._id_index[request_id] = entry
        return entry


class ModerationLinkTests(_RequestCacheMixin, unittest.TestCase):
    def test_inline_vote_link_uses_valid_start_payload(self) -> None:
        self.register_request("plugin-id+1")
        keyboard = moderation_inline_vote_url_kb("exteraPluginsRobot", "plugin-id+1")
        urls = [str(button.url) for row in keyboard.inline_keyboard for button in row]

        self.assertEqual(len(urls), 2)
        for url in urls:
            payload = url.split("start=", 1)[1]
            self.assertLessEqual(len(payload), 64)
            self.assertRegex(payload, r"^[A-Za-z0-9_-]+$")

    def test_completed_request_cannot_receive_late_vote(self) -> None:
        self.assertTrue(can_accept_vote({"status": "pending"}))
        self.assertFalse(can_accept_vote({"status": "published"}))
        self.assertFalse(can_accept_vote({"status": "rejected"}))

    def test_vote_callbacks_fit_telegram_limit_for_long_request_id(self) -> None:
        request_id = "очень-длинный-id-заявки-" * 8
        self.register_request(request_id)
        keyboards = (
            moderation_vote_kb(request_id),
            moderation_vote_reason_kb(
                request_id,
                9_223_372_036_854_775_807,
                has_templates=True,
                allow_no_reason=True,
            ),
            moderation_vote_template_kb(
                request_id,
                9_223_372_036_854_775_807,
                ["one", "two"],
            ),
        )

        for keyboard in keyboards:
            for row in keyboard.inline_keyboard:
                for button in row:
                    self.assertLessEqual(len((button.callback_data or "").encode("utf-8")), 64)

    def test_pending_vote_ttl_survives_process_restart(self) -> None:
        now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)

        self.assertTrue(
            moderation_flow._pending_vote_is_active(
                {"started_at": (now - timedelta(seconds=30)).isoformat()},
                now,
            )
        )
        self.assertFalse(
            moderation_flow._pending_vote_is_active(
                {"started_at": (now - timedelta(minutes=5)).isoformat()},
                now,
            )
        )


class VoteStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_unrelated_fsm_state_is_preserved(self) -> None:
        state = AsyncMock()
        state.get_state.return_value = "AdminFlow:editing_config"

        await moderation_flow._leave_vote_reason_state(state)

        state.set_state.assert_not_awaited()

    async def test_vote_reason_state_is_reset(self) -> None:
        state = AsyncMock()
        state.get_state.return_value = UserFlow.entering_moderation_vote_reason.state

        await moderation_flow._leave_vote_reason_state(state)

        state.set_state.assert_awaited_once_with(UserFlow.idle)


class VotePromptTests(_RequestCacheMixin, unittest.IsolatedAsyncioTestCase):
    async def test_shared_prompt_creates_pending_vote_and_schedules_expiry(self) -> None:
        bot = _Bot()
        entry = {"id": "plugin-id", "status": "pending", "payload": {}}
        self.register_request("plugin-id")

        with (
            patch.object(moderation_flow, "get_pending_vote", return_value=None),
            patch.object(moderation_flow, "start_pending_vote", return_value=entry) as start,
            patch.object(moderation_flow, "update_pending_vote", return_value=entry) as update,
            patch.object(moderation_flow, "_schedule_prompt_expiry") as schedule,
            patch.object(moderation_flow, "_vote_templates", return_value=[]),
            patch.object(moderation_flow, "require_vote_reason", return_value=True),
        ):
            result = await moderation_flow.start_vote_prompt(
                bot,
                "plugin-id",
                123,
                "moderator",
                "Moderator",
                "yes",
                "en",
                123,
            )

        self.assertTrue(result)
        start.assert_called_once_with("plugin-id", 123, "moderator", "Moderator", "yes")
        update.assert_called_once_with(
            "plugin-id", 123, prompt_chat_id=123, prompt_message_id=42,
        )
        schedule.assert_called_once_with(bot, "plugin-id", 123)
        self.assertEqual(len(bot.sent), 1)

    async def test_failed_prompt_does_not_leave_pending_vote(self) -> None:
        bot = _Bot(fail=True)
        entry = {"id": "plugin-id", "status": "pending", "payload": {}}
        self.register_request("plugin-id")

        with self.assertLogs(moderation_flow.logger, level="ERROR"):
            with (
                patch.object(moderation_flow, "get_pending_vote", return_value=None),
                patch.object(moderation_flow, "start_pending_vote", return_value=entry),
                patch.object(moderation_flow, "clear_pending_vote") as clear,
                patch.object(moderation_flow, "_vote_templates", return_value=[]),
                patch.object(moderation_flow, "require_vote_reason", return_value=True),
            ):
                result = await moderation_flow.start_vote_prompt(
                    bot,
                    "plugin-id",
                    123,
                    "moderator",
                    "Moderator",
                    "no",
                    "en",
                    123,
                )

        self.assertFalse(result)
        clear.assert_called_once_with("plugin-id", 123)

    async def test_restarted_vote_displays_retained_anonymity(self) -> None:
        bot = _Bot()
        entry = {"id": "plugin-id", "status": "pending", "payload": {}}
        self.register_request("plugin-id")
        previous = {"anonymous": True, "prompt_chat_id": 0, "prompt_message_id": 0}
        stored = {"anonymous": True}
        keyboard = object()

        with (
            patch.object(moderation_flow, "get_pending_vote", side_effect=[previous, stored]),
            patch.object(moderation_flow, "start_pending_vote", return_value=entry),
            patch.object(moderation_flow, "update_pending_vote", return_value=entry),
            patch.object(moderation_flow, "_schedule_prompt_expiry"),
            patch.object(moderation_flow, "_vote_templates", return_value=[]),
            patch.object(moderation_flow, "require_vote_reason", return_value=True),
            patch.object(moderation_flow, "moderation_vote_reason_kb", return_value=keyboard) as build_keyboard,
        ):
            result = await moderation_flow.start_vote_prompt(
                bot,
                "plugin-id",
                123,
                "moderator",
                "Moderator",
                "yes",
                "en",
                123,
            )

        self.assertTrue(result)
        self.assertTrue(build_keyboard.call_args.kwargs["anonymous"])
        self.assertIs(bot.sent[0][2]["reply_markup"], keyboard)


if __name__ == "__main__":
    unittest.main()
