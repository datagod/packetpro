"""CLI entry points for PacketPro."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from rich.console import Console

from packetpro.config import ConfigError, load_config
from packetpro.web.app import create_app
from packetpro.workers.enhance_worker import run_enhance_worker
from packetpro.workers.ocr_worker import run_ocr_worker
from packetpro.workers.watch_worker import run_watch_worker

console = Console()


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to config YAML (default: ~/.config/packetpro/config.yaml)",
    )


def _load_or_exit(config_path: Path | None):
    try:
        return load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise SystemExit(1) from exc


def cmd_enhance(args: argparse.Namespace) -> None:
    config = _load_or_exit(args.config)
    run_enhance_worker(config)


def cmd_ocr(args: argparse.Namespace) -> None:
    config = _load_or_exit(args.config)
    run_ocr_worker(config)


def cmd_watch(args: argparse.Namespace) -> None:
    config = _load_or_exit(args.config)
    run_watch_worker(config)


def _web_bind(config_path: Path | None) -> tuple[str, int]:
    from packetpro.config import load_raw_config

    raw, _ = load_raw_config(config_path)
    web_raw = raw.get("web", {})
    return str(web_raw.get("host", "127.0.0.1")), int(web_raw.get("port", 8787))


def cmd_web(args: argparse.Namespace) -> None:
    host, port = _web_bind(args.config)
    try:
        config = load_config(args.config)
    except ConfigError:
        app = create_app()
    else:
        app = create_app(config)
        host, port = config.web.host, config.web.port
    uvicorn.run(app, host=host, port=port, log_level="info")


def cmd_init(args: argparse.Namespace) -> None:
    from packetpro.db import init_db

    config = _load_or_exit(args.config)
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

    watch = sub.add_parser("watch", help="Watch an external folder and process files in place")
    _add_config_arg(watch)
    watch.set_defaults(func=cmd_watch)

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