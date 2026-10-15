"""Regression tests for the optional Telegram notification channel.

Uses unittest, just like the repository CI suite. Network and Windows DPAPI are
stubbed: no real token or Telegram API request is made during tests.
"""
from __future__ import annotations

import json
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from codex_auto_resume import telegram


TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.paths = SimpleNamespace(state_dir=Path(self.directory.name))
        dpapi = mock.patch.object(telegram, "_dpapi", side_effect=lambda data, decrypt=False: data[::-1])
        dpapi.start()
        self.addCleanup(dpapi.stop)

    def configure(self, *, enabled=True, chat="12345"):
        return telegram.configure(self.paths, {"enabled": enabled, "token": TOKEN, "chat_id": chat})

    def test_disabled_by_default(self):
        self.assertEqual(telegram.status(self.paths),
                         {"enabled": False, "configured": False, "chat_id": ""})
        self.assertFalse(telegram.mirror(self.paths, "needs_you", SimpleNamespace(key="uuid")))
        self.assertFalse((self.paths.state_dir / "telegram.json").exists())

    def test_secret_is_encrypted_and_not_returned(self):
        self.assertEqual(self.configure(chat="-1001234567890"),
                         {"enabled": True, "configured": True, "chat_id": "-1001234567890"})
        body = (self.paths.state_dir / "telegram.json").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, body)
        self.assertIn("encrypted_token", json.loads(body))
        self.assertEqual(telegram._token(telegram._read(self.paths)), TOKEN)
        self.assertNotIn("token", telegram.status(self.paths))

    def test_empty_password_preserves_existing_token(self):
        self.configure()
        telegram.configure(self.paths, {"enabled": True, "token": "", "chat_id": "56789"})
        self.assertEqual(telegram._token(telegram._read(self.paths)), TOKEN)
        self.assertEqual(telegram.status(self.paths)["chat_id"], "56789")

    def test_disabling_keeps_token_but_prevents_delivery(self):
        self.configure()
        telegram.configure(self.paths, {"enabled": False})
        self.assertFalse(telegram.status(self.paths)["enabled"])
        self.assertFalse(telegram.mirror(self.paths, "needs_you", SimpleNamespace(key="uuid")))

    def test_invalid_inputs_do_not_write_file(self):
        for bad in ({"token": None}, {"token": "invalid"}, {"enabled": True, "chat_id": "abc"},
                    {"chat_id": 1234}, {"enabled": "true"}):
            with self.subTest(bad=bad):
                with self.assertRaises(telegram.TelegramError):
                    telegram.configure(self.paths, bad)
        self.assertFalse((self.paths.state_dir / "telegram.json").exists())

    def test_http_body_excludes_task_title_and_has_timeout(self):
        self.configure()
        sent = {}

        class Reply:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _): return b'{"ok":true}'

        def open_request(request, timeout):
            sent["data"], sent["timeout"] = request.data, timeout
            return Reply()

        msg = telegram._message("needs_you", SimpleNamespace(key="uuid-123", title="PRIVATE"),
                                {"prompt": "PRIVATE PROMPT"})
        telegram._send(self.paths, msg, opener=open_request)
        self.assertNotIn(b"PRIVATE", sent["data"])
        self.assertIn(b"uuid-123", sent["data"])
        self.assertEqual(sent["timeout"], telegram.SEND_TIMEOUT)

    def test_errors_never_include_token(self):
        self.configure()
        def fail(_request, timeout):
            raise OSError("secret: " + TOKEN)
        with self.assertRaises(telegram.TelegramError) as raised:
            telegram._send(self.paths, "test", opener=fail)
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_reset_time_without_private_title(self):
        msg = telegram._message("interruption", SimpleNamespace(key="uuid", title="PRIVATE"),
                                {"reset_at": 1792141200})
        self.assertIn("Ожидаемое снятие лимита", msg)
        self.assertIn("uuid", msg)
        self.assertNotIn("PRIVATE", msg)

    def test_result_kinds(self):
        for kind, fragment in (("resumed", "подтверждено"),
                               ("failed", "Ошибка"),
                               ("unknown", "неизвестен")):
            with self.subTest(kind=kind):
                self.assertIn(fragment, telegram._message("result", SimpleNamespace(kind=kind)))

    def test_queue_delivers_once_and_respects_disable(self):
        self.configure()
        local_queue = queue.Queue(maxsize=4)
        sent = []
        with mock.patch.object(telegram, "_QUEUE", local_queue), \
             mock.patch.object(telegram, "_QUEUE_THREAD", None), \
             mock.patch.object(telegram, "_send", side_effect=lambda _paths, text: sent.append(text)):
            self.assertTrue(telegram.mirror(self.paths, "needs_you", SimpleNamespace(key="uuid")))
            local_queue.join()
            self.assertEqual(len(sent), 1)
            self.assertIn("требуется", sent[0])
        telegram.configure(self.paths, {"enabled": False})
        self.assertFalse(telegram.mirror(self.paths, "needs_you", SimpleNamespace(key="uuid")))

    def test_control_bridge_does_not_return_token(self):
        from codex_auto_resume import controlcli
        self.configure()
        control = SimpleNamespace(paths=self.paths)
        status = controlcli.dispatch(control, "telegram-status", {})
        self.assertTrue(status["ok"])
        self.assertNotIn(TOKEN, json.dumps(status))
        with mock.patch.object(telegram, "test", return_value={"sent": True}):
            result = controlcli.dispatch(control, "telegram-test", {})
        self.assertEqual(result, {"ok": True, "result": {"sent": True}})

    def test_bridge_refuses_invalid_token_without_echoing_it(self):
        from codex_auto_resume import controlcli
        control = SimpleNamespace(paths=self.paths)
        payload = {"enabled": True, "chat_id": "1234", "token": "BADTOKEN"}
        answer = controlcli.dispatch(control, "telegram-update", payload)
        self.assertFalse(answer["ok"])
        self.assertNotIn("BADTOKEN", json.dumps(answer))

    def test_symlink_is_ignored(self):
        external = self.paths.state_dir / "other.json"
        external.write_text('{"enabled":true,"encrypted_token":"abc"}', encoding="utf-8")
        directory = self.paths.state_dir / "config"
        directory.mkdir()
        target = directory / "telegram.json"
        try:
            target.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        self.assertFalse(telegram.status(SimpleNamespace(state_dir=directory))["enabled"])


if __name__ == "__main__":
    unittest.main()
