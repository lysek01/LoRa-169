#include <SPI.h>
#include <SX127x.h>

SX127x LoRa;

const uint32_t FREQ_HZ  = 169437500UL;
const uint8_t  SF       = 12;
const uint32_t BW_HZ    = 41700;
const uint8_t  CR_DENOM = 5;
const uint16_t PREAMBLE = 8;
const uint8_t  SYNCWORD = 0x12;
const bool     CRC_ON   = true;

const int8_t NSS_PIN  = 10;
const int8_t RST_PIN  = 9;
const int8_t DIO0_PIN = 2;

const uint8_t  TX_DBM  = 20;
const uint32_t SPI_HZ  = 5000000;

char message[] = "1234567890";
uint8_t counter = 0;

void setup() {
  Serial.begin(9600);

  Serial.println(F("LoRaRF-Arduino TX @169 MHz"));
  LoRa.setSPI(SPI, SPI_HZ);

  if (!LoRa.begin(NSS_PIN, RST_PIN, DIO0_PIN, -1, -1)) {
    Serial.println(F("ERR: LoRa.begin()"));
    while (1) {}
  }

  LoRa.setFrequency(FREQ_HZ);
  LoRa.setSpreadingFactor(SF);
  LoRa.setBandwidth(BW_HZ);
  LoRa.setCodeRate(CR_DENOM);
  LoRa.setLdroEnable(true);

  LoRa.setHeaderType(SX127X_HEADER_EXPLICIT);
  LoRa.setPreambleLength(PREAMBLE);
  LoRa.setCrcEnable(CRC_ON);
  LoRa.setSyncWord(SYNCWORD);

  LoRa.setInvertIq(true);

  LoRa.setTxPower(TX_DBM, SX127X_TX_POWER_PA_BOOST);
}

void loop() {
  LoRa.request();
  LoRa.wait();

  const uint8_t msgLen = LoRa.available();
  char message[msgLen];
  LoRa.read(message, msgLen);
  uint8_t counter = LoRa.read();

  Serial.write(message, msgLen);
  Serial.println("");

  Serial.print("RSSI: ");
  Serial.print(LoRa.packetRssi());
  Serial.print(" dBm | SNR: ");
  Serial.print(LoRa.snr());
  Serial.println(" dB");

  uint8_t status = LoRa.status();
  if (status == SX127X_STATUS_CRC_ERR) Serial.println("CRC error");
  else if (status == SX127X_STATUS_HEADER_ERR) Serial.println("Packet header error");
  Serial.println();
}
