from __future__ import annotations

import argparse
import logging
import sys

from .config import ConfigError, load_config
from .logging_setup import setup_logging
from .orchestrator import AutoCommenterOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kuaishou AI auto commenter (single-machine local tool)")
    parser.add_argument("--config", default="./config.yaml", help="Path to config YAML")
    parser.add_argument("--once", action="store_true", help="Run one round and exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 2

    setup_logging(cfg.logging)
    logger = logging.getLogger("app.main")

    orchestrator = AutoCommenterOrchestrator(cfg)
    try:
        orchestrator.run(once=args.once)
    except KeyboardInterrupt:
        logger.warning("[AUTOMATION] interrupted by user")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[AUTOMATION] fatal error: %s", exc)
        return 1
    finally:
        orchestrator.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
