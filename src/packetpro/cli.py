"""CLI entry points for PacketPro."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from rich.console import Console

from packetpro.config import load_config
from packetpro.web.app import create_app
from packetpro.workers.enhance_worker import run_enhance_worker
from packetpro.workers.ocr_worker import run_ocr_worker

console = Console()


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to config YAML (default: config.default.yaml in project root)",
    )


def cmd_enhance(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    run_enhance_worker(config)


def cmd_ocr(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    run_ocr_worker(config)


def cmd_web(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    app = create_app(config)
    uvicorn.run(app, host=config.web.host, port=config.web.port, log_level="info")


def cmd_init(args: argparse.Namespace) -> None:
    from packetpro.config import ensure_data_dirs
    from packetpro.db import init_db

    config = load_config(args.config)
    ensure_data_dirs(config)
    init_db(config.database)
    console.print(f"[green]Initialized data directories at[/green] {config.data_root}")
    console.print(f"[green]Database ready at[/green] {config.database}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="packetpro", description="PacketPro OCR pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    enhance = sub.add_parser("enhance", help="Watch inbox and enhance images for OCR")
    _add_config_arg(enhance)
    enhance.set_defaults(func=cmd_enhance)

    ocr = sub.add_parser("ocr", help="Watch transformed folder and run OCR")
    _add_config_arg(ocr)
    ocr.set_defaults(func=cmd_ocr)

    web = sub.add_parser("web", help="Start the search web UI")
    _add_config_arg(web)
    web.set_defaults(func=cmd_web)

    init_cmd = sub.add_parser("init", help="Create data folders and database")
    _add_config_arg(init_cmd)
    init_cmd.set_defaults(func=cmd_init)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()