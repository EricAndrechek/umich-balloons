#include <Wire.h>           // i2c libraryc:\Users\brian\AppData\Local\Arduino15\packages\esp32\hardware\esp32\3.2.0-RC2\variants\adafruit_feather_esp32_v2\pins_arduino.h
#include <math.h>
#include <stdio.h>
#include <Adafruit_MCP23X08.h>
#include "UM_LoRa.h"        // modified LoRa library to use mcp IO expander
#include <IridiumSBD.h>     // iridium library
#include <SparkFun_u-blox_GNSS_Arduino_Library.h> //http://librarymanager/All#SparkFun_u-blox_GNSS
#include <SPI.h>
#include <ArduinoJson.h>
#include "Adafruit_BME680.h"
#include <SparkFun_Qwiic_Buzzer_Arduino_Library.h>

SemaphoreHandle_t mutex = NULL; // mutex to make sure we don't clobber serial or the data structure with multiple tasks

#define ONGROUND 0
#define FLYING 1
#define LANDING 2

int state; // are we on the ground, flying, or landing?

// 1, 2, 3 ssids 11, 12, 13 respectively
#define SATNUM 3


/////////////////////////////////////////////////////////////////////////
//  APRS 
/////////////////////////////////////////////////////////////////////////
const unsigned char mycall[] = "KF8ABL";
int myssid = 11;  // This gets set dynamically based on SATTNUM in setup.

#define OUT_PIN T7 // GPIO 27
#define PTT_PIN T4 // GPIO 13
#define PD_PIN T5  // GPIO 12
#define HL_PIN T9 // GPIO 32

#define _1200 1
#define _2400 0

bool nada = _2400;

const float baud_adj = 0.975;//0.975;  
const float adj_1200 = 1.0 * baud_adj;
const float adj_2400 = 1.0 * baud_adj;
unsigned int tc1200 = (unsigned int)(0.5 * adj_1200 * 1000000.0 / 1200.0);
unsigned int tc2400 = (unsigned int)(0.5 * adj_2400 * 1000000.0 / 2400.0);

#define _FLAG 0x7e


const char *dest = "APRS";
const char *digi = "WIDE2";
char digissid = 1;
const char sym_ovl = '/';
const char sym_tab = 'O';
char bit_stuff = 0;
unsigned short crc = 0xffff;

const char *mystatus = "Hello";
const char *lat = "4217.67N"; 
const char *lon = "08342.78W"; 
const char *course = "010";        // In degrees 0-360
const char *speed = "005";         // In knots
const char *alt = "/A=012345";        // In ft MSL
const char *bat_volt = "7.4";      // In V
const char *temperature = "63.12"; // In C
const char *cpu_temp = "63.12";    // In C
//bool alt_fault = false;

float alt_feet = 840; // AA AGL

unsigned char header_dest[] = {'A' << 1, 'P' << 1, 'R' << 1, 'S' << 1, ' ' << 1, ' ' << 1, '0' << 1}; // 7 chars
unsigned char header_source[] = {'K' << 1, 'F' << 1, '8' << 1, 'A' << 1, 'B' << 1, 'L' << 1, 11 +'0' << 1}; // 7 chars
unsigned char header_digi[] = {'W' << 1, 'I' << 1, 'D' << 1, 'E' << 1, '2' << 1, ' ' << 1, ((1+'0') << 1) +1}; // 7 chars
unsigned char header_ctrl[] = {0x03}; // 1 char
unsigned char header_PID[] = {0xf0}; // 1 char
unsigned char payload[] = {'!','4','2','1','7','.','6','7','N',0x2f,'0','8','3','4','2','.','7','8','W',0x4f,'0','1','0',0x2f,'0','0','5','/','A','=','0','1','2','3','4','5'}; //36 characters

unsigned char mycrc[] = {0x0, 0x1}; // 2 char

unsigned char ax25_packet[128]; 
unsigned char stuffed_ax25_packet[128];
unsigned char fx25_packet[168]; // correlation tag + stuffed ax25 + parity block

rmt_data_t rmt_buffer[2688]; // (160 byte fx buffer + 8 bytes each flag * 8) * 2 because 2400 hz tone takes 2 bytes
rmt_data_t* rmtPtr = &rmt_buffer[0];
int rmtCtr = 0;

int numchars = 0;
unsigned char packetreport[300];

// Correlation tag 5 0x6E260B1AC5835FAE -223 byte ax25 frame + 32 byte parity block
//unsigned char correlation_tag[8] = {0xAe, 0x5F, 0x83, 0xC5, 0x1A, 0x0B, 0x26, 0x6E};
// Correlation tag 6 0xFF94DC634F1CFF4E - bytes are reversed 128 byte ax25 frame + 32 byte parity block
 unsigned char correlation_tag[8] = {0x4E, 0xFF, 0x1C, 0x4F, 0x63, 0xDC, 0x94, 0xFF};

unsigned char rs_parity[32];



//unsigned char fx25_buffer[255];// = message (223)+ parity block (32); for tag 5
unsigned char fx25_buffer[160]; // message (128) + parity (32) for tag 6

// FX25 TABLES
 unsigned char index_of[] = {0xFF , 0x0 , 0x1 , 0x19 , 0x2 , 0x32 , 0x1A , 0xC6 , 
                            0x3 , 0xDF , 0x33 , 0xEE , 0x1B , 0x68 , 0xC7 , 0x4B , 
                            0x4 , 0x64 , 0xE0 , 0xE , 0x34 , 0x8D , 0xEF , 0x81 , 
                            0x1C , 0xC1 , 0x69 , 0xF8 , 0xC8 , 0x8 , 0x4C , 0x71 , 
                            0x5 , 0x8A , 0x65 , 0x2F , 0xE1 , 0x24 , 0xF , 0x21 , 
                            0x35 , 0x93 , 0x8E , 0xDA , 0xF0 , 0x12 , 0x82 , 0x45 , 
                            0x1D , 0xB5 , 0xC2 , 0x7D , 0x6A , 0x27 , 0xF9 , 0xB9 , 
                            0xC9 , 0x9A , 0x9 , 0x78 , 0x4D , 0xE4 , 0x72 , 0xA6 , 
                            0x6 , 0xBF , 0x8B , 0x62 , 0x66 , 0xDD , 0x30 , 0xFD , 
                            0xE2 , 0x98 , 0x25 , 0xB3 , 0x10 , 0x91 , 0x22 , 0x88 , 
                            0x36 , 0xD0 , 0x94 , 0xCE , 0x8F , 0x96 , 0xDB , 0xBD , 
                            0xF1 , 0xD2 , 0x13 , 0x5C , 0x83 , 0x38 , 0x46 , 0x40 , 
                            0x1E , 0x42 , 0xB6 , 0xA3 , 0xC3 , 0x48 , 0x7E , 0x6E , 
                            0x6B , 0x3A , 0x28 , 0x54 , 0xFA , 0x85 , 0xBA , 0x3D , 
                            0xCA , 0x5E , 0x9B , 0x9F , 0xA , 0x15 , 0x79 , 0x2B , 
                            0x4E , 0xD4 , 0xE5 , 0xAC , 0x73 , 0xF3 , 0xA7 , 0x57 , 
                            0x7 , 0x70 , 0xC0 , 0xF7 , 0x8C , 0x80 , 0x63 , 0xD , 
                            0x67 , 0x4A , 0xDE , 0xED , 0x31 , 0xC5 , 0xFE , 0x18 , 
                            0xE3 , 0xA5 , 0x99 , 0x77 , 0x26 , 0xB8 , 0xB4 , 0x7C , 
                            0x11 , 0x44 , 0x92 , 0xD9 , 0x23 , 0x20 , 0x89 , 0x2E , 
                            0x37 , 0x3F , 0xD1 , 0x5B , 0x95 , 0xBC , 0xCF , 0xCD , 
                            0x90 , 0x87 , 0x97 , 0xB2 , 0xDC , 0xFC , 0xBE , 0x61 , 
                            0xF2 , 0x56 , 0xD3 , 0xAB , 0x14 , 0x2A , 0x5D , 0x9E , 
                            0x84 , 0x3C , 0x39 , 0x53 , 0x47 , 0x6D , 0x41 , 0xA2 , 
                            0x1F , 0x2D , 0x43 , 0xD8 , 0xB7 , 0x7B , 0xA4 , 0x76 , 
                            0xC4 , 0x17 , 0x49 , 0xEC , 0x7F , 0xC , 0x6F , 0xF6 , 
                            0x6C , 0xA1 , 0x3B , 0x52 , 0x29 , 0x9D , 0x55 , 0xAA , 
                            0xFB , 0x60 , 0x86 , 0xB1 , 0xBB , 0xCC , 0x3E , 0x5A , 
                            0xCB , 0x59 , 0x5F , 0xB0 , 0x9C , 0xA9 , 0xA0 , 0x51 , 
                            0xB , 0xF5 , 0x16 , 0xEB , 0x7A , 0x75 , 0x2C , 0xD7 , 
                            0x4F , 0xAE , 0xD5 , 0xE9 , 0xE6 , 0xE7 , 0xAD , 0xE8 , 
                            0x74 , 0xD6 , 0xF4 , 0xEA , 0xA8 , 0x50 , 0x58, 0xAF};

  unsigned char alpha_to[]= {0x1 , 0x2 , 0x4 , 0x8 , 0x10 , 0x20 , 0x40 , 0x80 , 
                            0x1D , 0x3A , 0x74 , 0xE8 , 0xCD , 0x87 , 0x13 , 0x26 , 
                            0x4C , 0x98 , 0x2D , 0x5A , 0xB4 , 0x75 , 0xEA , 0xC9 , 
                            0x8F , 0x3 , 0x6 , 0xC , 0x18 , 0x30 , 0x60 , 0xC0 , 
                            0x9D , 0x27 , 0x4E , 0x9C , 0x25 , 0x4A , 0x94 , 0x35 , 
                            0x6A , 0xD4 , 0xB5 , 0x77 , 0xEE , 0xC1 , 0x9F , 0x23 , 
                            0x46 , 0x8C , 0x5 , 0xA , 0x14 , 0x28 , 0x50 , 0xA0 , 
                            0x5D , 0xBA , 0x69 , 0xD2 , 0xB9 , 0x6F , 0xDE , 0xA1 , 
                            0x5F , 0xBE , 0x61 , 0xC2 , 0x99 , 0x2F , 0x5E , 0xBC , 
                            0x65 , 0xCA , 0x89 , 0xF , 0x1E , 0x3C , 0x78 , 0xF0 , 
                            0xFD , 0xE7 , 0xD3 , 0xBB , 0x6B , 0xD6 , 0xB1 , 0x7F ,
                            0xFE , 0xE1 , 0xDF , 0xA3 , 0x5B , 0xB6 , 0x71 , 0xE2 , 
                            0xD9 , 0xAF , 0x43 , 0x86 , 0x11 , 0x22 , 0x44 , 0x88 , 
                            0xD , 0x1A , 0x34 , 0x68 , 0xD0 , 0xBD , 0x67 , 0xCE , 
                            0x81 , 0x1F , 0x3E , 0x7C , 0xF8 , 0xED , 0xC7 , 0x93 , 
                            0x3B , 0x76 , 0xEC , 0xC5 , 0x97 , 0x33 , 0x66 , 0xCC , 
                            0x85 , 0x17 , 0x2E , 0x5C , 0xB8 , 0x6D , 0xDA , 0xA9 , 
                            0x4F , 0x9E , 0x21 , 0x42 , 0x84 , 0x15 , 0x2A , 0x54 , 
                            0xA8 , 0x4D , 0x9A , 0x29 , 0x52 , 0xA4 , 0x55 , 0xAA , 
                            0x49 , 0x92 , 0x39 , 0x72 , 0xE4 , 0xD5 , 0xB7 , 0x73 , 
                            0xE6 , 0xD1 , 0xBF , 0x63 , 0xC6 , 0x91 , 0x3F , 0x7E , 
                            0xFC , 0xE5 , 0xD7 , 0xB3 , 0x7B , 0xF6 , 0xF1 , 0xFF , 
                            0xE3 , 0xDB , 0xAB , 0x4B , 0x96 , 0x31 , 0x62 , 0xC4 , 
                            0x95 , 0x37 , 0x6E , 0xDC , 0xA5 , 0x57 , 0xAE , 0x41 , 
                            0x82 , 0x19 , 0x32 , 0x64 , 0xC8 , 0x8D , 0x7 , 0xE , 
                            0x1C , 0x38 , 0x70 , 0xE0 , 0xDD , 0xA7 , 0x53 , 0xA6 , 
                            0x51 , 0xA2 , 0x59 , 0xB2 , 0x79 , 0xF2 , 0xF9 , 0xEF , 
                            0xC3 , 0x9B , 0x2B , 0x56 , 0xAC , 0x45 , 0x8A , 0x9 , 
                            0x12 , 0x24 , 0x48 , 0x90 , 0x3D , 0x7A , 0xF4 , 0xF5 , 
                            0xF7 , 0xF3 , 0xFB , 0xEB , 0xCB , 0x8B , 0xB , 0x16 , 
                            0x2C , 0x58 , 0xB0 , 0x7D , 0xFA , 0xE9 , 0xCF , 0x83 , 
                            0x1B , 0x36 , 0x6C , 0xD8 , 0xAD , 0x47 , 0x8E, 0x0}; 
  
  unsigned char genpoly[] = {0x12, 0xFB, 0xD7, 0x1C, 0x50, 0x6B, 0xF8, 0x35, 
                            0x54, 0xC2, 0x5B, 0x3B, 0xB0, 0x63, 0xCB, 0x89, 
                            0x2B, 0x68, 0x89, 0x0, 0x2C, 0x95, 0x94, 0xDA, 
                            0x4B, 0xB, 0xAD, 0xFE, 0xC2, 0x6D, 0x8, 0xB, 0x0};


__attribute__((always_inline))
static inline int modnn(int x){
  while (x >= 0xFF) {
    x -= 0xFF;
    x = (x >> 8) + (x & 0xFF);
  }
  return x;
}
#define MODNN(x) modnn(x)

#define FX25_MAX_DATA 239	// i.e. RS(255,239)

/////////////////////////////////////////////////////////////////////////
//  IO EXPANDER - LoRa CS and RESET pins defined in UM_LoRa library
/////////////////////////////////////////////////////////////////////////
Adafruit_MCP23X08 mcp;

#define SD_PIN 0
#define LED_PIN 7

/////////////////////////////////////////////////////////////////////////
//  GPS
/////////////////////////////////////////////////////////////////////////
SFE_UBLOX_GNSS myGNSS;


/////////////////////////////////////////////////////////////////////////
// BME
/////////////////////////////////////////////////////////////////////////
#define SEALEVELPRESSURE_HPA (1013.25)
Adafruit_BME680 bme(&Wire); // I2C0

/////////////////////////////////////////////////////////////////////////
// SD CARD
/////////////////////////////////////////////////////////////////////////
#include "SdFat.h"

SPIClass *hspi = NULL;

int sdCardFailed = 0;

// SD card initialization
// SD_FAT_TYPE = 0 for SdFat/File as defined in SdFatConfig.h,
// 1 for FAT16/FAT32, 2 for exFAT, 3 for FAT16/FAT32 and exFAT.
#define SD_FAT_TYPE 3

// Define bus and pins for SD interface
//#define SDCARD_MOSI_PIN MOSI
//#define SDCARD_MISO_PIN MISO
//#define SDCARD_SCK_PIN SCK

const uint8_t SD_CS_PIN = 32; // Need this variable for the SD card functions - actually use MCP for SD card CS - 32 is an unused GPIO; not even routed on the board
// Max SPI clock speed for SD; reduce if errors occur (16 MHz is safe)
SPIClass SD_SPI(VSPI);
#define SPI_CLOCK SD_SCK_MHZ(6)
//#define SD_CONFIG SdSpiConfig(SD_CS_PIN, DEDICATED_SPI, SPI_CLOCK, &SD_SPI) - crashes with FREERTOS
#define SD_CONFIG SdSpiConfig(SD_CS_PIN, SHARED_SPI, SPI_CLOCK, &SD_SPI)

#if SD_FAT_TYPE == 0
SdFat sd;
File file;
#elif SD_FAT_TYPE == 1
SdFat32 sd;
File32 file;
#elif SD_FAT_TYPE == 2
SdExFat sd;
ExFile file;
#elif SD_FAT_TYPE == 3
SdFs sd;
FsFile file;
#else // SD_FAT_TYPE
#error Invalid SD_FAT_TYPE
#endif // SD_FAT_TYPE

int loopIterator = 0;

/////////////////////////////////////////////////////////////////////////
// LoRA Section
/////////////////////////////////////////////////////////////////////////

SPIClass *vspi = NULL;
int counter = 0;

JsonDocument jsonData;

String Packet;
String IridiumPacket;

float speed_knots = 0;
int rounded_speed = 0;
long heading = 0;

/////////////////////////////////////////////////////////////////////////
// Data structure to hold last measured data
/////////////////////////////////////////////////////////////////////////

struct myStructure
{
  String GPS_date;    // GPS date
  String GPS_time;    // GPS time
  int GPS_hour;       // GPS raw hours
  int GPS_min;        // GPS raw minutes
  double GPS_lat;     // GPS latitude (degrees * 10^-7)
  double GPS_lon;     // GPS longitude (degrees * 10^-7)
  int32_t GPS_alt;    // GPS altitude (mm)
  int32_t GPS_course; // GPS heading (degrees / 10^-5)
  int32_t GPS_speed;  // GPS speed (mm/s)
  float BME_temp;     // BME680 temperature (C)
  float BME_pressure; // BME680 pressure (hPa)
  float BME_humidity; // BME680 humidity (%)
  float BME_gas_R;    // BME680 gas resistance (kohms)
  float BME_alt;      // BME680 calculated altitude (m)
  float voltage;      // Battery voltage
  int32_t GPS_sec;    // GPS seconds
  int32_t GPS_sats;   // Number of satellites
};

struct myStructure s1;

#define batteryVoltageScalingFactor 0.002 // multiply by 2 for the voltage divider, divide by 1000 to convert back to volts


/////////////////////////////////////////////////////////////////////////
//  IRIDIUM 
/////////////////////////////////////////////////////////////////////////
#define IridiumSerial Serial2

// Declare the IridiumSBD object
IridiumSBD modem(IridiumSerial, 14); // pin 14 is the sleep pin

// Iridium modem stuff
int err;
int signalQuality = -1;

int singleSend = 0;

const char *DataChar = Packet.c_str(); // pointer to c string of Json packet


/////////////////////////////////////////////////////////////////////////
//  BUZZER
/////////////////////////////////////////////////////////////////////////

QwiicBuzzer buzzer;

#define  a3f    208     // 208 Hz
#define  b3f    233     // 233 Hz
#define  b3     247     // 247 Hz
#define  c4     261     // 261 Hz MIDDLE C
#define  c4s    277     // 277 Hz
#define  e4f    311     // 311 Hz    
#define  f4     349     // 349 Hz 
#define  a4f    415     // 415 Hz  
#define  b4f    466     // 466 Hz 
#define  b4     493     //  493 Hz 
#define  c5     523     // 523 Hz 
#define  c5s    554     // 554  Hz
#define  e5f    622     // 622 Hz  
#define  f5     698     // 698 Hz 
#define  f5s    740     // 740 Hz
#define  a5f    831     // 831 Hz 

#define  rest    -1

volatile int beatlength  = 100; // determines tempo
float beatseparationconstant = 0.3;

int threshold;

int  a; // part index
int b; // song index

boolean  flag;

// Parts 1 and 2 (Intro)

int song1_intro_melody[] =
{c5s,  e5f, e5f, f5, a5f, f5s, f5, e5f, c5s, e5f, rest, a4f, a4f};

int song1_intro_rhythmn[]  =
{6, 10, 6, 6, 1, 1, 1, 1, 6, 10, 4, 2, 10};




int song1_chorus_melody[] =
{ b4f, b4f, a4f, a4f,
  f5, f5, e5f, b4f, b4f, a4f, a4f, e5f, e5f, c5s, c5, b4f,
  c5s, c5s, c5s, c5s,
  c5s, e5f, c5, b4f, a4f, a4f, a4f, e5f, c5s,
  b4f, b4f, a4f, a4f,
  f5,  f5, e5f, b4f, b4f, a4f, a4f, a5f, c5, c5s, c5, b4f,
  c5s, c5s, c5s, c5s,
  c5s, e5f, c5, b4f, a4f, rest, a4f, e5f, c5s, rest
};

int song1_chorus_rhythmn[]  =
{ 1, 1, 1, 1,
  3, 3, 6, 1, 1, 1, 1, 3, 3, 3, 1, 2,
  1, 1, 1, 1,
  3, 3, 3, 1, 2, 2, 2, 4, 8,
  1, 1, 1, 1,
  3, 3, 6, 1, 1, 1, 1, 3, 3, 3,  1, 2,
  1, 1, 1, 1,
  3, 3, 3, 1, 2, 2, 2, 4, 8, 4};
