#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Deploying Systemd Overrides..."
sudo mkdir -p /etc/systemd/system/docker.service.d/
sudo cp "$SCRIPT_DIR/../systemd/docker.service.d/override.conf" /etc/systemd/system/docker.service.d/override.conf
sudo chmod 644 /etc/systemd/system/docker.service.d/override.conf

echo "==> Reloading Systemd..."
sudo systemctl daemon-reload

echo "Done!"