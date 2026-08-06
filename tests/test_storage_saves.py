import asyncio
import unittest
from copy import deepcopy
from unittest.mock import patch

import storage


class StorageSaveRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._saved_state = {
            "cache": storage._cache,
            "cache_time": storage._cache_time,
            "dirty": storage._dirty,
            "save_locks": storage._save_locks,
            "last_save": storage._last_save,
            "pending_save": storage._pending_save,
            "save_interval": storage._SAVE_INTERVAL,
        }
        storage._cache = {}
        storage._cache_time = {}
        storage._dirty = {}
        storage._save_locks = {}
        storage._last_save = {}
        storage._pending_save = {}
        storage._SAVE_INTERVAL = 0

    async def asyncTearDown(self) -> None:
        storage._cache = self._saved_state["cache"]
        storage._cache_time = self._saved_state["cache_time"]
        storage._dirty = self._saved_state["dirty"]
        storage._save_locks = self._saved_state["save_locks"]
        storage._last_save = self._saved_state["last_save"]
        storage._pending_save = self._saved_state["pending_save"]
        storage._SAVE_INTERVAL = self._saved_state["save_interval"]

    async def test_older_write_cannot_mark_newer_mutation_clean(self) -> None:
        first_write_started = asyncio.Event()
        release_first_write = asyncio.Event()
        writes: list[dict] = []

        async def fake_to_thread(_func, _doc_key, data):
            writes.append(deepcopy(data))
            if len(writes) == 1:
                first_write_started.set()
                await release_first_write.wait()

        doc_key = storage._DOC_STENKA
        storage._set_cached(doc_key, {"nested": {"value": "old"}})

        with patch.object(storage.asyncio, "to_thread", new=fake_to_thread):
            old_save = asyncio.create_task(storage._schedule_save(doc_key))
            await first_write_started.wait()

            storage._set_cached(doc_key, {"nested": {"value": "new"}})
            new_save = asyncio.create_task(storage._schedule_save(doc_key))
            release_first_write.set()
            await asyncio.gather(old_save, new_save)

        self.assertEqual(
            writes,
            [
                {"nested": {"value": "old"}},
                {"nested": {"value": "new"}},
            ],
        )
        self.assertFalse(storage._dirty[doc_key])


class ConfigSaveOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._generation = storage._config_generation
        self._persisted_generation = storage._config_persisted_generation

    def tearDown(self) -> None:
        storage._config_generation = self._generation
        storage._config_persisted_generation = self._persisted_generation

    def test_stale_config_worker_cannot_overwrite_newer_save(self) -> None:
        storage._config_generation = 2
        storage._config_persisted_generation = 0
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection

        with (
            patch.object(storage, "_ensure_db"),
            patch.object(storage, "_connect", return_value=connection) as connect,
            patch.object(storage, "_set_meta_json") as set_meta,
        ):
            storage._write_config_sync({"value": "old"}, 1)
            connect.assert_not_called()

            storage._write_config_sync({"value": "new"}, 2)

        set_meta.assert_called_once_with(
            connection,
            storage._CONFIG_META_KEY,
            {"value": "new"},
        )
        self.assertEqual(storage._config_persisted_generation, 2)


if __name__ == "__main__":
    unittest.main()
