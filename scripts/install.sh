#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -e "$ROOT"

mkdir -p "$UNIT_DIR"
for svc in enhance ocr web; do
  sed "s|@INSTALL_ROOT@|$ROOT|g" "$ROOT/systemd/packetpro-${svc}.service" \
    > "$UNIT_DIR/packetpro-${svc}.service"
done

systemctl --user daemon-reload
echo "Installed PacketPro into $VENV"
echo "Enable services with:"
echo "  systemctl --user enable --now packetpro-enhance packetpro-ocr packetpro-web"