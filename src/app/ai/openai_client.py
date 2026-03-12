from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from ..config import OpenAIModelConfig


class OpenAIChatClient:
    def __init__(self, cfg: OpenAIModelConfig, enabled: bool = True):
        self._cfg = cfg
        api_key = (cfg.api_key or "").strip()
        unresolved_env = api_key.startswith("${") and api_key.endswith("}")
        self._enabled = enabled and bool(api_key and cfg.model_id) and not unresolved_env
        self._logger = logging.getLogger(self.__class__.__name__)

        self._base_url = self._normalize_base_url(cfg.base_url)
        self._client: Optional[httpx.Client] = None
        self._preferred_mode: Optional[str] = None
        if self._enabled:
            self._client = httpx.Client(
                timeout=cfg.timeout_seconds,
                follow_redirects=True,
                trust_env=True,
            )
        else:
            self._logger.warning("[AI] OpenAI disabled or misconfigured")

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def ensure_available(self) -> None:
        if not self.enabled:
            raise RuntimeError("AI 未启用或配置缺失（api_key/model_id）")

        _ = self.chat(
            system_prompt="你是连通性检测助手。",
            user_prompt="只回复OK",
            temperature=0,
            max_tokens=16,
        )

    def chat(self, system_prompt: str, user_prompt: str, temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
        if not self.enabled:
            raise RuntimeError("openai client is disabled")

        temp = self._cfg.temperature if temperature is None else temperature
        tokens = self._cfg.max_tokens if max_tokens is None else max_tokens

        attempts = [
            ("responses.instructions", lambda: self._chat_via_responses(system_prompt, user_prompt, temp, tokens, use_instruction_field=True)),
            ("responses.input_items", lambda: self._chat_via_responses(system_prompt, user_prompt, temp, tokens, use_instruction_field=False)),
            ("chat.completions", lambda: self._chat_via_chat_completions(system_prompt, user_prompt, temp, tokens)),
        ]

        if self._preferred_mode:
            ordered = [item for item in attempts if item[0] == self._preferred_mode]
            ordered.extend(item for item in attempts if item[0] != self._preferred_mode)
            attempts = ordered

        errors: list[str] = []
        for mode, runner in attempts:
            try:
                self._logger.debug("[AI] chat attempt mode=%s system_prompt=%.200s user_prompt=%.300s", mode, system_prompt, user_prompt)
                text = runner()
                self._logger.debug("[AI] chat success mode=%s response=%.300s", mode, text)
                self._preferred_mode = mode
                return text
            except RuntimeError as exc:
                msg = str(exc)
                errors.append(f"{mode}: {msg}")
                if not self._should_fallback(msg):
                    raise

        raise RuntimeError("all API modes failed: " + " | ".join(errors))

    def _chat_via_responses(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        use_instruction_field: bool,
    ) -> str:
        if use_instruction_field:
            payload: dict[str, Any] = {
                "model": self._cfg.model_id,
                "instructions": system_prompt,
                "input": user_prompt,
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
        else:
            payload = {
                "model": self._cfg.model_id,
                "input": [
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": system_prompt}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_prompt}],
                    },
                ],
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }

        data = self._post_json("/responses", payload)
        text = self._extract_text(data)
        if not text:
            raise RuntimeError("openai empty response")
        return text.strip()

    def _chat_via_chat_completions(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> str:
        payload = {
            "model": self._cfg.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = self._post_json("/chat/completions", payload)
        text = self._extract_chat_completions_text(data)
        if not text:
            raise RuntimeError("openai empty response")
        return text.strip()

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict:
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }

        response = self._client.post(url, headers=headers, content=json.dumps(payload, ensure_ascii=False))
        if response.status_code >= 400:
            body = response.text.strip()
            raise RuntimeError(f"api error {response.status_code}: {body[:300]}")

        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("api returned non-json response") from exc

        if not isinstance(data, dict):
            raise RuntimeError("api returned invalid json object")
        return data

    @staticmethod
    def _should_fallback(error_text: str) -> bool:
        text = (error_text or "").lower()
        fallback_codes = (
            "api error 400",
            "api error 404",
            "api error 405",
            "api error 415",
            "api error 422",
            "api error 500",
            "api error 502",
            "api error 503",
            "api error 504",
        )
        if any(code in text for code in fallback_codes):
            return True

        compatibility_hints = (
            "instructions are required",
            "unsupported",
            "not implemented",
            "unknown field",
            "invalid request",
            "unrecognized request",
            "openai empty response",
            "api returned invalid json object",
        )
        return any(token in text for token in compatibility_hints)

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        raw = (base_url or "").strip().rstrip("/")
        if not raw:
            return "https://api.openai.com/v1"
        if raw.endswith("/v1"):
            return raw
        return f"{raw}/v1"

    @staticmethod
    def _extract_text(data: dict) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        output = data.get("output")
        if isinstance(output, list):
            chunks = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    text = c.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text)
            if chunks:
                return "\n".join(chunks)

        return ""

    @staticmethod
    def _extract_chat_completions_text(data: dict) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list):
            return ""

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue

            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content

            if isinstance(content, list):
                chunks = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text)
                if chunks:
                    return "\n".join(chunks)

        return ""
