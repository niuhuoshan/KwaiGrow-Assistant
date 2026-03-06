from __future__ import annotations

import json
import logging
from typing import Optional

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
            "temperature": temp,
            "max_output_tokens": tokens,
        }

        url = f"{self._base_url}/responses"
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }

        response = self._client.post(url, headers=headers, content=json.dumps(payload, ensure_ascii=False))
        if response.status_code >= 400:
            body = response.text.strip()
            raise RuntimeError(f"api error {response.status_code}: {body[:300]}")

        data = response.json()
        text = self._extract_text(data)
        if not text:
            raise RuntimeError("openai empty response")

        return text.strip()

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
