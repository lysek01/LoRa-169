#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
VENV_DIR="$APP_DIR/venv"

GATEWAY_ENTRY="lora_gateway.py"
CONFIG_ENTRY="lora_config.py"

GW_SERVICE="loravsb-gateway.service"
CFG_SERVICE="loravsb-config.service"

RUN_AS_USER="admin"
RUN_AS_GROUP="admin"

PYTHON_BIN="python3"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0"; exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y git "$PYTHON_BIN" python3-venv
fi

# ======= venv + pip =======
"$PYTHON_BIN" -m venv "$VENV_DIR" 2>/dev/null || true
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip wheel setuptools

if [[ -f "$APP_DIR/requirements.txt" ]]; then
  pip install -r "$APP_DIR/requirements.txt"
else
  pip install "paho-mqtt>=2,<3" || true
fi

GW_PATH="$APP_DIR/$GATEWAY_ENTRY"
CFG_PATH="$APP_DIR/$CONFIG_ENTRY"
[[ -f "$GW_PATH" ]] || { echo "Script not found: $GW_PATH"; exit 1; }
[[ -f "$CFG_PATH" ]] || { echo "Script not found: $CFG_PATH"; exit 1; }

chown -R "$RUN_AS_USER:$RUN_AS_GROUP" "$APP_DIR"

# ======= systemd: gateway =======
cat > "/etc/systemd/system/$GW_SERVICE" <<EOF
[Unit]
Description=LoRa VSB Gateway
After=network-online.target sysinit.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_AS_USER
Group=$RUN_AS_GROUP
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/python $GW_PATH

# Restart policy
Restart=on-failure
RestartSec=5
StartLimitInterval=60
StartLimitBurst=3

# Timeouts
TimeoutStartSec=20
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
EOF

# ======= systemd: config =======
cat > "/etc/systemd/system/$CFG_SERVICE" <<EOF
[Unit]
Description=LoRa VSB Config (MQTT)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_AS_USER
Group=$RUN_AS_GROUP
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/python $CFG_PATH

# Restart policy (mírnější než gateway - žádný hardware)
Restart=on-failure
RestartSec=3
StartLimitInterval=60
StartLimitBurst=5

# Timeouts
TimeoutStartSec=10
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF

# ======= enable + start =======
systemctl daemon-reload
systemctl enable "$GW_SERVICE" "$CFG_SERVICE"
systemctl restart "$GW_SERVICE" "$CFG_SERVICE"

echo "Done"
