#    STATUS_DEFAULT                         = 0
#    STATUS_TX_WAIT                         = 1
#    STATUS_TX_TIMEOUT                      = 2
#    STATUS_TX_DONE                         = 3
#    STATUS_RX_WAIT                         = 4
#    STATUS_RX_CONTINUOUS                   = 5
#    STATUS_RX_TIMEOUT                      = 6
#    STATUS_RX_DONE                         = 7
#    STATUS_HEADER_ERR                      = 8
#    STATUS_CRC_ERR                         = 9
#    STATUS_CAD_WAIT                        = 10
#    STATUS_CAD_DETECTED                    = 11
#    STATUS_CAD_DONE                        = 12



from LoRaRF import SX127x
import time
import json
import binascii
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO

MQTT_CONFIG_PATH = "mqtt.conf"
MQTT_TOPIC_RX  = "loravsb/169/rx"
MQTT_TOPIC_TXH = "loravsb/169/tx/hex"
MQTT_TOPIC_TXA = "loravsb/169/tx/ascii"
MQTT_TOPIC_TX_ACK = "loravsb/169/tx/ack"
MQTT_TOPIC_CONFIG_ACK = "loravsb/169/config/ack"
MQTT_QOS       = 1
MQTT_KEEPALIVE = 60
MQTT_CLIENT_ID = "lora-gw-169mhz"

SPI_BUS = 0
SPI_CS  = 0
RST_PIN = 25
DIO0_PIN= 5
SPI_HZ  = 7_800_000

CONFIG_PATH = "config.json"
CONFIG_POLL_SEC = 30
WAIT_TIMEOUT_S  = 0.001
WAIT_SLEEP_S = 0.1
BOOT_TIMEOUT_S = 10

LoRa = None
mqtt_client = None
cfg = None
cfg_hash = None

tx_pending = False
tx_mode = None
tx_bytes_buf = b""


def now_iso():
    return datetime.now(ZoneInfo("Europe/Prague")).isoformat()

def ascii_safe_preview(b, max_len=256):
    out = []
    for x in b[:max_len]:
        if 32 <= x <= 126 or x in (9, 10, 13):
            out.append(chr(x))
        else:
            out.append(f"\\x{x:02x}")
    if len(b) > max_len:
        out.append("…")
    return "".join(out)

def compute_rssi(pkt_rssi_dbm, pkt_snr_db):
    if pkt_rssi_dbm is None:
        return None
    if pkt_snr_db is None or pkt_snr_db >= 0:
        return float(pkt_rssi_dbm)
    return float(pkt_rssi_dbm) + (float(pkt_snr_db) * 0.25)

def cfg_defaults():
    return {
        "freq_hz": 169437500,
        "sf": 12,
        "bw_hz": 41700,
        "cr_denom": 5,
        "ldro": True,
        "preamble": 8,
        "sync_word": 0x12,
        "crc_on": True,
        "header": "explicit",
        "implicit_len": 32,
        "invert_iq_rx": False,
        "invert_iq_tx": False,
        "tx_power_dbm": 17,
        "tx_pa": "pa_boost",
        "rx_gain_mode": "boosted",
        "rx_gain_level": "auto"
    }

def cfg_enforce_169(c):
    c["sf"] = 12
    c["cr_denom"] = 5
    c["ldro"] = True
    if c.get("bw_hz", 41700) >= 50_000:
        c["bw_hz"] = 41_700
    return c

def _dict_hash(d):
    s = json.dumps(d, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def cfg_load():
    base = cfg_defaults()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in base.keys():
            if k in data:
                base[k] = data[k]
    except Exception:
        pass
    return cfg_enforce_169(base)

def cfg_load_if_changed(prev_hash):
    c = cfg_load()
    h = _dict_hash(c)
    return (c, h, h != prev_hash)

def mqtt_config_load():
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

def map_rx_gain(mode, level):
    boost = SX127x.RX_GAIN_BOOSTED if str(mode).lower() == "boosted" else SX127x.RX_GAIN_POWER_SAVING
    if isinstance(level, str) and level.lower() == "auto":
        lvl = SX127x.RX_GAIN_AUTO
    else:
        try:
            v = int(level)
            v = 0 if v < 0 else 6 if v > 6 else v
            lvl = v
        except Exception:
            lvl = SX127x.RX_GAIN_AUTO
    return boost, lvl

def map_pa(pa_str):
    return SX127x.TX_POWER_PA_BOOST if str(pa_str).lower() == "pa_boost" else SX127x.TX_POWER_RFO


def lora_init():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(RST_PIN, GPIO.OUT, initial=GPIO.HIGH)
    time.sleep(0.01)

    l = SX127x()
    l.setSpi(SPI_BUS, SPI_CS, SPI_HZ)
    l.setPins(RST_PIN, DIO0_PIN)

    start_time = time.time()

    while True:
        if time.time() - start_time > BOOT_TIMEOUT_S:
            raise RuntimeError(f"Failed to initialize module")

        try:
            if l.begin():
                return l
        except Exception:
            pass
        time.sleep(0.1)

def lora_apply_common(c):
    LoRa.setFrequency(int(c["freq_hz"]))
    LoRa.setLoRaModulation(int(c["sf"]), int(c["bw_hz"]), int(c["cr_denom"]), bool(c["ldro"]))
    header = LoRa.HEADER_EXPLICIT if str(c.get("header", "explicit")).lower() == "explicit" else LoRa.HEADER_IMPLICIT
    payload_len = 255 if header == LoRa.HEADER_EXPLICIT else int(c.get("implicit_len", 32))
    LoRa.setLoRaPacket(header, int(c["preamble"]), payload_len, bool(c["crc_on"]), False)
    LoRa.setSyncWord(int(c["sync_word"]))
    LoRa.setTxPower(int(c["tx_power_dbm"]), map_pa(c["tx_pa"]))
    boost, lvl = map_rx_gain(c["rx_gain_mode"], c["rx_gain_level"])
    LoRa.setRxGain(boost, lvl)

def set_rx_iq(c):
    try:
        LoRa.setInvertIq(bool(c["invert_iq_rx"]))
    except Exception:
        pass

def set_tx_iq(c):
    try:
        LoRa.setInvertIq(bool(c["invert_iq_tx"]))
    except Exception:
        pass

def lora_soft_restart_and_apply(c):
    try:
        LoRa.restart()
    except Exception:
        try:
            LoRa.sleep()
            time.sleep(0.02)
            LoRa.wake()
        except Exception:
            pass
    time.sleep(0.02)

    try:
        LoRa.init()
    except Exception:
        LoRa.begin()
    lora_apply_common(c)
    set_rx_iq(c)
    time.sleep(0.02)
    try:
        LoRa.request()
    except Exception:
        pass


def mqtt_init():
    mqtt_cfg = mqtt_config_load()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
    if mqtt_cfg.get("username") and mqtt_cfg.get("password"):
        c.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])
    c.on_message = on_mqtt_message
    c.connect(mqtt_cfg["broker"], mqtt_cfg["port"], keepalive=MQTT_KEEPALIVE)
    c.subscribe([(MQTT_TOPIC_TXH, MQTT_QOS),
                 (MQTT_TOPIC_TXA, MQTT_QOS)])
    c.loop_start()
    return c

def mqtt_publish(topic, obj):
    mqtt_client.publish(topic, json.dumps(obj, ensure_ascii=False), qos=MQTT_QOS, retain=False)

def on_mqtt_message(client, userdata, msg):
    global tx_pending, tx_mode, tx_bytes_buf
    if msg.topic == MQTT_TOPIC_TXH:
        try:
            data = binascii.unhexlify(msg.payload.decode("utf-8").strip())
        except Exception:
            mqtt_publish(MQTT_TOPIC_TX_ACK, {"timestamp": now_iso(), "status_code": None})
            return
        tx_mode = "hex"
        tx_bytes_buf = data
        tx_pending = True
    elif msg.topic == MQTT_TOPIC_TXA:
        tx_mode = "ascii"
        tx_bytes_buf = bytes(msg.payload)
        tx_pending = True


def rx_handle_if_ready():
    if LoRa.available() <= 0:
        return
    buf = bytearray()
    while LoRa.available() > 0:
        buf.append(LoRa.read())
    data = bytes(buf)

    try:
        rssi = float(LoRa.packetRssi())
    except Exception:
        rssi = None
    try:
        snr_bind = LoRa.snr()
        if snr_bind is None:
            snr = None
        else:
            q = int(round(float(snr_bind) * 4.0)) & 0xFF
            if q >= 128:
                q -= 256
            snr = q / 4.0
    except Exception:
        snr = None
    try:
        st = LoRa.status()
        st = int(st) if isinstance(st, int) else st
    except Exception:
        st = None

    mqtt_publish(MQTT_TOPIC_RX, {
        "timestamp": now_iso(),
        "status_code": f"{int(st):02d}",
        "rssi": compute_rssi(rssi, snr),
        "snr": snr,
        "payload_hex": binascii.hexlify(data).decode("ascii"),
        "payload_ascii": ascii_safe_preview(data)
    })

def do_tx_now(mode, data_bytes):
    try:
        set_tx_iq(cfg)
        LoRa.beginPacket()
        for b in data_bytes:
            LoRa.write(b)
        LoRa.endPacket()
        while True:
             if LoRa.wait(WAIT_TIMEOUT_S):
                 try:
                     st = LoRa.status()
                 except Exception:
                     st = None
                 if st is not None:
                     st = int(st) if isinstance(st, int) else st
                     break

             time.sleep(WAIT_SLEEP_S)

    except Exception:
        st = None
    finally:
        set_rx_iq(cfg)
        try:
            LoRa.request()
        except Exception:
            pass

    mqtt_publish(MQTT_TOPIC_TX_ACK, {
        "timestamp": now_iso(),
        "status_code": f"{int(st):02d}",
        "transmit_time": round(LoRa.transmitTime(), 1),
    })


def main():
    global LoRa, mqtt_client, cfg, cfg_hash, tx_pending, tx_mode, tx_bytes_buf

    cfg = cfg_load()
    cfg_hash = _dict_hash(cfg)

    mqtt_client = mqtt_init()
    LoRa = lora_init()
    lora_apply_common(cfg)
    set_rx_iq(cfg)
    try:
        LoRa.request()
    except Exception:
        pass

    last_cfg_check = 0.0

    try:
        while True:
            if tx_pending:
                mode = tx_mode
                data = tx_bytes_buf
                tx_pending = False
                do_tx_now(mode, data)

            try:
                LoRa.request()
            except Exception:
                pass

            ok = LoRa.wait(WAIT_TIMEOUT_S)

            if ok and LoRa.available() > 0:
                rx_handle_if_ready()
            else:
                time.sleep(WAIT_SLEEP_S)

            now = time.time()
            if now - last_cfg_check >= CONFIG_POLL_SEC:
                last_cfg_check = now
                newc, newh, changed = cfg_load_if_changed(cfg_hash)
                if changed:
                    cfg = newc
                    cfg_hash = newh
                    lora_soft_restart_and_apply(cfg)

                    mqtt_publish(MQTT_TOPIC_CONFIG_ACK, {
                        "timestamp": now_iso(),
                        "status_code": 13,
                    })

    except KeyboardInterrupt:
        pass
    finally:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            pass

if __name__ == "__main__":
    main()
