#!/usr/bin/env bash
# Optional: dedicated PacketPro Samba share (run with sudo).
# Reads data_root from ~/.config/packetpro/config.yaml
set -euo pipefail

USER_CONFIG="${PACKETPRO_CONFIG:-$HOME/.config/packetpro/config.yaml}"
if [[ ! -f "$USER_CONFIG" ]]; then
  echo "No PacketPro config found at $USER_CONFIG"
  echo "Configure folder locations in the web UI first: http://127.0.0.1:8787/settings"
  exit 1
fi

DATA_ROOT="$(python3 - <<PY
import yaml
from pathlib import Path
raw = yaml.safe_load(Path("$USER_CONFIG").read_text()) or {}
print(raw.get("data_root", ""))
PY
)"

if [[ -z "$DATA_ROOT" ]]; then
  echo "data_root is not set in $USER_CONFIG"
  exit 1
fi

CONF="/etc/samba/smb.conf.d/packetpro.conf"
mkdir -p /etc/samba/smb.conf.d

cat >"$CONF" <<EOF
[PacketPro]
   comment = PacketPro document drop folders
   path = ${DATA_ROOT}
   browseable = yes
   writable = yes
   guest ok = no
   valid users = bill
   create mask = 0664
   directory mask = 0775
   force user = bill
   force group = bill
EOF

sed -i '/^EOF$/d' /etc/samba/smb.conf
testparm -s >/dev/null
systemctl reload smbd
echo "Share ready at \\\\$(hostname)\\PacketPro"
echo "Drop files in: \\\\$(hostname)\\PacketPro\\inbox"