import json, os, time, threading
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
from zoneinfo import ZoneInfo

CFG_PATH         = "config.json"
MQTT_CONFIG_PATH = "mqtt.conf"
TLS_ENABLED      = False
QOS              = 1

TOPIC_GET       = "loravsb/169/config/get"
TOPIC_SET       = "loravsb/169/config/set"
TOPIC_REPORTED  = "loravsb/169/config/reported"
TOPIC_ACK       = "loravsb/169/config/ack"

STATUS = {
    "REPORTED_SENT":       11,
    "CONFIG_OVERWRITTEN":  12,
    "CONFIG_APPLIED":      13,
    "INVALID_PAYLOAD":     14,
    "WRITE_FAILED":        15,
    "INTERNAL_ERROR":      16,
}

lock = threading.RLock()

def now_iso():
    return datetime.now(ZoneInfo("Europe/Prague")).isoformat()

def load_mqtt_config():
    cfg = {}

    try:
        with open(MQTT_CONFIG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if key == "MQTT_BROKER":
                        cfg["broker"] = val
                    elif key == "MQTT_PORT":
                        cfg["port"] = int(val)
                    elif key == "MQTT_USERNAME":
                        cfg["username"] = val
                    elif key == "MQTT_PASSWORD":
                        cfg["password"] = val
    except FileNotFoundError:
        raise RuntimeError(f"MQTT config file not found: {MQTT_CONFIG_PATH}")
    except Exception as e:
        raise RuntimeError(f"Failed to parse MQTT config: {e}")

    required = ["broker", "port"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise RuntimeError(f"Missing required MQTT config keys: {', '.join(missing)}")

    return cfg

def load_cfg():
    if not os.path.exists(CFG_PATH):
        return {}
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def write_cfg(data: dict):
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, CFG_PATH)

def publish_reported(client, status_code, data=None):
    payload = {
        "timestamp": now_iso(),
        "status_code": status_code,
        "data": load_cfg() if data is None else data,
    }
    client.publish(TOPIC_REPORTED, json.dumps(payload, ensure_ascii=False), qos=QOS, retain=True)

def publish_ack(client, op, status_code, message="", error=None):
    payload = {
        "timestamp": now_iso(),
        "status_code": status_code,
        "message": message,
    }
    client.publish(TOPIC_ACK, json.dumps(payload, ensure_ascii=False), qos=QOS, retain=False)

# --- nově: rekurzivní merge (dst se mění na místě) ---
def deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst

# ---- MQTT ----
def init_mqtt_client():
    mqtt_cfg = load_mqtt_config()

    client = mqtt.Client(
        client_id="cfg-shadow-loravsb-169",
        userdata=None,
        protocol=mqtt.MQTTv311,
        transport="tcp",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )

    if mqtt_cfg.get("username") and mqtt_cfg.get("password"):
        client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])
    if TLS_ENABLED:
        client.tls_set()

    return client, mqtt_cfg

client, mqtt_cfg = init_mqtt_client()

def on_connect(cl, userdata, flags, rc, properties=None):
    if rc == 0:
        cl.subscribe([(TOPIC_GET, QOS), (TOPIC_SET, QOS)])
        publish_reported(cl, STATUS["REPORTED_SENT"])

def on_message(cl, userdata, msg):
    try:
        if msg.topic == TOPIC_GET:
            publish_reported(cl, STATUS["REPORTED_SENT"])
            return

        if msg.topic == TOPIC_SET:
            raw = msg.payload.decode("utf-8").strip()
            try:
                patch = json.loads(raw or "{}")
                if not isinstance(patch, dict) or len(patch) == 0:
                    raise ValueError("payload must be non-empty JSON object")
            except Exception:
                publish_ack(cl, "set", STATUS["INVALID_PAYLOAD"], "Invalid JSON payload")
                return

            try:
                with lock:
                    current = load_cfg()
                    updated = deep_merge(current if isinstance(current, dict) else {}, patch)
                    write_cfg(updated)
            except Exception:
                publish_ack(cl, "set", STATUS["WRITE_FAILED"], "Write failed")
                return

            publish_reported(cl, STATUS["CONFIG_OVERWRITTEN"], data=updated)
            publish_ack(cl, "set", STATUS["CONFIG_OVERWRITTEN"], "Config overwritten")
            return

    except Exception:
        publish_ack(cl, "unknown", STATUS["INTERNAL_ERROR"], "Unhandled error")

client.on_connect = on_connect
client.on_message = on_message

def main():
    client.connect(mqtt_cfg["broker"], mqtt_cfg["port"], keepalive=60)
    client.loop_start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
