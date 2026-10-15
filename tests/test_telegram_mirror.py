"""Regression coverage for the optional Telegram notification channel.

Linux runs stub the DPAPI boundary and HTTP endpoint. Windows integration is
covered separately by the installed-app smoke test.
"""
from __future__ import annotations

import json
import queue
from pathlib import Path
from types import SimpleNamespace
import pytest


def _paths(tmp_path):
    return SimpleNamespace(state_dir=tmp_path)


def _token():
    return "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"


def test_defaults_are_disabled_and_no_secrets(tmp_path):
    from codex_auto_resume import telegram
    paths = _paths(tmp_path)
    assert telegram.status(paths) == {"enabled": False, "configured": False, "chat_id": ""}
    assert not telegram.mirror(paths, "needs_you", SimpleNamespace(key="uuid"))
    assert not (tmp_path / "telegram.json").exists()


def test_secret_is_encrypted_and_not_returned(tmp_path, monkeypatch):
    from codex_auto_resume import telegram
    monkeypatch.setattr(telegram, "_dpapi", lambda data, decrypt=False: data[::-1])
    paths = _paths(tmp_path)
    result = telegram.configure(paths, {"enabled": True, "token": _token(), "chat_id": "-10012345"})
    assert result == {"enabled": True, "configured": True, "chat_id": "-10012345"}
    data = (tmp_path / "telegram.json").read_text(encoding="utf-8")
    assert _token() not in data
    assert "encrypted_token" in json.loads(data)
    assert telegram._token(telegram._read(paths)) == _token()


def test_password_box_empty_preserves_saved_token(tmp_path, monkeypatch):
    from codex_auto_resume import telegram
    monkeypatch.setattr(telegram, "_dpapi", lambda data, decrypt=False: data)
    paths = _paths(tmp_path)
    telegram.configure(paths, {"enabled": True, "token": _token(), "chat_id": "1234"})
    telegram.configure(paths, {"enabled": True, "token": "", "chat_id": "5678"})
    assert telegram._token(telegram._read(paths)) == _token()
    assert telegram.status(paths)["chat_id"] == "5678"


def test_invalid_input_does_not_create_config(tmp_path, monkeypatch):
    from codex_auto_resume import telegram
    monkeypatch.setattr(telegram, "_dpapi", lambda data, decrypt=False: data)
    paths = _paths(tmp_path)
    with pytest.raises(telegram.TelegramError):
        telegram.configure(paths, {"token": None})
    with pytest.raises(telegram.TelegramError):
        telegram.configure(paths, {"token": "invalid"})
    with pytest.raises(telegram.TelegramError):
        telegram.configure(paths, {"enabled": True, "chat_id": "not-an-id"})
    with pytest.raises(telegram.TelegramError):
        telegram.configure(paths, {"chat_id": 1234})
    assert not (tmp_path / "telegram.json").exists()


def test_payload_no_task_text_no_title(tmp_path, monkeypatch):
    from codex_auto_resume import telegram
    monkeypatch.setattr(telegram, "_dpapi", lambda data, decrypt=False: data)
    paths = _paths(tmp_path)
    telegram.configure(paths, {"enabled": True, "token": _token(), "chat_id": "12345"})
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, _limit): return b'{"ok":true}'

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return Response()

    notice = SimpleNamespace(key="uuid-123", title="PRIVATE TITLE")
    message = telegram._message("needs_you", notice, {"prompt": "SECRET PROMPT"})
    telegram._send(paths, message, opener=opener)
    assert b"PRIVATE" not in captured["data"]
    assert b"SECRET" not in captured["data"]
    assert b"uuid-123" in captured["data"]
    assert captured["timeout"] == telegram.SEND_TIMEOUT


def test_reset_timestamp_reported_without_task_title():
    from codex_auto_resume import telegram
    notice = SimpleNamespace(key="uuid", title="MY PRIVATE PROJECT")
    message = telegram._message("interruption", notice, {"reset_at": 1792141200})
    assert "Ожидаемое снятие лимита" in message
    assert "PRIVATE" not in message
    assert "uuid" in message


def test_network_error_never_discloses_token(tmp_path, monkeypatch):
    from codex_auto_resume import telegram
    monkeypatch.setattr(telegram, "_dpapi", lambda data, decrypt=False: data)
    paths = _paths(tmp_path)
    telegram.configure(paths, {"enabled": True, "token": _token(), "chat_id": "1234"})
    def fail(request, timeout):
        raise OSError("URL contained bot token " + _token())
    with pytest.raises(telegram.TelegramError) as info:
        telegram._send(paths, "test", opener=fail)
    assert _token() not in str(info.value)


def test_queue_delivery_and_disable(tmp_path, monkeypatch):
    from codex_auto_resume import telegram
    monkeypatch.setattr(telegram, "_dpapi", lambda data, decrypt=False: data)
    monkeypatch.setattr(telegram, "_QUEUE", queue.Queue(maxsize=4))
    monkeypatch.setattr(telegram, "_QUEUE_THREAD", None)
    sent = []
    monkeypatch.setattr(telegram, "_send", lambda paths, msg: sent.append(msg))
    paths = _paths(tmp_path)
    telegram.configure(paths, {"enabled": True, "token": _token(), "chat_id": "1234"})
    assert telegram.mirror(paths, "needs_you", SimpleNamespace(key="uuid"))
    telegram._QUEUE.join()
    assert len(sent) == 1 and "требуется" in sent[0]
    telegram.configure(paths, {"enabled": False})
    assert not telegram.mirror(paths, "needs_you", SimpleNamespace(key="uuid"))


def test_read_rejects_symlink(tmp_path):
    from codex_auto_resume import telegram
    external = tmp_path / "secrets.json"
    external.write_text(json.dumps({"enabled": True, "encrypted_token": "abc"}))
    state = tmp_path / "config"
    state.mkdir()
    link = state / "telegram.json"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    assert telegram.status(_paths(state))["enabled"] is False


def test_notice_kind_controls_result_message():
    from codex_auto_resume import telegram
    assert "подтверждено" in telegram._message("result", SimpleNamespace(kind="resumed"))
    assert "Ошибка" in telegram._message("result", SimpleNamespace(kind="failed"))
    assert "неизвестен" in telegram._message("result", SimpleNamespace(kind="unknown"))
