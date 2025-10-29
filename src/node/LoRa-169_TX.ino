#include <SPI.h>
#include <SX127x.h>

SX127x LoRa;

const uint16_t interval = 10000;

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

  LoRa.setInvertIq(false);

  LoRa.setTxPower(TX_DBM, SX127X_TX_POWER_PA_BOOST);
}

void loop() {
  char buf[48];
  int n = snprintf(buf, sizeof(buf), "%s %u", message, counter);
  if (n < 0) n = 0;
  if (n > 255) n = 255;

  LoRa.setPayloadLength((uint8_t)n);

  LoRa.beginPacket();
  LoRa.write((uint8_t*)buf, (uint8_t)n);
  LoRa.endPacket();

  LoRa.wait();

  Serial.print(F("TX: "));
  Serial.write((uint8_t*)buf, (uint8_t)n);
  Serial.print(F("   #"));
  Serial.println(counter++);

  Serial.print(F("Transmit time: "));
  Serial.print(LoRa.transmitTime());
  Serial.println(F(" ms\n"));

  delay(interval);
}
