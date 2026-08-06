import asyncio
import unittest
from copy import deepcopy
from unittest.mock import patch

import user_store


class UserStoreSaveRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_state = {
            "users_cache": user_store._users_cache,
            "dirty": user_store._dirty,
            "last_save": user_store._last_save,
            "pending_save": user_store._pending_save,
            "save_interval": user_store._SAVE_INTERVAL,
            "save_lock": user_store._save_lock,
        }
        user_store._users_cache = {"1": {"language": "ru"}}
        user_store._dirty = False
        user_store._last_save = 0
        user_store._pending_save = False
        user_store._SAVE_INTERVAL = 0
        user_store._save_lock = asyncio.Lock()

    async def asyncTearDown(self) -> None:
        user_store._users_cache = self._saved_state["users_cache"]
        user_store._dirty = self._saved_state["dirty"]
        user_store._last_save = self._saved_state["last_save"]
        user_store._pending_save = self._saved_state["pending_save"]
        user_store._SAVE_INTERVAL = self._saved_state["save_interval"]
        user_store._save_lock = self._saved_state["save_lock"]

    async def test_user_writes_are_serialized_and_latest_value_wins(self) -> None:
        first_write_started = asyncio.Event()
        release_first_write = asyncio.Event()
        writes: list[dict] = []

        async def fake_to_thread(_func, data):
            writes.append(deepcopy(data))
            if len(writes) == 1:
                first_write_started.set()
                await release_first_write.wait()

        with patch.object(user_store.asyncio, "to_thread", new=fake_to_thread):
            old_save = asyncio.create_task(user_store._schedule_save())
            await first_write_started.wait()

            user_store._users_cache["1"]["language"] = "en"
            new_save = asyncio.create_task(user_store._schedule_save())
            release_first_write.set()
            await asyncio.gather(old_save, new_save)

        self.assertEqual(
            writes,
            [
                {"users": {"1": {"language": "ru"}}},
                {"users": {"1": {"language": "en"}}},
            ],
        )
        self.assertFalse(user_store._dirty)


if __name__ == "__main__":
    unittest.main()
