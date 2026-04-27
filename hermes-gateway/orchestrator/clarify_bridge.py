from __future__ import annotations

import logging
import threading

import httpx

logger = logging.getLogger("clarify-bridge")

_pending_clarify: dict[tuple[str, str], dict] = {}
_lock = threading.Lock()


def register_pending_clarify(bot_token: str, chat_id: str) -> dict:
    key = (bot_token, chat_id)
    entry = {"event": threading.Event(), "answer": None, "question_msg_id": None}
    with _lock:
        _pending_clarify[key] = entry
    return entry


def resolve_clarify_reply(bot_token: str, chat_id: str, reply_text: str) -> bool:
    key = (bot_token, chat_id)
    with _lock:
        entry = _pending_clarify.get(key)
        if entry and not entry["event"].is_set():
            entry["answer"] = reply_text
            entry["event"].set()
            _pending_clarify.pop(key, None)
            return True
    return False


def make_clarify_callback(bot_token: str, chat_id: str, timeout: int = 600):
    def callback(question: str, choices: list[str] | None) -> str:
        text = f"\u2753 *Agent asks:*\n\n{question}"
        if choices:
            lines = [f"{i + 1}. {c}" for i, c in enumerate(choices)]
            lines.append(f"{len(choices) + 1}. Other (type your answer)")
            text += "\n\n" + "\n".join(lines)
        text += "\n\n_Reply to this message with your answer._"

        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=30,
            )
            result = resp.json()
            if not result.get("ok"):
                logger.error("Telegram sendMessage failed: %s", result)
                return f"[Failed to send question to Telegram: {result.get('description', 'unknown error')}]"
        except Exception as exc:
            logger.error("Telegram sendMessage error: %s", exc)
            return f"[Failed to send question to Telegram: {exc}]"

        entry = register_pending_clarify(bot_token, chat_id)

        if entry["event"].wait(timeout=timeout):
            return entry["answer"]
        else:
            with _lock:
                _pending_clarify.pop((bot_token, chat_id), None)
            return "[No response received within timeout. Proceeding without clarification.]"

    return callback
