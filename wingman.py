"""
wingman.py
----------
The core service function: given a relationship + message, builds the prompt,
calls DeepSeek V4 Flash, validates the response against schemas.WingmanResponse,
and returns it. This module has no HTTP or UI code in it on purpose — backend.py
and any batch/eval script both import get_wingman_response() directly.

Model note: DeepSeek V4 Flash (not V3.1) — V3.1/deepseek-chat is the legacy
alias being deprecated by DeepSeek on 2026-07-24; V4 Flash is the current
model at the same price point, so there's no reason to target the old name.
"""

import json
import re
import hashlib
import time
import os
import random
import urllib.request
import urllib.error
from typing import Optional

from pydantic import ValidationError

from prompt import build_messages
from memory import MemoryManager
from schemas import WingmanResponse
import cache
import storage_utils as storage

MODEL = "deepseek-v4-flash"
API_URL = "https://api.deepseek.com/v1/chat/completions"

# Tracks the outcome of the most recent *real* API attempt, so backend.py's
# /health endpoint can report actual connectivity, not just "a key is set."
_last_api_status = {
    "attempted": False,
    "success": None,
    "last_error": None,
    "last_attempt_ts": None,
}


def get_api_status() -> dict:
    return dict(_last_api_status)


class WingmanError(Exception):
    """Raised when DeepSeek fails or returns something that doesn't validate."""
    pass


FALLBACK_DEBUG_LOG_PATH = os.path.join(os.path.dirname(__file__), "fallback_debug.log")


def _log_fallback(relationship: str, message: str, reason: str, raw_text: Optional[str]):
    """
    Appends a diagnostic line whenever a live call falls back to mock —
    including the actual raw text DeepSeek returned, not just "it failed".
    Without this, every fallback is invisible: results.jsonl only records
    that a mock response was shown, never *what the model actually said*
    that couldn't be parsed. That gap is exactly what made the previous
    invalid-JSON issue hard to diagnose precisely — we knew fallback fired,
    but had zero visibility into the actual malformed output.
    Never lets a logging failure affect the real response.
    """
    try:
        with storage.get_lock(FALLBACK_DEBUG_LOG_PATH):
            with open(FALLBACK_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.time(),
                    "relationship": relationship,
                    "message": message[:200],
                    "reason": reason,
                    "raw": raw_text[:3000] if raw_text else None,
                }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _call_deepseek(messages: list, api_key: str, retries: int = 2) -> tuple:
    payload = {
        "model": MODEL,
        "messages": messages,

        "temperature": 0.82,
        "top_p": 0.92,

        "frequency_penalty": 0.25,
        "presence_penalty": 0.15,

        "max_tokens": 900,

        "response_format": {
            "type": "json_object"
        }
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    delay = 1.5
    last_err = None
    for attempt in range(retries):
        try:
            start = time.time()
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - start
            content = data["choices"][0]["message"]["content"]
            _last_api_status.update(attempted=True, success=True, last_error=None, last_attempt_ts=time.time())
            return content, elapsed
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            _last_api_status.update(attempted=True, success=False, last_error=f"HTTP {e.code} {e.reason}", last_attempt_ts=time.time())
            raise WingmanError(f"DeepSeek HTTP error: {e.code} {e.reason}") from e
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            _last_api_status.update(attempted=True, success=False, last_error=str(e), last_attempt_ts=time.time())
            raise WingmanError(f"DeepSeek call failed after {retries} attempts: {e}") from e
    _last_api_status.update(attempted=True, success=False, last_error=str(last_err), last_attempt_ts=time.time())
    raise WingmanError(str(last_err))


_MOCK_TEMPLATES = [
    {
        "keywords": ["code", "debug", "python", "math", "equation", "homework", "algorithm"],
        "payload": {
            "mode": "advice",
            "energy_read": "off-topic request",
            "wingman_response": "haha wrong wingman for that one, I only do relationships and texts 😄",
            "suggested_messages": [],
            "follow_up_question": None,
            "safety_flag": False,
            "confidence": 0.95,
        },
    },
    {
        "keywords": ["haha", "lol", "one word", "dry", "left me on read", "short repl", "thumbs up", "read and"],
        "payload": {
            "mode": "reply_suggestion",
            "energy_read": "they're giving low effort, but not necessarily losing interest yet",
            "wingman_response": "don't chase it, match the energy once instead of writing another paragraph",
            "suggested_messages": [
                "okay noted, tough crowd today 😂",
                "wow riveting response, truly a novelist",
            ],
            "follow_up_question": None,
            "safety_flag": False,
            "confidence": 0.78,
        },
    },
    {
        "keywords": ["forgot", "cancelled", "cancel", "mistake", "accidentally", "annoyed", "hurt", "quiet"],
        "payload": {
            "mode": "reply_suggestion",
            "energy_read": "they're hurt and probably want acknowledgment, not a justification",
            "wingman_response": "own it straight up, don't explain your way out of it before you've actually apologized",
            "suggested_messages": [
                "that was on me, I should've been more thoughtful about it",
                "I get why that stung, I'm sorry",
            ],
            "follow_up_question": None,
            "safety_flag": False,
            "confidence": 0.74,
        },
    },
    {
        "keywords": ["miss", "traveling", "haven't seen", "disconnected", "far", "long distance"],
        "payload": {
            "mode": "advice",
            "energy_read": "genuine longing, no drama behind it",
            "wingman_response": "say it plainly, this doesn't need to be dressed up or downplayed",
            "suggested_messages": [
                "randomly missing you a lot today",
            ],
            "follow_up_question": None,
            "safety_flag": False,
            "confidence": 0.8,
        },
    },
    {
        "keywords": ["birthday", "gift", "anniversary", "date idea", "plan", "surprise", "budget"],
        "payload": {
            "mode": "planning",
            "energy_read": "practical planning need, low emotional risk",
            "wingman_response": "keep it specific to her instead of generic — a small thoughtful detail beats an expensive guess",
            "suggested_messages": [],
            "follow_up_question": "What's your rough budget for this?",
            "safety_flag": False,
            "confidence": 0.7,
        },
    },
    {
        "keywords": ["don't know", "confusing", "off between us", "we need to talk", "out of nowhere"],
        "payload": {
            "mode": "clarification_needed",
            "energy_read": "too little context to read this confidently",
            "wingman_response": "hard to call this one without knowing what's changed recently on your side too",
            "suggested_messages": [],
            "follow_up_question": "Has anything changed in how often you two have been talking lately?",
            "safety_flag": False,
            "confidence": 0.5,
        },
    },
]

_DEFAULT_MOCK_PAYLOADS = [
    {
        "mode": "reply_suggestion",
        "energy_read": "reads like a fairly normal, low-stakes moment",
        "wingman_response": "keep it light, no need to overthink this one",
        "suggested_messages": ["haha fair enough", "noted 😌"],
        "follow_up_question": None,
        "safety_flag": False,
        "confidence": 0.65,
    },
    {
        "mode": "advice",
        "energy_read": "nothing alarming here, just a normal update",
        "wingman_response": "nothing to fix here, just respond naturally",
        "suggested_messages": ["makes sense, thanks for the update"],
        "follow_up_question": None,
        "safety_flag": False,
        "confidence": 0.6,
    },
    {
        "mode": "clarification_needed",
        "energy_read": "not quite enough here to give a confident read",
        "wingman_response": "I'd need a bit more to go on for this one",
        "suggested_messages": [],
        "follow_up_question": "What's the actual situation you're trying to navigate here?",
        "safety_flag": False,
        "confidence": 0.45,
    },
]

# very simple pattern-based mock memory-candidate detector — just enough to
# demo the accept/discard flow offline; the real detection happens inside
# DeepSeek's structured output once a live key is used.
_MOCK_MEMORY_PATTERNS = [
    (re.compile(r"(?:loves?|likes?) ([a-zA-Z ]+)"), "preference", "likes {0}"),
    (re.compile(r"hates? ([a-zA-Z ]+)"), "preference", "dislikes {0}"),
    (re.compile(r"birthday is ([a-zA-Z0-9 ,]+)"), "important_date", "Birthday: {0}"),
    (re.compile(r"favorite ([a-zA-Z ]+) is ([a-zA-Z ]+)"), "preference", "favorite {0} is {1}"),
]


def _detect_mock_candidates(user_text: str) -> list:
    candidates = []
    for pattern, category, template in _MOCK_MEMORY_PATTERNS:
        m = pattern.search(user_text)
        if m:
            value = template.format(*[g.strip() for g in m.groups()])
            candidates.append({"category": category, "value": value})
    return candidates


def _extract_situation_text(last_message_content: str) -> str:
    """
    The last user message is '[Relationship context]\\n...\\n\\n[User's situation]\\n<actual message>'.
    Keyword matching and memory-candidate detection must only look at the
    actual new message — otherwise stored memory (e.g. 'short replies' in
    conversation_style) keeps re-matching every single turn and gets
    re-flagged as a 'new' candidate, which is wrong on both counts.
    """
    marker = "[User's situation]\n"
    idx = last_message_content.find(marker)
    if idx == -1:
        return last_message_content
    return last_message_content[idx + len(marker):]


def _keyword_hits(text: str, keywords: list) -> bool:
    """Word-boundary matching so short/common keywords (e.g. 'plan') don't
    false-positive on substrings inside unrelated words ('explanation')."""
    for kw in keywords:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, text):
            return True
    return False


def _mock_call(messages: list) -> tuple:
    """Offline stand-in so the rest of the pipeline can be tested without a key/network.
    Picks a template by keyword match instead of always returning the same
    payload, so mock mode is actually useful for demoing the UI/pipeline.
    When nothing matches, picks from a small set of default responses
    (chosen deterministically by input hash, not randomly) rather than
    always returning the exact same line — a single fixed fallback became
    very noticeable/repetitive in practice once the live API fell back to
    mock several times in a row."""
    time.sleep(random.uniform(0.2, 0.5))
    situation_text = _extract_situation_text(messages[-1]["content"]).lower()

    chosen = None
    for template in _MOCK_TEMPLATES:
        if _keyword_hits(situation_text, template["keywords"]):
            chosen = template["payload"]
            break

    if chosen is None:
        idx = int(hashlib.sha256(situation_text.encode("utf-8")).hexdigest(), 16) % len(_DEFAULT_MOCK_PAYLOADS)
        chosen = _DEFAULT_MOCK_PAYLOADS[idx]

    payload = dict(chosen)
    payload["memory_candidates"] = _detect_mock_candidates(situation_text)
    return json.dumps(payload), round(random.uniform(0.2, 0.5), 3)


def _repair_json_text(raw: str) -> str:
    """
    Models frequently wrap structured output in markdown code fences
    (```json ... ```) or add a stray sentence before/after the JSON object,
    even with response_format=json_object set — this isn't a DeepSeek quirk
    specifically, it's common across providers. A raw json.loads() on that
    text fails even though the actual JSON inside is perfectly valid. This
    strips fences and extracts the first balanced {...} block before giving
    up, since falling back to mock for a cosmetic wrapping issue means
    throwing away a perfectly good real response.
    """
    text = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` fences
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # If it's not a clean {...} already, extract the first balanced brace block
    if not (text.startswith("{") and text.endswith("}")):
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        text = text[start:i + 1]
                        break
    return text


def _attempt_truncation_repair(text: str) -> str:
    """
    Last-resort repair for a response that got cut off mid-generation —
    hit max_tokens before the JSON object actually closed. This is different
    from _repair_json_text's job (stripping decoration around otherwise-
    complete JSON): here the JSON itself is incomplete.

    Walks the buffer tracking string/escape state and open brace/bracket
    depth, then closes whatever's still open. This recovers cleanly when
    truncation happens after all the fields that matter (mode,
    wingman_response, confidence, etc.) already generated and only a
    trailing optional field got cut short — memory_candidates is last in
    the schema, so it's the most common casualty, and losing it costs
    nothing since it's usually empty anyway. If truncation happened mid-way
    through a REQUIRED field instead, closing the JSON produces valid JSON
    that's still missing a required key, so it correctly fails schema
    validation and falls through to mock exactly as before — this can only
    rescue responses, never mask a genuinely broken one as more complete
    than it is.
    """
    text = text.rstrip()
    if not text.startswith("{"):
        return text

    stack = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    repaired = text
    if in_string:
        repaired += '"'
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def _try_parse_and_validate(raw: str):
    """Returns (parsed_dict_or_None, WingmanResponse_or_None). Never raises.
    Tries the raw text, then fence/prose-stripped, then truncation-repaired
    on top of that — in increasing order of how broken the input is assumed
    to be. Keeps trying all candidates rather than stopping at the first one
    that merely parses, since a raw response can parse as JSON but still
    fail schema validation while a further-repaired version would pass."""
    repaired = _repair_json_text(raw)
    candidates = (raw, repaired, _attempt_truncation_repair(repaired))
    last_parsed = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        last_parsed = parsed
        try:
            return parsed, WingmanResponse(**parsed)
        except ValidationError:
            continue
    return last_parsed, None


def get_wingman_response(
    relationship: str,
    message: str,
    api_key: Optional[str] = None,
    mock: bool = False,
    memory_manager: Optional[MemoryManager] = None,
    use_history: bool = True,
) -> dict:
    """
    Main entry point. Returns a dict:
      {
        "output": validated response dict, or None on failure,
        "response_time_sec": float or None,
        "schema_valid": bool,
        "cached": bool,         # True if served from cache.py, skipping the API call
        "warning": str,         # present only if a live call failed and we fell
                                 # back to mock — the chat still works, this is FYI
        "raw": str,             # present only if validation failed
        "error": str,           # present only if something failed
      }
    Never raises on a schema mismatch — callers (backend/eval) decide what to
    do with an invalid response, but a validation failure should never crash
    the app. A failed *live API call* is handled even more gently: it falls
    back to a mock response with a warning instead of erroring at all, so a
    flaky network never breaks the chat for the user.

    When use_history=True (default), prior turns for this relationship are
    pulled in for continuity, and the new user/assistant turns are appended
    to conversation.json afterward — this is what makes the chat feel like
    an ongoing thread instead of resetting every message. Batch eval runs
    should pass use_history=False so the 30 test prompts stay independent.
    """
    mm = memory_manager or MemoryManager()
    history = mm.get_history(relationship) if use_history else None
    messages = build_messages(relationship, message, mm, history=history)

    warning = None
    cached = False
    already_fell_back = mock or not api_key

    if already_fell_back:
        raw, elapsed = _mock_call(messages)
    else:
        cached_raw = cache.get(messages)
        if cached_raw is not None:
            raw, elapsed, cached = cached_raw, 0.0, True
        else:
            try:
                raw, elapsed = _call_deepseek(messages, api_key)
                cache.set(messages, raw)
            except WingmanError as e:
                # Graceful degradation: a broken/unreachable API should never
                # surface as a hard error to the user — fall back to mock and
                # say so, so the chat keeps working.
                warning = f"Live API call failed, showing a mock response instead: {e}"
                _log_fallback(relationship, message, "network_error", str(e))
                raw, elapsed = _mock_call(messages)
                already_fell_back = True

    parsed, validated = _try_parse_and_validate(raw)

    if validated is None and not already_fell_back:
        # The live call succeeded at the HTTP level but returned something
        # that isn't valid JSON or doesn't match the schema (e.g. truncated
        # output, or the model wrapped JSON in prose). This is just as much
        # a "the live call didn't work" situation as a network failure, and
        # should be handled the same gentle way — fall back to mock instead
        # of surfacing a raw 502 to the user.
        prior_issue = "invalid JSON" if parsed is None else "a response that didn't match the required schema"
        warning = (f"Live model returned {prior_issue}, showing a mock response instead."
                   + (f" ({warning})" if warning else ""))
        _log_fallback(relationship, message, "invalid_json" if parsed is None else "schema_mismatch", raw)
        raw, elapsed = _mock_call(messages)
        parsed, validated = _try_parse_and_validate(raw)

    if validated is None:
        # Only reachable if even the mock fallback failed to validate, which
        # would mean a bug in _mock_call itself, not a live-API problem.
        return {
            "output": None,
            "response_time_sec": round(elapsed, 3),
            "schema_valid": False,
            "raw": raw,
            "error": "Mock fallback also failed to produce a valid response — this indicates a bug in _mock_call, not an API issue.",
        }

    if use_history:
        mm.append_turn(relationship, "user", message)
        # Include the follow-up question in what gets remembered, not just
        # wingman_response — otherwise the model has no record of having
        # asked something on the next turn, and a short reply like "she
        # calls her mom" (answering "does she call her mom 'mom' or by
        # name?") gets read completely out of context, since as far as the
        # model's own history shows, it never asked that question at all.
        assistant_turn = validated.wingman_response
        if validated.follow_up_question:
            assistant_turn = f"{assistant_turn} {validated.follow_up_question}"
        mm.append_turn(relationship, "assistant", assistant_turn)

    result = {
        "output": validated.model_dump(),
        "response_time_sec": round(elapsed, 3),
        "schema_valid": True,
        "cached": cached,
    }
    if warning:
        result["warning"] = warning
    return result
