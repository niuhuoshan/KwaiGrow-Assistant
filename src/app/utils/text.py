from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List

_RE_SPACES = re.compile(r"\s+")
_RE_CODE_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_RE_BRACE = re.compile(r"\{.*?\}", re.S)
_RE_BRACKET = re.compile(r"\[.*?\]", re.S)

_KEYWORD_PUNCT = frozenset(["，", "。", "！", "？", "；", ",", ".", "!", "?", "#", "\n"])


def normalize_spaces(value: str) -> str:
    return _RE_SPACES.sub(" ", (value or "")).strip()


def is_valid_keyword(text: str) -> bool:
    if not text:
        return False
    if len(text) < 2 or len(text) > 16:
        return False
    if any(mark in text for mark in _KEYWORD_PUNCT):
        return False
    if text.count(" ") > 1:
        return False
    return True


def short_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:24]


def safe_json_loads(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = _RE_CODE_BLOCK.search(text)
    if match:
        snippet = match.group(1).strip()
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None

    brace = _RE_BRACE.search(text)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass

    bracket = _RE_BRACKET.search(text)
    if bracket:
        try:
            return json.loads(bracket.group(0))
        except json.JSONDecodeError:
            pass

    return None


def to_clean_string_list(value: Any, max_count: int) -> List[str]:
    result: List[str] = []
    seen = set()

    if isinstance(value, list):
        source = value
    elif isinstance(value, dict):
        source = value.get("items") or value.get("keywords") or value.get("comments") or []
    else:
        source = []

    for item in source:
        text = normalize_spaces(str(item))
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= max_count:
            break

    return result


def truncate(value: str, limit: int) -> str:
    text = normalize_spaces(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
