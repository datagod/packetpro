# PacketPro

Drop images or PDFs into a folder, enhance them for OCR, extract text with **Qwen2.5-VL** on Ollama, archive the originals, and search everything from a web UI.

## Quick start

```bash
cd packetpro
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

packetpro web
```

Open **http://127.0.0.1:8787/settings** and set your folder locations. Paths are saved to `~/.config/packetpro/config.yaml` (not in git).

Then start the workers:

```bash
packetpro enhance
packetpro ocr
```

Or use systemd:

```bash
./scripts/install.sh
systemctl --user enable --now packetpro-enhance packetpro-ocr packetpro-web
```

After changing folder locations in the web UI, restart `packetpro-enhance` and `packetpro-ocr`.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) with `qwen2.5vl:7b` (GPU recommended)
- Linux (tested on Debian)

## Configuration

| File | Purpose |
|------|---------|
| `config.default.yaml` | Committed defaults for OCR, enhancement, and web server settings |
| `~/.config/packetpro/config.yaml` | Your folder locations (configured via web UI) |
| `config.example.yaml` | Example of the user config format |

Override the user config path with `PACKETPRO_CONFIG=/path/to/config.yaml`.

## Windows network access

Point the **data root** in Settings at a Samba-shared folder (for example a path under your existing share). Then drop files into the `inbox` subfolder from Windows.

For a dedicated `\\HOST\PacketPro` share after configuring paths:

```bash
sudo ./scripts/setup-samba-share.sh
```

## Supported formats

jpg, jpeg, png, tiff, webp, bmp, pdf (multi-page)

## License

MIT