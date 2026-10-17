#!/usr/bin/env bash
# Prepare (but NEVER ENABLE) the Kiwoom read-only service on an OCI Always Free VM.
# Execute on the user's own authorized VM only, after reviewing the script.
# No API request, payment, order, token request, cloud provisioning or key access.
set -euo pipefail

if [[ $# -ne 1 || "$1" != "--prepare" ]]; then
  echo "not_run: use --prepare explicitly on the reviewed VM"
  exit 2
fi
if [[ "$EUID" -ne 0 ]]; then
  echo "not_run: run with sudo on the intended OCI VM"
  exit 2
fi

REPO=/opt/korean-stock-screener
SECURE_ROOT=/etc/korean-stock-screener
PRIVATE_DIR=/var/lib/korean-stock-screener/kiwoom
SERVICE=/etc/systemd/system/korean-stock-kiwoom.service
TIMER=/etc/systemd/system/korean-stock-kiwoom.timer

for cmd in python3 git systemctl install; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "not_ready: missing prerequisite command"
    exit 1
  fi
done
if ! python3 -c 'import sys; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
  echo "not_ready: Python 3.11+ required"
  exit 1
fi
if [[ ! -f "$REPO/v3_kiwoom_auto_collector.py" ||
      ! -f "$REPO/deploy/kiwoom-investor.service.example" ||
      ! -f "$REPO/deploy/kiwoom-investor.timer.example" ]]; then
  echo "not_ready: expected reviewed repository checkout missing"
  exit 1
fi
if systemctl is-enabled --quiet korean-stock-kiwoom.timer 2>/dev/null; then
  echo "not_ready: collector timer already enabled; refusing to modify live service"
  exit 1
fi

# Dedicated non-login account. No SSH access for the collector itself.
if ! id kiwoom >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/korean-stock-screener \
    --shell /usr/sbin/nologin kiwoom
fi
install -d -m 0700 -o root -g root "$SECURE_ROOT"
install -d -m 0700 -o kiwoom -g kiwoom "$PRIVATE_DIR"

# Never overwrite existing credentials. Empty on first install and stay inert.
if [[ ! -e "$SECURE_ROOT/kiwoom.env" ]]; then
  install -m 0600 -o root -g root /dev/null "$SECURE_ROOT/kiwoom.env"
fi
if [[ -L "$SECURE_ROOT/kiwoom.env" ]]; then
  echo "not_ready: credential environment file must not be a symlink"
  exit 1
fi
chown root:root "$SECURE_ROOT/kiwoom.env"
chmod 0600 "$SECURE_ROOT/kiwoom.env"

# Templates install disabled, even when they already exist.
install -m 0644 -o root -g root \
  "$REPO/deploy/kiwoom-investor.service.example" "$SERVICE"
install -m 0644 -o root -g root \
  "$REPO/deploy/kiwoom-investor.timer.example" "$TIMER"
systemctl daemon-reload
echo "oci_preparation_complete_timer_not_enabled"
echo "next_steps: review cost, register VM outbound IP, enter secret file locally, test one read-only symbol, then enable timer manually"
