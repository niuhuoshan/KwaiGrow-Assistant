from __future__ import annotations

from typing import Callable, List, Tuple


def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def pick_comment(
    candidates: List[str],
    max_length: int,
    banned_words: List[str],
    duplicate_checker: Callable[[str], bool],
) -> Tuple[str, List[str]]:
    rejected: List[str] = []
    clean_candidates: List[str] = []
    seen = set()

    for item in candidates:
        text = normalize_text(item)
        if not text:
            rejected.append("空文本")
            continue
        if text in seen:
            rejected.append(f"重复候选: {text}")
            continue
        seen.add(text)
        clean_candidates.append(text)

    for text in clean_candidates:
        if len(text) > max_length:
            rejected.append(f"超长({len(text)}>{max_length}): {text}")
            continue

        hit_word = next((w for w in banned_words if w and w in text), "")
        if hit_word:
            rejected.append(f"命中禁词({hit_word}): {text}")
            continue

        if duplicate_checker(text):
            rejected.append(f"与近期评论重复: {text}")
            continue

        return text, rejected

    return "", rejected
