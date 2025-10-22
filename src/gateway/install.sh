#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/lysek01/LoRa-169.git"
BRANCH="main"

APP_DIR="/opt/loravsb"
VENV_DIR="$APP_DIR/venv"

GATEWAY_ENTRY="src/gateway/gateway.py"
CONFIG_ENTRY="src/gateway/lora_config.py"

GW_SERVICE="loravsb-gateway.service"
CFG_SERVICE="loravsb-config.service"

RUN_AS_USER="admin"
RUN_AS_GROUP="admin"

PYTHON_BIN="python3"
PIP_BIN="pip3"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root $0"; exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y git "$PYTHON_BIN" python3-venv "$PIP_BIN"
fi

mkdir -p "$APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch --all --prune
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

"$PYTHON_BIN" -m venv "$VENV_DIR" 2>/dev/null || true
source "$VENV_DIR/bin/activate"
pip install --upgrade pip wheel

if [[ -f "$APP_DIR/requirements.txt" ]]; then
  pip install -r "$APP_DIR/requirements.txt"
else
  pip install "paho-mqtt>=2,<3" || true
fi

GW_PATH="$APP_DIR/$GATEWAY_ENTRY"
CFG_PATH="$APP_DIR/$CONFIG_ENTRY"
[[ -f "$GW_PATH" ]] || { echo "Script not found: $GW_PATH"; exit 1; }
[[ -f "$CFG_PATH" ]] || { echo "Script not found:  $CFG_PATH"; exit 1; }

chown -R "$RUN_AS_USER:$RUN_AS_GROUP" "$APP_DIR"

# ======= SYSTEMD: GATEWAY =======
cat > "/etc/systemd/system/$GW_SERVICE" <<EOF
[Unit]
Description=LoRa VSB Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_AS_USER
Group=$RUN_AS_GROUP
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/python $GW_PATH
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# ======= SYSTEMD: CONFIG =======
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
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$GW_SERVICE" "$CFG_SERVICE"
systemctl restart "$GW_SERVICE" "$CFG_SERVICE"


echo "Done"
