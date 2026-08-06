import unittest
from unittest.mock import AsyncMock, patch

from aiogram.types import ChatPermissions

from bot.routers import joinly_flow


class WelcomeTemplateTests(unittest.TestCase):
    def test_known_placeholders_flags_and_button_are_valid(self) -> None:
        template = (
            "Привет, {fullname}! {{обычные скобки}} {nonotif}\n"
            "[Чат](buttonurl://https://t.me/exteraForum)"
        )
        self.assertTrue(joinly_flow._is_valid_welcome_template(template))

    def test_unknown_or_unsafe_format_fields_are_rejected(self) -> None:
        self.assertFalse(joinly_flow._is_valid_welcome_template("Привет, {unknown}"))
        self.assertFalse(joinly_flow._is_valid_welcome_template("{first.__class__}"))
        self.assertFalse(joinly_flow._is_valid_welcome_template("Привет, {first"))

    def test_invalid_button_url_is_rejected(self) -> None:
        self.assertFalse(
            joinly_flow._is_valid_welcome_template(
                "[Открыть](buttonurl://javascript:alert(1))"
            )
        )

    def test_doubled_flag_name_is_literal_not_a_flag(self) -> None:
        text, flags = joinly_flow._extract_flags("{{preview}} {preview}")
        self.assertEqual(text, "{{preview}}")
        self.assertTrue(flags["preview"])


class PostGuardUnlockTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_unlock_restores_saved_restrictions(self) -> None:
        settings = {
            "PostOriginalPermissions": {
                "can_send_messages": False,
                "can_send_photos": True,
            },
            "PostLockedPermissions": ["can_send_messages", "can_send_photos"],
            "PostLockUntil": 123,
        }
        saved_settings: list[tuple[int, str, object]] = []
        current = ChatPermissions(
            can_send_messages=False,
            can_send_photos=False,
            can_send_polls=False,
        )

        def get_setting(_chat_id, key):
            return settings.get(key)

        def set_setting(chat_id, key, value):
            settings[key] = value
            saved_settings.append((chat_id, key, value))

        set_permissions = AsyncMock()
        with (
            patch.object(joinly_flow, "_get_setting", side_effect=get_setting),
            patch.object(joinly_flow, "_set_setting", side_effect=set_setting),
            patch.object(joinly_flow, "_get_chat_permissions", new=AsyncMock(return_value=current)),
            patch.object(joinly_flow, "_set_chat_permissions", new=set_permissions),
        ):
            await joinly_flow._unlock_chat_now(object(), -100123)

        permissions = set_permissions.await_args.args[2]
        self.assertIs(permissions.can_send_messages, False)
        self.assertIs(permissions.can_send_photos, True)
        self.assertIs(permissions.can_send_polls, False)
        self.assertEqual(settings["PostLockUntil"], 0)
        self.assertEqual(settings["PostOriginalPermissions"], {})
        self.assertEqual(settings["PostLockedPermissions"], [])
        self.assertTrue(saved_settings)

    async def test_manual_unlock_without_active_lock_does_not_open_chat(self) -> None:
        settings = {
            "PostOriginalPermissions": {},
            "PostLockedPermissions": [],
            "PostLockUntil": 0,
        }

        with (
            patch.object(joinly_flow, "_get_setting", side_effect=lambda _chat_id, key: settings.get(key)),
            patch.object(joinly_flow, "_set_setting", side_effect=lambda _chat_id, key, value: settings.__setitem__(key, value)),
            patch.object(joinly_flow, "_get_chat_permissions", new=AsyncMock()) as get_permissions,
            patch.object(joinly_flow, "_set_chat_permissions", new=AsyncMock()) as set_permissions,
        ):
            await joinly_flow._unlock_chat_now(object(), -100123)

        get_permissions.assert_not_awaited()
        set_permissions.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
