"""Native Windows DPAPI smoke test: real token encryption without Telegram traffic.

The regular Telegram tests replace the DPAPI boundary for portability; this
suite verifies that the installed account can decrypt what Windows encrypted.
No credentials from a real bot are used and no HTTP calls are made.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from codex_auto_resume import telegram


@unittest.skipUnless(os.name == "nt", "Windows DPAPI is only available on Windows")
class NativeDpapiTests(unittest.TestCase):
    def test_current_user_roundtrip_and_no_plaintext(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = SimpleNamespace(state_dir=Path(temp))
            synthetic = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
            before = telegram.status(paths)
            self.assertFalse(before["configured"])
            self.assertFalse(before["enabled"])

            saved = telegram.configure(paths, {
                "enabled": True, "token": synthetic, "chat_id": "123456789"})
            self.assertEqual(saved, {
                "enabled": True, "configured": True, "chat_id": "123456789",
                "credentials_ready": True})
            written = (Path(temp) / "telegram.json").read_text(encoding="utf-8")
            self.assertNotIn(synthetic, written)
            self.assertNotIn("token\": \"123456789:", written)
            data = json.loads(written)
            ciphertext = base64.b64decode(data["encrypted_token"], validate=True)
            self.assertNotEqual(ciphertext, synthetic.encode("ascii"))
            self.assertEqual(telegram._dpapi(ciphertext, decrypt=True).decode("ascii"), synthetic)
            self.assertEqual(telegram._token(data), synthetic)

            # Rotating the password field to empty must not destroy a stored token.
            telegram.configure(paths, {"enabled": True, "token": "", "chat_id": "987654321"})
            self.assertEqual(telegram._token(telegram._read(paths)), synthetic)

            telegram.configure(paths, {"enabled": False})
            self.assertFalse(telegram.status(paths)["enabled"])
            self.assertFalse(telegram.mirror(paths, "needs_you", object()))


if __name__ == "__main__":
    unittest.main()
