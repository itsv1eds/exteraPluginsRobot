import hashlib
import unittest
from unittest.mock import patch

from bot import callback_tokens


class CallbackTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        callback_tokens._slug_tokens.clear()

    def tearDown(self) -> None:
        callback_tokens._slug_tokens.clear()

    @staticmethod
    def _catalog(long_slug: str) -> dict:
        return {"plugins": [{"slug": long_slug}]}

    def test_long_slug_can_be_decoded_after_process_restart(self) -> None:
        slug = "plugin-" + "очень-длинное-название-" * 3
        token = callback_tokens.encode_slug(slug)
        self.assertNotEqual(token, slug)

        callback_tokens._slug_tokens.clear()
        with (
            patch.object(callback_tokens, "load_plugins", return_value=self._catalog(slug)),
            patch.object(callback_tokens, "load_icons", return_value={"iconpacks": []}),
        ):
            self.assertEqual(callback_tokens.decode_slug(token), slug)

    def test_legacy_ten_character_token_still_decodes(self) -> None:
        slug = "legacy-" + "long-slug-" * 8
        token = "t" + hashlib.sha1(slug.encode("utf-8")).hexdigest()[:10]

        with (
            patch.object(callback_tokens, "load_plugins", return_value=self._catalog(slug)),
            patch.object(callback_tokens, "load_icons", return_value={"iconpacks": []}),
        ):
            self.assertEqual(callback_tokens.decode_slug(token), slug)

    def test_real_token_shaped_slug_has_priority(self) -> None:
        slug = "t0123456789"
        with (
            patch.object(callback_tokens, "load_plugins", return_value=self._catalog(slug)),
            patch.object(callback_tokens, "load_icons", return_value={"iconpacks": []}),
        ):
            self.assertEqual(callback_tokens.decode_slug(slug), slug)


if __name__ == "__main__":
    unittest.main()
