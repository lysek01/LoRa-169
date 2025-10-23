# LoRa-169 Gateway Documentation

## Overview
The LoRa-169 Gateway is a Raspberry Pi-based system for receiving and transmitting LoRa packets on the 169 MHz IoT band using the SX127x radio module. It provides MQTT integration for remote control and configuration management.

## System Components

### 1. **lora_gateway.py** - Main Gateway Service
The core application that handles LoRa radio communication and MQTT integration.

**Features:**
- Continuous monitoring for incoming LoRa packets
- MQTT-based packet transmission (hex/ASCII formats)
- Dynamic configuration reloading without restart
- RSSI/SNR measurement and reporting
- Automatic radio recovery on errors

**MQTT Topics:**
- `loravsb/169/rx` - Published when packets are received
- `loravsb/169/tx/hex` - Subscribe to transmit hex-encoded data
- `loravsb/169/tx/ascii` - Subscribe to transmit ASCII data
- `loravsb/169/tx/ack` - Published transmission acknowledgments
- `loravsb/169/config/ack` - Published config change notifications

### 2. **lora_config.py** - Configuration Management Service
Shadow configuration service for remote MQTT-based configuration updates.

**MQTT Topics:**
- `loravsb/169/config/get` - Request current configuration
- `loravsb/169/config/set` - Update configuration (JSON payload)
- `loravsb/169/config/reported` - Reports current configuration state
- `loravsb/169/config/ack` - Configuration operation acknowledgments

### 3. **config.json** - Radio Configuration
LoRa radio parameters (frequency, spreading factor, bandwidth, coding rate, etc.)

### 4. **install.sh** - Installation Script
Automated setup script that installs dependencies and creates systemd services.

## Radio Configuration Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| **frequency** | 169.4375 MHz | Operating frequency (169 MHz IoT band) |
| **spreading_factor** | 12 | SF12 (longest range, slowest speed) |
| **bandwidth** | 41.7 kHz | Narrow bandwidth for 169 MHz |
| **coding_rate** | 5 | 4/5 error correction |
| **ldro_enable** | true | Low Data Rate Optimization |
| **preamble_length** | 8 | 8-symbol preamble |
| **sync_word** | 18 (0x12) | Network sync word |
| **crc_enable** | true | CRC error checking enabled |
| **tx_power** | 30 | 30 dBm output power |
| **rx_gain** | "G1" | Boosted LNA gain mode |

## Error Messages and Status Codes

### Configuration Service Status Codes (lora_config.py)

| Code | Constant | Description | Trigger |
|------|----------|-------------|---------|
| **11** | REPORTED_SENT | Configuration successfully reported | After MQTT connect or GET request |
| **12** | CONFIG_OVERWRITTEN | Configuration file updated | Successful SET operation |
| **13** | CONFIG_APPLIED | New configuration applied to radio | Config change detected |
| **14** | INVALID_PAYLOAD | Malformed JSON received | Invalid/empty JSON in SET message |
| **15** | WRITE_FAILED | File write error | Cannot write config.json |
| **16** | INTERNAL_ERROR | Unhandled exception | Unexpected error in handler |

### Gateway Radio Status Codes (lora_gateway.py)

| Code | Constant | Description |
|------|----------|-------------|
| **0** | STATUS_DEFAULT | Default/idle state |
| **1** | STATUS_TX_WAIT | Waiting for transmission |
| **2** | STATUS_TX_TIMEOUT | Transmission timeout |
| **3** | STATUS_TX_DONE | Transmission completed successfully |
| **4** | STATUS_RX_WAIT | Waiting to receive |
| **5** | STATUS_RX_CONTINUOUS | Continuous receive mode active |
| **6** | STATUS_RX_TIMEOUT | Reception timeout |
| **7** | STATUS_RX_DONE | Packet received successfully |
| **8** | STATUS_HEADER_ERR | LoRa header corruption detected |
| **9** | STATUS_CRC_ERR | CRC checksum validation failed |
| **10** | STATUS_CAD_WAIT | Channel Activity Detection in progress |
| **11** | STATUS_CAD_DETECTED | Channel activity detected |
| **12** | STATUS_CAD_DONE | CAD completed |

### Error Messages

| Error Message | Location | Meaning | Resolution |
|---------------|----------|---------|------------|
| "Invalid JSON payload" | lora_config.py:103 | SET message contains non-JSON data | Send valid JSON object |
| "Write failed" | lora_config.py:112 | Cannot write config.json to disk | Check file permissions |
| "payload must be non-empty JSON object" | lora_config.py:101 | Empty or invalid config object | Provide valid configuration |
| "Unhandled error" | lora_config.py:120 | Unexpected exception occurred | Check system logs |
| "Run as root: sudo bash $0" | install.sh:20 | Installation requires root privileges | Run with sudo |
| "Script not found: {path}" | install.sh:43-44 | Required Python script missing | Verify file structure |

## Installation

```bash
cd /path/to/gateway
sudo bash install.sh
```

The script will:
1. Install system dependencies (Python3, venv, git)
2. Create Python virtual environment
3. Install Python packages from requirements.txt
4. Create two systemd services
5. Enable and start services

**Services Created:**
- `loravsb-gateway.service` - Main gateway (port 1883)
- `loravsb-config.service` - Configuration manager

## Hardware Requirements

- **Platform:** Raspberry Pi (tested on Pi 3/4)
- **Radio Module:** SX127x LoRa transceiver
- **Connections:**
  - SPI Bus 0, CS 0
  - GPIO 25: Reset pin
  - GPIO 5: DIO0 interrupt pin

## Usage Examples

### Transmit Hex Data via MQTT
```bash
mosquitto_pub -h 158.196.109.41 -t loravsb/169/tx/hex -m "48656C6C6F"
```

### Transmit ASCII Data
```bash
mosquitto_pub -h 158.196.109.41 -t loravsb/169/tx/ascii -m "Hello"
```

### Update Configuration
```bash
mosquitto_pub -h 158.196.109.41 -t loravsb/169/config/set -m '{"spreading_factor":11}'
```

### Request Current Configuration
```bash
mosquitto_pub -h 158.196.109.41 -t loravsb/169/config/get -m ""
```

### Subscribe to Received Packets
```bash
mosquitto_sub -h 158.196.109.41 -t loravsb/169/rx
```

## Service Management

### Check Service Status
```bash
sudo systemctl status loravsb-gateway
sudo systemctl status loravsb-config
```

### View Logs
```bash
sudo journalctl -u loravsb-gateway -f
sudo journalctl -u loravsb-config -f
```

### Restart Services
```bash
sudo systemctl restart loravsb-gateway
sudo systemctl restart loravsb-config
```

### Stop Services
```bash
sudo systemctl stop loravsb-gateway
sudo systemctl stop loravsb-config
```

### Disable Auto-Start
```bash
sudo systemctl disable loravsb-gateway
sudo systemctl disable loravsb-config
```

## Architecture Details

### File Structure
```
gateway/
├── config.json              # Radio configuration
├── lora_gateway.py          # Main gateway service
├── lora_config.py           # Config management service
├── install.sh               # Installation script
├── requirements.txt         # Python dependencies
```

### Main Gateway Service Flow
1. Load configuration from config.json
2. Initialize SX127x radio via SPI
3. Connect to MQTT broker
4. Enter main loop:
   - Check for configuration changes (reload if needed)
   - Check for received LoRa packets
   - Process pending transmissions
   - Handle MQTT messages

### Configuration Service Flow
1. Connect to MQTT broker
2. Subscribe to config GET/SET topics
3. Publish initial configuration state
4. Wait for incoming messages:
   - **GET**: Publish current config.json
   - **SET**: Merge changes into config.json atomically

### MQTT Message Flow

**Receiving LoRa Packet:**
```
LoRa Radio → lora_gateway.py → MQTT Publish → loravsb/169/rx
```

**Transmitting LoRa Packet:**
```
MQTT Subscribe → loravsb/169/tx/hex → lora_gateway.py → LoRa Radio
```

**Configuration Update:**
```
MQTT → loravsb/169/config/set → lora_config.py → config.json
→ lora_gateway.py (detects change) → Reload radio
```

## Dependencies

### Python Packages (requirements.txt)
- **RPi.GPIO** - Raspberry Pi GPIO library
- **spidev** - SPI device interface
- **paho-mqtt>=2.0,<3** - MQTT client library
- **LoRaRF** - LoRa radio driver for SX127x
- **wheel** - Python package wheel format
- **setuptools** - Python package tools

### System Requirements
- Debian/Ubuntu-based Linux distribution
- Python 3.7+
- systemd init system
- Root/sudo access for installation

## Troubleshooting

### Gateway Not Starting
**Symptoms:** Service fails to start or crashes immediately

**Possible Causes:**
1. SPI interface not enabled
2. Incorrect GPIO pin connections
3. Missing dependencies
4. Permission issues

**Solutions:**
```bash
# Enable SPI on Raspberry Pi
sudo raspi-config
# Navigate to: Interface Options → SPI → Enable

# Check service logs
sudo journalctl -u loravsb-gateway -n 50

# Verify Python dependencies
source /opt/loravsb/venv/bin/activate
pip list

# Check file permissions
ls -la /opt/loravsb/
```

### No Packets Received
**Symptoms:** Gateway running but no packets appear on MQTT

**Possible Causes:**
1. Incorrect frequency or radio parameters
2. No transmitter in range
3. Antenna not connected
4. Radio module hardware failure

**Solutions:**
```bash
# Check current configuration
mosquitto_pub -h 158.196.109.41 -t loravsb/169/config/get -m ""
mosquitto_sub -h 158.196.109.41 -t loravsb/169/config/reported -C 1

# Verify radio parameters match transmitter
cat /opt/loravsb/config.json

# Check service logs for errors
sudo journalctl -u loravsb-gateway -f
```

### Configuration Not Applying
**Symptoms:** SET commands sent but configuration unchanged

**Possible Causes:**
1. Config service not running
2. Invalid JSON payload
3. File write permissions
4. MQTT connection issues

**Solutions:**
```bash
# Check config service status
sudo systemctl status loravsb-config

# View config service logs
sudo journalctl -u loravsb-config -n 50

# Test configuration update
mosquitto_pub -h 158.196.109.41 -t loravsb/169/config/set -m '{"spreading_factor":11}'

# Check for acknowledgment
mosquitto_sub -h 158.196.109.41 -t loravsb/169/config/ack -C 1
```

### MQTT Connection Failed
**Symptoms:** Cannot connect to MQTT broker

**Possible Causes:**
1. Incorrect broker IP address
2. Network connectivity issues
3. Firewall blocking port 1883
4. Invalid credentials

**Solutions:**
```bash
# Test MQTT connectivity
mosquitto_pub -h 158.196.109.41 -p 1883 -t test -m "hello"

# Check network connectivity
ping 158.196.109.41

# Verify port is open
nc -zv 158.196.109.41 1883

# Check credentials in source code
grep MQTT_USERNAME /opt/loravsb/lora_gateway.py
```

## Future Improvements

### Recommended Enhancements
1. **Security:**
   - Environment variable-based configuration
   - TLS/SSL support for MQTT
   - Authentication token validation

2. **Reliability:**
   - Structured logging with log levels
   - Health check endpoint
   - Watchdog timer for radio hang detection
   - Automatic radio reset on persistent errors

3. **Monitoring:**
   - Prometheus metrics export
   - Packet statistics (RX/TX counters, error rates)
   - RSSI/SNR histograms

4. **Code Quality:**
   - Unit test suite
   - Class-based architecture (replace globals)
   - Docstrings for all functions
   - Type hints (Python 3.7+)

5. **Features:**
   - Multiple radio support
   - Packet filtering/routing
   - Time-scheduled transmissions
   - Over-the-air configuration

