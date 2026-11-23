#!/usr/bin/env bash
# Instala o auto-deploy do Bidfy na VPS (roda UMA vez, como root, na pasta do projeto).
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$DIR/deploy/auto-deploy.sh"

cat >/etc/systemd/system/bidfy-deploy.service <<UNIT
[Unit]
Description=Bidfy auto-deploy (git pull + docker compose)
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=/usr/bin/env bash $DIR/deploy/auto-deploy.sh
TimeoutStartSec=1500
UNIT

cat >/etc/systemd/system/bidfy-deploy.timer <<UNIT
[Unit]
Description=Confere o GitHub a cada minuto e atualiza o Bidfy

[Timer]
OnBootSec=60
OnUnitInactiveSec=60
Unit=bidfy-deploy.service

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now bidfy-deploy.timer
bash "$DIR/deploy/auto-deploy.sh" --forcar || true
echo
echo "Auto-deploy instalado. Status atual:"
cat "$DIR/uploads/_deploy/status.json"; echo
