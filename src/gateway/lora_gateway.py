from __future__ import annotations
from typing import Optional, Dict, Any, Tuple

from LoRaRF import SX127x
import time
import json
import binascii
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import logging
import os

# LoRa Status Codes
STATUS_DEFAULT = 0
STATUS_TX_WAIT = 1
STATUS_TX_TIMEOUT = 2
STATUS_TX_DONE = 3
STATUS_RX_WAIT = 4
STATUS_RX_CONTINUOUS = 5
STATUS_RX_TIMEOUT = 6
STATUS_RX_DONE = 7
STATUS_HEADER_ERR = 8
STATUS_CRC_ERR = 9
STATUS_CAD_WAIT = 10
STATUS_CAD_DETECTED = 11
STATUS_CAD_DONE = 12

STATUS_NAMES = {
    STATUS_DEFAULT: "DEFAULT",
    STATUS_TX_WAIT: "TX_WAIT",
    STATUS_TX_TIMEOUT: "TX_TIMEOUT",
    STATUS_TX_DONE: "TX_DONE",
    STATUS_RX_WAIT: "RX_WAIT",
    STATUS_RX_CONTINUOUS: "RX_CONTINUOUS",
    STATUS_RX_TIMEOUT: "RX_TIMEOUT",
    STATUS_RX_DONE: "RX_DONE",
    STATUS_HEADER_ERR: "HEADER_ERR",
    STATUS_CRC_ERR: "CRC_ERR",
    STATUS_CAD_WAIT: "CAD_WAIT",
    STATUS_CAD_DETECTED: "CAD_DETECTED",
    STATUS_CAD_DONE: "CAD_DONE"
}

LOG_DIR = "logs"
LOG_FILE = None

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

RX_RATE_WINDOW = 1.0
RX_RATE_THRESHOLD_HIGH = 20
RX_RATE_THRESHOLD_EXTREME = 100
RX_SAME_PAYLOAD_THRESHOLD = 5
RX_SAME_STATUS_THRESHOLD = 5
RX_SAME_PAYLOAD_EXTREME = 20
RECOVERY_SLEEP_S = 0.05
TX_TIMEOUT_S = 60

LoRa = None
mqtt_client = None
cfg = None
cfg_hash = None

tx_pending = False
tx_mode = None
tx_bytes_buf = b""

rx_events = []
last_payload_hash = None
same_payload_count = 0
last_status = None
same_status_count = 0
last_rx_time = 0.0



# =============================================================================
# LOGGING AND UTILITY FUNCTIONS
# =============================================================================

def setup_logging():
    global LOG_FILE
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    timestamp = datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y%m%d_%H%M%S")
    LOG_FILE = os.path.join(LOG_DIR, f"lora_gateway_{timestamp}.log")

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8')
        ]
    )
    logging.info(f"=== LoRa Gateway Started ===")
    logging.info(f"Log file: {LOG_FILE}")


def now_iso() -> str:
    """Get current ISO timestamp in Europe/Prague timezone.

    Returns:
        ISO 8601 formatted timestamp string.
    """
    return datetime.now(ZoneInfo("Europe/Prague")).isoformat()


def ascii_safe_preview(b: bytes, max_len: int = 256) -> str:
    """Convert bytes to ASCII-safe preview string.

    Non-printable characters are shown as hex escapes (\\xNN).
    Long strings are truncated with ellipsis.

    Args:
        b: Bytes to preview.
        max_len: Maximum length before truncation.

    Returns:
        ASCII-safe preview string.
    """
    out = []
    for x in b[:max_len]:
        if 32 <= x <= 126 or x in (9, 10, 13):
            out.append(chr(x))
        else:
            out.append(f"\\x{x:02x}")
    if len(b) > max_len:
        out.append("…")
    return "".join(out)

def compute_rssi(pkt_rssi_dbm: Optional[float], pkt_snr_db: Optional[float]) -> Optional[float]:
    """Compute corrected RSSI value considering SNR.

    When SNR is negative, RSSI needs correction as per LoRa specifications.

    Args:
        pkt_rssi_dbm: Raw RSSI value in dBm.
        pkt_snr_db: SNR value in dB.

    Returns:
        Corrected RSSI value in dBm, or None if input is None.
    """
    if pkt_rssi_dbm is None:
        return None
    if pkt_snr_db is None or pkt_snr_db >= 0:
        return float(pkt_rssi_dbm)
    return float(pkt_rssi_dbm) + (float(pkt_snr_db) * 0.25)


# =============================================================================
# CONFIGURATION MANAGEMENT
# =============================================================================

def cfg_defaults() -> Dict[str, Any]:
    """Get default LoRa configuration for 169 MHz ISM band.

    Returns:
        Dictionary with default configuration values.
    """
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
    logging.debug(f"Loading config from {CONFIG_PATH}")
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in base.keys():
            if k in data:
                base[k] = data[k]
        logging.info(f"Config loaded successfully: {json.dumps(base, indent=2)}")
    except Exception as e:
        logging.warning(f"Failed to load config file, using defaults: {e}")
    return cfg_enforce_169(base)

def cfg_load_if_changed(prev_hash):
    c = cfg_load()
    h = _dict_hash(c)
    return (c, h, h != prev_hash)

def mqtt_config_load():
    cfg = {}
    logging.debug(f"Loading MQTT config from {MQTT_CONFIG_PATH}")

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
        logging.error(f"MQTT config file not found: {MQTT_CONFIG_PATH}")
        raise RuntimeError(f"MQTT config file not found: {MQTT_CONFIG_PATH}")
    except Exception as e:
        logging.error(f"Failed to parse MQTT config: {e}")
        raise RuntimeError(f"Failed to parse MQTT config: {e}")

    required = ["broker", "port"]
    missing = [k for k in required if k not in cfg]
    if missing:
        logging.error(f"Missing required MQTT config keys: {', '.join(missing)}")
        raise RuntimeError(f"Missing required MQTT config keys: {', '.join(missing)}")

    logging.info(f"MQTT config loaded: broker={cfg['broker']}, port={cfg['port']}")
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


# =============================================================================
# LORA PACKET METADATA HELPERS
# =============================================================================

def read_packet_rssi() -> Optional[float]:
    """Read RSSI for the last received packet.

    Returns:
        RSSI value in dBm, or None if reading failed.
    """
    try:
        rssi = float(LoRa.packetRssi())
        logging.debug(f"Packet RSSI: {rssi} dBm")
        return rssi
    except Exception as e:
        logging.debug(f"Failed to read RSSI: {e}")
        return None


def read_packet_snr() -> Optional[float]:
    """Read SNR for the last received packet.

    The raw SNR value from the chip is converted to proper dB scale.

    Returns:
        SNR value in dB, or None if reading failed.
    """
    try:
        snr_bind = LoRa.snr()
        if snr_bind is None:
            return None

        # Convert to proper SNR value
        q = int(round(float(snr_bind) * 4.0)) & 0xFF
        if q >= 128:
            q -= 256
        snr = q / 4.0
        logging.debug(f"Packet SNR: {snr} dB")
        return snr
    except Exception as e:
        logging.debug(f"Failed to read SNR: {e}")
        return None


def get_status_name(status_code: Optional[int]) -> str:
    """Get human-readable name for LoRa status code.

    Args:
        status_code: Numeric status code (0-12).

    Returns:
        Status name string (e.g., "RX_DONE", "CRC_ERR").
    """
    return STATUS_NAMES.get(status_code, "UNKNOWN")


def format_status_code(status_code: Optional[int]) -> str:
    """Format status code as 2-digit decimal string.

    Args:
        status_code: Numeric status code.

    Returns:
        Formatted string (e.g., "07", "09", "FF" for None).
    """
    return f"{int(status_code):02d}" if status_code is not None else "FF"


# =============================================================================
# LORA MODULE INITIALIZATION AND CONFIGURATION
# =============================================================================

def lora_init():
    logging.info("Initializing LoRa module...")
    logging.debug(f"GPIO setup: RST_PIN={RST_PIN}, DIO0_PIN={DIO0_PIN}")
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(DIO0_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(RST_PIN, GPIO.OUT, initial=GPIO.HIGH)
    time.sleep(0.01)

    l = SX127x()
    logging.debug(f"SPI setup: BUS={SPI_BUS}, CS={SPI_CS}, HZ={SPI_HZ}")
    l.setSpi(SPI_BUS, SPI_CS, SPI_HZ)
    l.setPins(RST_PIN, DIO0_PIN)

    start_time = time.time()

    while True:
        if time.time() - start_time > BOOT_TIMEOUT_S:
            logging.error(f"Failed to initialize module after {BOOT_TIMEOUT_S}s timeout")
            raise RuntimeError(f"Failed to initialize module")

        try:
            if l.begin():
                logging.info("LoRa module initialized successfully")
                return l
        except Exception as e:
            logging.debug(f"LoRa init attempt failed: {e}")
            pass
        time.sleep(0.1)

def lora_apply_common(c):
    logging.debug("Applying LoRa configuration...")
    logging.debug(f"  Frequency: {c['freq_hz']} Hz")
    LoRa.setFrequency(int(c["freq_hz"]))
    logging.debug(f"  Modulation: SF={c['sf']}, BW={c['bw_hz']} Hz, CR=4/{c['cr_denom']}, LDRO={c['ldro']}")
    LoRa.setLoRaModulation(int(c["sf"]), int(c["bw_hz"]), int(c["cr_denom"]), bool(c["ldro"]))
    header = LoRa.HEADER_EXPLICIT if str(c.get("header", "explicit")).lower() == "explicit" else LoRa.HEADER_IMPLICIT
    payload_len = 255 if header == LoRa.HEADER_EXPLICIT else int(c.get("implicit_len", 32))
    logging.debug(f"  Packet: header={'explicit' if header == LoRa.HEADER_EXPLICIT else 'implicit'}, preamble={c['preamble']}, len={payload_len}, crc={c['crc_on']}")
    LoRa.setLoRaPacket(header, int(c["preamble"]), payload_len, bool(c["crc_on"]), False)
    logging.debug(f"  Sync word: 0x{c['sync_word']:02X}")
    LoRa.setSyncWord(int(c["sync_word"]))
    logging.debug(f"  TX power: {c['tx_power_dbm']} dBm, PA={c['tx_pa']}")
    LoRa.setTxPower(int(c["tx_power_dbm"]), map_pa(c["tx_pa"]))
    boost, lvl = map_rx_gain(c["rx_gain_mode"], c["rx_gain_level"])
    logging.debug(f"  RX gain: mode={c['rx_gain_mode']}, level={c['rx_gain_level']}")
    LoRa.setRxGain(boost, lvl)
    logging.info("LoRa configuration applied successfully")

def set_rx_iq(c):
    try:
        invert = bool(c["invert_iq_rx"])
        LoRa.setInvertIq(invert)
        logging.debug(f"Set RX IQ invert: {invert}")
    except Exception as e:
        logging.warning(f"Failed to set RX IQ invert: {e}")
        pass

def set_tx_iq(c):
    try:
        invert = bool(c["invert_iq_tx"])
        LoRa.setInvertIq(invert)
        logging.debug(f"Set TX IQ invert: {invert}")
    except Exception as e:
        logging.warning(f"Failed to set TX IQ invert: {e}")
        pass

def lora_soft_restart_and_apply(c):
    """Perform LoRa module soft restart and reapply configuration."""
    logging.warning("Performing LoRa soft restart...")
    try:
        LoRa.reset()
        logging.debug("LoRa reset() successful - hardware reset completed")
    except Exception as e:
        logging.error(f"LoRa reset() failed: {e}, trying sleep/wake fallback")
        try:
            LoRa.sleep()
            time.sleep(0.02)
            LoRa.wake()
            logging.debug("LoRa sleep/wake successful")
        except Exception as e2:
            logging.error(f"LoRa sleep/wake failed: {e2}")
            pass

    time.sleep(0.02)
    LoRa.begin()
    logging.debug("LoRa begin() successful - module reinitialized")

    lora_apply_common(c)
    set_rx_iq(c)
    time.sleep(0.02)
    try:
        LoRa.request(LoRa.RX_CONTINUOUS)
        logging.info("LoRa soft restart completed, back to RX_CONTINUOUS mode")
    except Exception as e:
        logging.error(f"Failed to set RX_CONTINUOUS after restart: {e}")
        pass


# =============================================================================
# MQTT CLIENT INITIALIZATION AND HANDLERS
# =============================================================================

def mqtt_init():
    logging.info("Initializing MQTT client...")
    mqtt_cfg = mqtt_config_load()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
    if mqtt_cfg.get("username") and mqtt_cfg.get("password"):
        c.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])
        logging.debug("MQTT authentication configured")
    c.on_message = on_mqtt_message
    logging.info(f"Connecting to MQTT broker {mqtt_cfg['broker']}:{mqtt_cfg['port']}")
    c.connect(mqtt_cfg["broker"], mqtt_cfg["port"], keepalive=MQTT_KEEPALIVE)
    logging.debug(f"Subscribing to topics: {MQTT_TOPIC_TXH}, {MQTT_TOPIC_TXA}")
    c.subscribe([(MQTT_TOPIC_TXH, MQTT_QOS),
                 (MQTT_TOPIC_TXA, MQTT_QOS)])
    c.loop_start()
    logging.info("MQTT client initialized and running")
    return c

def mqtt_publish(topic, obj):
    payload = json.dumps(obj, ensure_ascii=False)
    logging.debug(f"MQTT publish to {topic}: {payload}")
    mqtt_client.publish(topic, payload, qos=MQTT_QOS, retain=False)

def on_mqtt_message(client, userdata, msg):
    global tx_pending, tx_mode, tx_bytes_buf
    logging.info(f"MQTT message received on topic: {msg.topic}")
    if msg.topic == MQTT_TOPIC_TXH:
        try:
            payload_str = msg.payload.decode("utf-8").strip()
            logging.debug(f"TX HEX payload: {payload_str}")
            data = binascii.unhexlify(payload_str)
            logging.info(f"TX HEX request: {len(data)} bytes")
        except Exception as e:
            logging.error(f"Failed to decode HEX payload: {e}")
            mqtt_publish(MQTT_TOPIC_TX_ACK, {"timestamp": now_iso(), "status_code": None})
            return
        tx_mode = "hex"
        tx_bytes_buf = data
        tx_pending = True
    elif msg.topic == MQTT_TOPIC_TXA:
        logging.debug(f"TX ASCII payload: {msg.payload[:100]}")
        logging.info(f"TX ASCII request: {len(msg.payload)} bytes")
        tx_mode = "ascii"
        tx_bytes_buf = bytes(msg.payload)
        tx_pending = True



# =============================================================================
# RX LOOP DETECTION AND RECOVERY
# =============================================================================

def detect_rx_loop(now: float, payload_hash: bytes, st: Optional[int]) -> bool:
    """Detect infinite RX loops based on rate, payload, and status.

    Uses multiple detection heuristics:
    - High packet rate (>20 packets/sec)
    - Extreme packet rate (>100 packets/sec)
    - Repeated identical payloads
    - Repeated status codes

    Args:
        now: Current timestamp.
        payload_hash: MD5 hash of received payload.
        st: LoRa status code.

    Returns:
        True if loop detected, False otherwise.
    """
    global rx_events, last_payload_hash, same_payload_count
    global last_status, same_status_count, last_rx_time

    rx_events.append(now)
    rx_events[:] = [t for t in rx_events if now - t < RX_RATE_WINDOW]

    # Reset counters if too much time passed since last RX (outside the rate window)
    if last_rx_time > 0 and (now - last_rx_time) > RX_RATE_WINDOW:
        same_payload_count = 0
        same_status_count = 0
        logging.debug(f"Counters reset due to time gap: {now - last_rx_time:.2f}s")

    last_rx_time = now

    if payload_hash == last_payload_hash:
        same_payload_count += 1
    else:
        same_payload_count = 0
        last_payload_hash = payload_hash

    if st == last_status:
        same_status_count += 1
    else:
        same_status_count = 0
        last_status = st

    logging.debug(f"Loop detection: events={len(rx_events)}, same_payload={same_payload_count}, same_status={same_status_count}")

    if (len(rx_events) > RX_RATE_THRESHOLD_HIGH and
        same_payload_count > RX_SAME_PAYLOAD_THRESHOLD and
        same_status_count > RX_SAME_STATUS_THRESHOLD):
        logging.warning(f"Loop detected: high rate ({len(rx_events)} events) + same payload ({same_payload_count}) + same status ({same_status_count})")
        return True

    if len(rx_events) > RX_RATE_THRESHOLD_EXTREME:
        logging.warning(f"Loop detected: extreme rate ({len(rx_events)} events in {RX_RATE_WINDOW}s window)")
        return True

    if same_payload_count > RX_SAME_PAYLOAD_EXTREME:
        logging.warning(f"Loop detected: extreme same payload count ({same_payload_count})")
        return True

    return False


def perform_rx_recovery() -> None:
    """Perform recovery when RX loop is detected.

    Recovery process:
    1. Perform LoRa module soft restart
    2. Reapply configuration
    3. Reset all loop detection counters
    4. Return to RX_CONTINUOUS mode
    """
    global rx_events, same_payload_count, same_status_count, last_rx_time

    logging.warning("!!! PERFORMING RX RECOVERY DUE TO LOOP DETECTION !!!")
    try:
        lora_soft_restart_and_apply(cfg)
        logging.info("RX recovery completed successfully")
    except Exception as e:
        logging.error(f"RX recovery failed: {e}")
        pass

    rx_events.clear()
    same_payload_count = 0
    same_status_count = 0
    last_rx_time = 0.0
    logging.debug("Recovery counters reset")


# =============================================================================
# RX PACKET HANDLING
# =============================================================================

def rx_handle_if_ready() -> None:
    """Handle incoming RX data if available.

    Processing flow:
    1. Check if data is available
    2. Read all bytes from FIFO
    3. Check LoRa status
    4. Handle CRC_ERR (immediate restart)
    5. Purge FIFO buffer
    6. Detect potential RX loops
    7. Read packet metadata (RSSI/SNR)
    8. Publish packet to MQTT

    Special cases:
    - CRC_ERR: Triggers immediate module restart
    - Purge failure: Triggers module restart
    - Loop detection: Triggers recovery procedure
    """
    available = LoRa.available()
    if available <= 0:
        return

    logging.debug(f"RX data available: {available} bytes")

    buf = bytearray()
    while LoRa.available() > 0:
        buf.append(LoRa.read())
    data = bytes(buf)
    logging.debug(f"Read {len(data)} bytes from LoRa")

    try:
        st = LoRa.status()
        st = int(st) if isinstance(st, int) else st
        logging.debug(f"LoRa status: {st}")
    except Exception as e:
        logging.warning(f"Failed to read LoRa status: {e}")
        st = None

    # Check for CRC_ERR BEFORE purging - this state requires full restart
    if st == STATUS_CRC_ERR:
        logging.error(f"!!! RX status CRC_ERR detected - buffer stuck, triggering immediate restart !!!")

        # Read packet metadata for error tracking
        rssi = read_packet_rssi()
        snr = read_packet_snr()

        # Publish CRC error to MQTT with the corrupted data for analysis
        mqtt_publish(MQTT_TOPIC_RX, {
            "timestamp": now_iso(),
            "status_code": format_status_code(st),
            "rssi": compute_rssi(rssi, snr),
            "snr": snr,
            "payload_hex": binascii.hexlify(data).decode("ascii") if len(data) > 0 else "",
            "payload_ascii": ascii_safe_preview(data) if len(data) > 0 else "",
        })

        LoRa._payloadTxRx = 0
        lora_soft_restart_and_apply(cfg)
        return  # Skip further processing after restart

    purge_success = False
    try:
        LoRa.purge()
        purge_success = True
        logging.debug("LoRa.purge() successful")
    except Exception as e:
        logging.error(f"!!! LoRa.purge() FAILED: {e} - triggering soft restart !!!")
        LoRa._payloadTxRx = 0
        lora_soft_restart_and_apply(cfg)
        return

    now = time.time()
    payload_hash = hashlib.md5(data).digest()[:4] if len(data) > 0 else b"\x00\x00\x00\x00"

    loop_detected = detect_rx_loop(now, payload_hash, st)

    if loop_detected:
        perform_rx_recovery()
        return

    # Read packet metadata
    rssi = read_packet_rssi()
    snr = read_packet_snr()

    # Log packet information
    status_name = get_status_name(st)
    status_str = format_status_code(st)
    logging.info(f"RX packet: status={status_str} ({status_name}), len={len(data)}, rssi={rssi}, snr={snr}, purge_ok={purge_success}")

    if len(data) > 0:
        logging.debug(f"  Payload HEX: {binascii.hexlify(data).decode('ascii')[:100]}...")
        logging.debug(f"  Payload ASCII: {ascii_safe_preview(data, 50)}")

    # Publish to MQTT
    mqtt_publish(MQTT_TOPIC_RX, {
        "timestamp": now_iso(),
        "status_code": format_status_code(st),
        "rssi": compute_rssi(rssi, snr),
        "snr": snr,
        "payload_hex": binascii.hexlify(data).decode("ascii") if len(data) > 0 else "",
        "payload_ascii": ascii_safe_preview(data) if len(data) > 0 else ""
    })


# =============================================================================
# TX PACKET HANDLING
# =============================================================================

def do_tx_now(mode: str, data_bytes: bytes) -> None:
    """Transmit data packet and handle TX completion.

    Transmit flow:
    1. Set TX IQ configuration
    2. Begin packet transmission
    3. Write data bytes
    4. End packet and wait for completion
    5. Handle TX timeout
    6. Return to RX_CONTINUOUS mode
    7. Read TX metadata (transmit time)
    8. Publish TX ACK to MQTT

    Special cases:
    - TX timeout: Status set to TX_TIMEOUT
    - CRC_ERR on TX: Triggers module restart
    - Exceptions: Logged and handled gracefully

    Args:
        mode: Transmission mode ("hex" or "ascii").
        data_bytes: Data to transmit.
    """
    logging.info(f"Starting TX: mode={mode}, len={len(data_bytes)} bytes")
    logging.debug(f"TX data HEX: {binascii.hexlify(data_bytes).decode('ascii')}")
    st = None
    try:
        set_tx_iq(cfg)
        logging.debug("Begin packet transmission")
        LoRa.beginPacket()
        for b in data_bytes:
            LoRa.write(b)
        LoRa.endPacket()
        logging.debug("Packet queued, waiting for TX completion")
        tx_start = time.time()
        while True:
             if LoRa.wait(WAIT_TIMEOUT_S):
                 try:
                     st = LoRa.status()
                 except Exception as e:
                     logging.warning(f"Failed to read TX status: {e}")
                     st = None
                 if st is not None:
                     st = int(st) if isinstance(st, int) else st
                     logging.debug(f"TX status received: {st}")
                     break

             if time.time() - tx_start > TX_TIMEOUT_S:
                 logging.error(f"TX timeout after {TX_TIMEOUT_S}s")
                 st = STATUS_TX_TIMEOUT
                 break

             time.sleep(WAIT_SLEEP_S)

    except Exception as e:
        logging.error(f"TX exception: {e}")
        st = None
    finally:
        set_rx_iq(cfg)
        try:
            LoRa.request(LoRa.RX_CONTINUOUS)
            logging.debug("Returned to RX_CONTINUOUS mode after TX")
        except Exception as e:
            logging.error(f"Failed to return to RX_CONTINUOUS: {e}")
            pass

    try:
        tx_time = round(LoRa.transmitTime(), 1)
        logging.debug(f"TX time: {tx_time} ms")
    except Exception as e:
        logging.warning(f"Failed to read TX time: {e}")
        tx_time = 0.0

    # Log TX completion
    status_name = get_status_name(st)
    status_str = format_status_code(st)
    logging.info(f"TX completed: status={status_str} ({status_name}), time={tx_time}ms")

    # If TX returned CRC_ERR status, perform soft restart to recover
    if st == STATUS_CRC_ERR:
        logging.error("TX returned CRC_ERR status - abnormal state detected, triggering recovery")
        lora_soft_restart_and_apply(cfg)

    # Publish TX acknowledgment to MQTT
    mqtt_publish(MQTT_TOPIC_TX_ACK, {
        "timestamp": now_iso(),
        "status_code": format_status_code(st),
        "transmit_time": tx_time,
    })


# =============================================================================
# CONFIGURATION RELOAD
# =============================================================================

def check_and_apply_config(last_cfg_check):
    """Check for configuration file changes and apply if changed."""
    global cfg, cfg_hash

    now = time.time()
    if now - last_cfg_check < CONFIG_POLL_SEC:
        return last_cfg_check

    logging.debug("Checking for config file changes...")
    newc, newh, changed = cfg_load_if_changed(cfg_hash)
    if changed:
        logging.warning(f"Config file changed detected! Old hash: {cfg_hash[:8]}..., new hash: {newh[:8]}...")
        cfg = newc
        cfg_hash = newh
        lora_soft_restart_and_apply(cfg)

        mqtt_publish(MQTT_TOPIC_CONFIG_ACK, {
            "timestamp": now_iso(),
            "status_code": 13,
        })
        logging.info("Config reloaded and applied")
    else:
        logging.debug("No config changes detected")

    return now


# =============================================================================
# MAIN PROGRAM
# =============================================================================

def main():
    """Main gateway loop."""
    global LoRa, mqtt_client, cfg, cfg_hash, tx_pending, tx_mode, tx_bytes_buf

    setup_logging()

    logging.info("=== Starting LoRa Gateway Initialization ===")
    cfg = cfg_load()
    cfg_hash = _dict_hash(cfg)

    mqtt_client = mqtt_init()
    LoRa = lora_init()
    lora_apply_common(cfg)
    set_rx_iq(cfg)
    try:
        LoRa.request(LoRa.RX_CONTINUOUS)
        logging.info("LoRa module set to RX_CONTINUOUS mode")
    except Exception as e:
        logging.error(f"Failed to set RX_CONTINUOUS mode: {e}")
        pass

    last_cfg_check = 0.0

    logging.info("=== Gateway Initialized Successfully - Entering Main Loop ===")
    loop_count = 0

    try:
        while True:
            loop_count += 1
            if loop_count % 1000 == 0:
                logging.debug(f"Main loop iteration: {loop_count}")

            if tx_pending:
                logging.debug("TX request pending, processing...")
                mode = tx_mode
                data = tx_bytes_buf
                tx_pending = False
                do_tx_now(mode, data)

            ok = LoRa.wait(WAIT_TIMEOUT_S)

            if ok and LoRa.available() > 0:
                rx_handle_if_ready()
            else:
                time.sleep(WAIT_SLEEP_S)

            last_cfg_check = check_and_apply_config(last_cfg_check)

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received, shutting down...")
        pass
    except Exception as e:
        logging.error(f"!!! FATAL ERROR in main loop: {e}", exc_info=True)
        raise
    finally:
        logging.info("Shutting down gateway...")
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            logging.info("MQTT client disconnected")
        except Exception as e:
            logging.error(f"Error during shutdown: {e}")
            pass
        logging.info("=== Gateway Stopped ===")
        logging.info(f"Total main loop iterations: {loop_count}")

if __name__ == "__main__":
    main()
