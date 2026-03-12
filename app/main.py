from __future__ import annotations

import argparse
from pathlib import Path

from app.config import load_config
from app.logging_setup import setup_logging
from app.orchestrator import CommentOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="快手 AI 评论自动化（单机版）")
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="配置文件路径，默认: config.yaml",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="仅执行一轮流程",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        example_path = Path("config.example.yaml")
        raise FileNotFoundError(
            f"未找到配置文件: {args.config}。请复制 {example_path} 为 config.yaml 并填写后重试。"
        )

    cfg = load_config(str(config_path))
    logger = setup_logging(cfg.log.level, cfg.log.file)
    logger.info("[AUTO] 配置加载完成 | model=%s | base_url=%s", cfg.model.model_id, cfg.model.base_url)

    orchestrator = CommentOrchestrator(cfg, logger)
    try:
        if args.once or not cfg.run.continuous:
            orchestrator.run_round()
        else:
            orchestrator.run_forever()
    finally:
        orchestrator.close()

    return 0
