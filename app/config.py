from __future__ import annotations

from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel, Field, model_validator


class ModelConfig(BaseModel):
    base_url: str
    api_key: str
    model_id: str
    temperature: float = 0.4
    timeout_seconds: int = 30
    max_tokens: int = 512


class RunConfig(BaseModel):
    headless: bool = False
    user_data_dir: str = "./data/browser"
    loop_interval_seconds: int = 900
    post_load_wait_seconds: float = 1.2
    sleep_min_seconds: float = 5.0
    sleep_max_seconds: float = 12.0
    per_round_comment_limit: int = 15
    daily_comment_limit: int = 60
    max_posts_per_keyword: int = 15
    continuous: bool = False
    dry_run: bool = False

    @model_validator(mode="after")
    def _check_sleep_range(self) -> "RunConfig":
        if self.sleep_max_seconds < self.sleep_min_seconds:
            raise ValueError("sleep_max_seconds must be >= sleep_min_seconds")
        return self


class DedupConfig(BaseModel):
    db_path: str = "./data/runtime.db"
    skip_same_title_hash: bool = True
    recent_comment_window: int = 80


class CommentConfig(BaseModel):
    requirement: str
    max_length: int = 60
    banned_words: List[str] = Field(default_factory=list)
    candidate_count: int = 3


class AIConfig(BaseModel):
    expand_keywords_count: int = 10
    enable_comment_gate: bool = True


class PlatformConfig(BaseModel):
    name: str = "kuaishou"
    selectors_file: str = "./app/browser/selectors/kuaishou.yaml"


class LogConfig(BaseModel):
    file: str = "./logs/runtime.log"
    level: str = "INFO"


class AppConfig(BaseModel):
    model: ModelConfig
    run: RunConfig = Field(default_factory=RunConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    comment: CommentConfig
    ai: AIConfig = Field(default_factory=AIConfig)
    platform: PlatformConfig = Field(default_factory=PlatformConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    topics: List[str]


def load_config(config_path: str) -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {config_path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = AppConfig.model_validate(data)
    ensure_runtime_paths(cfg)
    return cfg


def ensure_runtime_paths(cfg: AppConfig) -> None:
    Path(cfg.run.user_data_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.dedup.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.log.file).parent.mkdir(parents=True, exist_ok=True)
