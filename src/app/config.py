from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class OpenAIModelConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str
    model_id: str
    temperature: float = 0.7
    max_tokens: int = 500
    timeout_seconds: int = 45


class BrowserConfig(BaseModel):
    ws_url: Optional[str] = None
    headless: bool = False
    executable_path: Optional[str] = None
    user_data_dir: str = "./data/browser"
    navigation_timeout_ms: int = 45000
    action_timeout_ms: int = 18000
    post_load_wait_ms: int = 1200
    search_each_post: bool = False
    auto_launch_chrome: bool = True
    remote_debugging_port: int = 9222


class SelectorConfig(BaseModel):
    kuaishou_selector_file: str = "./config/selectors/kuaishou.yaml"


class AIConfig(BaseModel):
    enabled: bool = True
    keyword_max_count: int = 10
    comment_candidate_count: int = 3
    strict_comment_gate: bool = False


class DedupConfig(BaseModel):
    sqlite_path: str = "./data/dedup.sqlite3"
    by_post_id: bool = True
    by_url: bool = True
    by_title_hash: bool = True
    avoid_repeated_comment_text: bool = True


class WaitRange(BaseModel):
    min_seconds: float = 1.0
    max_seconds: float = 3.0

    @field_validator("max_seconds")
    @classmethod
    def validate_max_seconds(cls, value: float, info):
        min_value = info.data.get("min_seconds", 0)
        if value < min_value:
            raise ValueError("max_seconds must be >= min_seconds")
        return value


class RuntimeConfig(BaseModel):
    max_comments_per_round: int = 5
    daily_comment_limit: int = 30
    search_limit_per_keyword: int = 10
    single_keyword_search: bool = True
    disable_keyword_expansion: bool = False
    comment_every_post: bool = True
    sort_by: str = "latest"
    time_range: str = "week"
    action_wait_seconds: WaitRange = Field(default_factory=lambda: WaitRange(min_seconds=1.0, max_seconds=2.5))
    round_wait_seconds: WaitRange = Field(default_factory=lambda: WaitRange(min_seconds=20.0, max_seconds=45.0))


class CommentRuleConfig(BaseModel):
    requirements: List[str]
    banned_words: List[str] = Field(default_factory=list)
    min_length: int = 5
    max_length: int = 80


class TopicConfig(BaseModel):
    direction_keywords: List[str]


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file_path: str = "./logs/app.log"


class AppConfig(BaseModel):
    platform: str = "kuaishou"
    account_id: str = "default"
    openai: OpenAIModelConfig
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    selectors: SelectorConfig = Field(default_factory=SelectorConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    topics: TopicConfig
    comment_rules: CommentRuleConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class ConfigError(RuntimeError):
    pass


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_config(config_path: str | Path) -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = _expand_env(raw)

    try:
        cfg = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid config: {exc}") from exc

    return cfg


def load_selector_map(selector_file: str | Path) -> Dict[str, str]:
    path = Path(selector_file)
    if not path.exists():
        raise ConfigError(f"selector file not found: {path}")

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    selectors = payload.get("selectors")
    if not isinstance(selectors, dict):
        raise ConfigError(f"invalid selector yaml structure: {path}")

    normalized: Dict[str, str] = {}
    for key, value in selectors.items():
        if isinstance(value, str):
            normalized[str(key)] = value.strip()

    return normalized
