"""Optional Russian Telegram channel for watcher notifications.

Telegram is an opt-in mirror of the notification pipeline. Secrets are stored
separately from settings.json with Windows DPAPI (CurrentUser). Network traffic
never blocks the Codex watcher and never includes conversation text or paths.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

TOKEN_PATTERN = re.compile(r"\A[0-9]{5,15}:[A-Za-z0-9_-]{20,}\Z")
CHAT_PATTERN = re.compile(r"\A-?[0-9]{1,20}\Z")
MAX_BYTES = 8192
SEND_TIMEOUT = 5
_QUEUE = queue.Queue(maxsize=64)
_QUEUE_LOCK = threading.Lock()
_QUEUE_THREAD = None


class TelegramError(ValueError):
    """Safe error text for UI; no token or HTTP response body."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data):
    buf = ctypes.create_string_buffer(data)
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise TelegramError("Шифрование токена доступно только в Windows")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    source, backing = _blob(data)
    result = _Blob()
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = (
        [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        if decrypt else
        [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    )
    operation.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN: no password prompt in background watcher.
    if not operation(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise TelegramError("Windows не смогла защитить или прочитать токен")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _path(paths) -> Path:
    return Path(paths.state_dir) / "telegram.json"


def _read(paths) -> dict:
    target = _path(paths)
    try:
        if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_BYTES:
            return {}
        obj = json.loads(target.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def status(paths) -> dict:
    obj = _read(paths)
    return {"enabled": obj.get("enabled") is True,
            "configured": bool(obj.get("encrypted_token")),
            "chat_id": obj.get("chat_id") if isinstance(obj.get("chat_id"), str) else ""}


def _token(obj: dict) -> str:
    try:
        raw = base64.b64decode(obj["encrypted_token"], validate=True)
        token = _dpapi(raw, decrypt=True).decode("ascii")
        if TOKEN_PATTERN.fullmatch(token):
            return token
    except (KeyError, TypeError, ValueError, UnicodeError):
        pass
    raise TelegramError("Токен не настроен или недоступен текущему пользователю Windows")


def configure(paths, data: dict) -> dict:
    if not isinstance(data, dict) or not set(data).issubset({"enabled", "token", "chat_id"}):
        raise TelegramError("Неправильные параметры Telegram")
    if "enabled" in data and type(data["enabled"]) is not bool:
        raise TelegramError("Недопустимое значение переключателя")
    obj = dict(_read(paths))
    if "enabled" in data:
        obj["enabled"] = data["enabled"]
    if "chat_id" in data:
        if not isinstance(data["chat_id"], str):
            raise TelegramError("Chat ID должен быть строкой")
        chat = data["chat_id"].strip()
        if chat and not CHAT_PATTERN.fullmatch(chat):
            raise TelegramError("Chat ID должен состоять из цифр")
        obj["chat_id"] = chat
    if "token" in data:
        token = data["token"]
        if not isinstance(token, str):
            raise TelegramError("Неправильный формат токена бота")
        if token:
            if len(token) > 256 or not TOKEN_PATTERN.fullmatch(token):
                raise TelegramError("Неправильный формат токена бота")
            obj["encrypted_token"] = base64.b64encode(_dpapi(token.encode("ascii"))).decode("ascii")
    if obj.get("enabled") and (not CHAT_PATTERN.fullmatch(obj.get("chat_id", ""))
                               or not obj.get("encrypted_token")):
        raise TelegramError("Для включения укажи токен бота и Chat ID")
    target = _path(paths)
    if hasattr(paths, "confined") and not paths.confined(target):
        raise TelegramError("Папка Telegram находится за пределами установки")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise TelegramError("Недопустимая ссылка вместо файла Telegram")
    fd, temporary = tempfile.mkstemp(prefix="telegram.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(obj, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return status(paths)


EVENTS_RU = {
    "interruption": "Работа прервана — ожидается возобновление",
    "objection": "Скоро будет отправлено продолжение",
    "starting": "Возобновление задачи запускается",
    "result": "Проверен результат возобновления",
    "needs_you": "Беседе требуется твоё участие",
    "stopped": "Автовозобновление этой задачи остановлено",
    "after_sleep": "После сна компьютера задачи ожидают решения",
    "memory_warning": "Монитор: превышен порог использования памяти",
    "memory_stopped": "Монитор остановлен из-за памяти",
    "power_grace": "Компьютер скоро перейдёт в режим энергосбережения",
    "power_now": "Выполняется действие с питанием компьютера",
    "power_failed": "Действие с питанием не удалось",
    "power_not_met": "Условия действия с питанием не выполнены",
    "power_stopped": "Действие с питанием отменено",
}


def _message(event, notice, detail=None) -> str:
    detail = detail if isinstance(detail, dict) else {}
    name = EVENTS_RU.get(event, "Событие Codex Auto Resume")
    if event == "result":
        name = {"resumed": "Возобновление подтверждено",
                "failed": "Ошибка возобновления",
                "unknown": "Результат отправки неизвестен — повтор не выполняется"}.get(
                    getattr(notice, "kind", ""), name)
    thread = getattr(notice, "key", "")
    thread_line = "\nБеседа: " + thread if isinstance(thread, str) and thread else ""
    reset_line = ""
    when = detail.get("reset_at")
    if event == "interruption" and type(when) in (int, float) and 946684800 <= when <= 4102444800:
        try:
            reset_line = "\nОжидаемое снятие лимита: " + datetime.fromtimestamp(when).strftime("%d.%m.%Y %H:%M")
        except (OSError, OverflowError, ValueError):
            pass
    return "Codex Auto Resume\n" + name + reset_line + thread_line


def _send(paths, message: str, opener=None):
    obj = _read(paths)
    token = _token(obj)
    chat = obj.get("chat_id", "")
    if not isinstance(chat, str) or not CHAT_PATTERN.fullmatch(chat):
        raise TelegramError("Не задан Chat ID")
    body = urllib.parse.urlencode({"chat_id": chat, "text": message,
                                   "disable_web_page_preview": "true"}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=SEND_TIMEOUT) as response:
            if json.loads(response.read(4096)).get("ok") is not True:
                raise TelegramError("Telegram не подтвердил доставку")
    except (OSError, urllib.error.URLError, ValueError, TypeError):
        # Never propagate a URL: the Telegram bot token is part of it.
        raise TelegramError("Не удалось отправить сообщение в Telegram") from None


def test(paths):
    _send(paths, "Codex Auto Resume: проверка Telegram прошла успешно")
    return {"sent": True}


def _drain():
    """Dedicated sender thread: never block toast delivery or the recovery loop."""
    while True:
        paths, event, message, log = _QUEUE.get()
        try:
            # The user may have disabled Telegram while messages were waiting.
            if status(paths)["enabled"]:
                _send(paths, message)
        except Exception:
            if log is not None:
                log("telegram: notification could not be delivered (%s)", event)
        finally:
            _QUEUE.task_done()


def mirror(paths, event, notice, log=None, *, detail=None) -> bool:
    """Queue only events already allowed by the original notification switches."""
    global _QUEUE_THREAD
    try:
        if not status(paths)["enabled"]:
            return False
        _QUEUE.put_nowait((paths, event, _message(event, notice, detail), log))
    except queue.Full:
        if log is not None:
            log("telegram: notification queue full (%s)", event)
        return False
    except Exception:
        return False
    with _QUEUE_LOCK:
        if _QUEUE_THREAD is None or not _QUEUE_THREAD.is_alive():
            _QUEUE_THREAD = threading.Thread(
                target=_drain, name="telegram-notifications", daemon=True
            )
            _QUEUE_THREAD.start()
    return True
