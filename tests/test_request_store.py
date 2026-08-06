import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import request_store


class RequestLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_cache = request_store._requests_cache
        self.previous_index = request_store._id_index
        self.previous_route_index = request_store._route_token_index
        self.previous_route_tokens_ready = request_store._route_tokens_ready
        self.previous_route_tokens_source_id = request_store._route_tokens_source_id
        self.save_patcher = patch.object(request_store, "_save_requests_list")
        self.save_patcher.start()

    def tearDown(self) -> None:
        self.save_patcher.stop()
        request_store._requests_cache = self.previous_cache
        request_store._id_index = self.previous_index
        request_store._route_token_index = self.previous_route_index
        request_store._route_tokens_ready = self.previous_route_tokens_ready
        request_store._route_tokens_source_id = self.previous_route_tokens_source_id

    def _set_requests(self, entries: list[dict]) -> None:
        request_store._requests_cache = entries
        request_store._id_index = {entry["id"]: entry for entry in entries}
        request_store._route_token_index = {}
        request_store._route_tokens_ready = False
        request_store._route_tokens_source_id = None

    def test_plugin_lookup_prefers_newest_matching_request(self) -> None:
        old = {
            "id": "plugin-id",
            "status": "published",
            "payload": {"plugin": {"id": "plugin-id"}},
        }
        pending = {
            "id": "plugin-id+1",
            "status": "pending",
            "payload": {"old_plugin": {"slug": "plugin-id"}},
        }
        self._set_requests([old, pending])

        self.assertIs(request_store.get_request_by_plugin_id("plugin-id"), pending)

    def test_plugin_lookup_filters_status_before_choosing_newest(self) -> None:
        scheduled = {
            "id": "plugin-id",
            "status": "scheduled",
            "payload": {"plugin": {"id": "plugin-id"}},
        }
        draft = {
            "id": "plugin-id+1",
            "status": "draft",
            "payload": {"old_plugin": {"slug": "plugin-id"}},
        }
        self._set_requests([scheduled, draft])

        found = request_store.get_request_by_plugin_id(
            "plugin-id",
            statuses={"pending", "error", "scheduled"},
        )

        self.assertIs(found, scheduled)

    def test_deeplink_token_round_trips_unsafe_request_id(self) -> None:
        entry = {"id": "plugin-id+1", "payload": {}}
        self._set_requests([entry])

        token = request_store.request_deeplink_token(entry["id"])

        self.assertRegex(token, r"^[A-Za-z0-9_-]+$")
        self.assertNotIn("+", token)
        self.assertIs(request_store.get_request_by_deeplink_token(token), entry)

    def test_legacy_direct_deeplink_id_still_resolves(self) -> None:
        entry = {"id": "plugin-id", "payload": {}}
        self._set_requests([entry])

        self.assertIs(request_store.get_request_by_deeplink_token("plugin-id"), entry)

    def test_compact_callback_token_resolves_long_request_id(self) -> None:
        entry = {"id": "плагин-" * 20, "payload": {}}
        self._set_requests([entry])

        token = request_store.request_callback_token(entry["id"])

        self.assertLessEqual(len(token.encode("utf-8")), 24)
        self.assertIs(request_store.get_request_by_callback_token(token), entry)

    def test_persisted_token_takes_priority_over_conflicting_legacy_id(self) -> None:
        token = "q" + "1" * 20
        original = {"id": "original-request", "route_token": token, "payload": {}}
        conflicting = {"id": token, "payload": {}}
        self._set_requests([original, conflicting])

        self.assertIs(request_store.get_request_by_callback_token(token), original)
        self.assertIs(request_store.get_request_by_deeplink_token(token), original)

    def test_new_request_id_cannot_reuse_reserved_route_token(self) -> None:
        token = "q" + "2" * 20
        existing = {"id": "original-request", "route_token": token, "payload": {}}
        self._set_requests([existing])

        created = request_store.add_request({"plugin": {"id": token}})

        self.assertEqual(created["id"], f"{token}+1")
        self.assertNotEqual(created["route_token"], token)

    def test_route_token_index_is_built_once_for_repeated_lookups(self) -> None:
        entries = [
            {"id": f"request-{idx}", "payload": {}}
            for idx in range(200)
        ]
        self._set_requests(entries)

        with patch.object(
            request_store,
            "_route_token_candidate",
            wraps=request_store._route_token_candidate,
        ) as candidate:
            token = request_store.request_callback_token("request-199")
            calls_after_migration = candidate.call_count

            self.assertIs(request_store.get_request_by_callback_token(token), entries[-1])
            self.assertIs(request_store.get_request_by_deeplink_token(token), entries[-1])

        self.assertEqual(calls_after_migration, len(entries))
        self.assertEqual(candidate.call_count, calls_after_migration)


class RequestTimestampTests(unittest.TestCase):
    def test_timezone_offset_is_converted_to_utc(self) -> None:
        parsed = request_store._parse_datetime_utc("2026-08-06T12:00:00+03:00")

        self.assertEqual(parsed, datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc))

    def test_naive_legacy_timestamp_is_treated_as_utc(self) -> None:
        parsed = request_store._parse_datetime_utc("2026-08-06T12:00:00")

        self.assertEqual(parsed, datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc))

    def test_schedule_due_check_respects_timezone_offset(self) -> None:
        now = datetime(2026, 8, 6, 9, 30, tzinfo=timezone.utc)

        self.assertTrue(
            request_store._scheduled_time_is_due(
                "2026-08-06T12:00:00+03:00",
                now,
            )
        )
        self.assertFalse(
            request_store._scheduled_time_is_due(
                "2026-08-06T13:00:00+03:00",
                now,
            )
        )


if __name__ == "__main__":
    unittest.main()
