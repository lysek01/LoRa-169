#include <SPI.h>
#include <SX127x.h>

SX127x LoRa;

// ---------- Nastavení ----------
const uint32_t FREQ_HZ  = 169437500UL;
const uint8_t  SF       = 12;
const uint32_t BW_HZ    = 41700;
const uint8_t  CR_DENOM = 5;        // 4/5
const uint16_t PREAMBLE = 8;
const uint8_t  SYNCWORD = 0x12;
const bool     CRC_ON   = true;

const int8_t NSS_PIN  = 10;
const int8_t RST_PIN  = 9;
const int8_t DIO0_PIN = 2;          // může být i -1 (polling)
const int8_t TXEN_PIN = -1;
const int8_t RXEN_PIN = -1;

const uint8_t  TX_DBM  = 20;        // PA_BOOST modul
const uint32_t SPI_HZ  = 5000000;

char message[] = "1234567890";
uint8_t counter = 0;

void setup() {
  Serial.begin(115200);
  while (!Serial) {}

  Serial.println(F("LoRaRF-Arduino TX @169 MHz (RAW)"));

  // volitelně: nastavení SPI frekvence (můžeš i vynechat)
  LoRa.setSPI(SPI, SPI_HZ);

  // start rádia
  if (!LoRa.begin(NSS_PIN, RST_PIN, DIO0_PIN, TXEN_PIN, RXEN_PIN)) {
    Serial.println(F("ERR: LoRa.begin()"));
    while (1) {}
  }

  // RF parametry – MUSÍ sedět s RX
  LoRa.setFrequency(FREQ_HZ);
  LoRa.setSpreadingFactor(SF);
  LoRa.setBandwidth(BW_HZ);
  LoRa.setCodeRate(CR_DENOM);
  LoRa.setLdroEnable(true);

  // packet parametry
  LoRa.setHeaderType(SX127X_HEADER_EXPLICIT);
  LoRa.setPreambleLength(PREAMBLE);
  LoRa.setCrcEnable(CRC_ON);
  LoRa.setSyncWord(SYNCWORD);

  // Pozn.: LoRaRF 2.1.1 LDRO přepíná interně podle SF/BW, extra volání není potřeba.
  // Pokud bys měl ve své verzi i InvertIQ, nech false (default). Když metoda není, nic nepřidávej.
  LoRa.setInvertIq(true);

  LoRa.setTxPower(TX_DBM, SX127X_TX_POWER_PA_BOOST);

  Serial.println(F("Init OK. Odesílám každých 5 s."));
}

void loop() {
    // Request for receiving new LoRa packet
  LoRa.request();
  // Wait for incoming LoRa packet
  LoRa.wait();

  // Put received packet to message and counter variable
  // read() and available() method must be called after request() method
  const uint8_t msgLen = LoRa.available();
  char message[msgLen];
  LoRa.read(message, msgLen);
  uint8_t counter = LoRa.read();

  // Print received message and counter in serial
  Serial.write(message, msgLen);
  Serial.print("  ");
  Serial.println(counter);

  // Print packet / signal status
  Serial.print("RSSI: ");
  Serial.print(LoRa.packetRssi());
  Serial.print(" dBm | SNR: ");
  Serial.print(LoRa.snr());
  Serial.println(" dB");

  // Show received status in case CRC or header error occur
  uint8_t status = LoRa.status();
  if (status == SX127X_STATUS_CRC_ERR) Serial.println("CRC error");
  else if (status == SX127X_STATUS_HEADER_ERR) Serial.println("Packet header error");
  Serial.println();
}
