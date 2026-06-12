# PacketPro

Drop images or PDFs into a folder, enhance them for OCR, extract text with **Qwen2.5-VL** on Ollama, archive the originals, and search everything from a web UI.

## Quick start

```bash
cd packetpro
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

packetpro init
```

Start the three workers (separate terminals or systemd):

```bash
packetpro enhance   # watches ~/packetpro-data/inbox
packetpro ocr       # watches ~/packetpro-data/transformed
packetpro web       # http://127.0.0.1:8787
```

Drop files into `~/packetpro-data/inbox/` and search at [http://127.0.0.1:8787](http://127.0.0.1:8787).

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) with `qwen2.5vl:7b` (GPU recommended)
- Linux (tested on Debian)

## Data layout

| Path | Purpose |
|------|---------|
| `~/packetpro-data/inbox/` | Drop images or PDFs here |
| `~/packetpro-data/transformed/` | Enhanced images (internal) |
| `~/packetpro-data/archive/` | Originals after OCR |
| `~/packetpro-data/failed/` | Files that failed processing |
| `~/packetpro-data/packetpro.db` | SQLite full-text index |

## Configuration

Copy and edit `config.default.yaml`, then pass `--config /path/to/config.yaml` to any command.

## Supported formats

jpg, jpeg, png, tiff, webp, bmp, pdf (multi-page)

## Install as services

```bash
./scripts/install.sh
systemctl --user enable --now packetpro-enhance packetpro-ocr packetpro-web
```

## License

MIT