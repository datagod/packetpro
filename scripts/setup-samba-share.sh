#!/usr/bin/env bash
# Optional: dedicated PacketPro Samba share (run with sudo).
set -euo pipefail

CONF="/etc/samba/smb.conf.d/packetpro.conf"
DATA_ROOT="/home/bill/TRANSFER/packetpro"

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

# Remove stray EOF line if a previous edit left one in smb.conf
sed -i '/^EOF$/d' /etc/samba/smb.conf

testparm -s >/dev/null
systemctl reload smbd
echo "Share ready at \\\\$(hostname)\\PacketPro"
echo "Drop files in: \\\\$(hostname)\\PacketPro\\inbox"