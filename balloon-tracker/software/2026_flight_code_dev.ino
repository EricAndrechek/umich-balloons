#include "my_header.h"  // Consolidated header file

void TaskIridium(void *pvParameters);
void TaskMainLoop(void *pvParameters);
void TaskFlightBrain(void *pvParameters);

void set_nada_1200(void) {
  // send one pulse at 1200 hz high 406 us, low 406 us, 100 ns resolution
  rmtPtr->duration0 = tc1200;
  rmtPtr->level0 = 1;
  rmtPtr->duration1 = tc1200;
  rmtPtr->level1 = 0;
  rmtPtr++;  // each block is 4 bytes
  rmtCtr++;
}

void set_nada_2400(void) {
  rmtPtr->duration0 = tc2400;
  rmtPtr->level0 = 1;
  rmtPtr->duration1 = tc2400;
  rmtPtr->level1 = 0;
  rmtPtr++;  // each block is 4 bytes
  rmtPtr->duration0 = tc2400;
  rmtPtr->level0 = 1;
  rmtPtr->duration1 = tc2400;
  rmtPtr->level1 = 0;
  rmtPtr++;  // each block is 4 bytes
  rmtCtr += 2;
}

void set_nada(bool nada) {
  if (nada)
    set_nada_1200();
  else
    set_nada_2400();
}


void encode_rs_char(unsigned char *data, unsigned char *bb) {

  int i, j;
  unsigned char feedback;

  memset(bb, 0, 32 * sizeof(unsigned char));  // clear out the FEC data area

  // Serial.print("Feedback: ");
  for (i = 0; i < 255 - 32; i++) {
    feedback = index_of[data[i] ^ bb[0]];
    // Serial.print("0x");
    // Serial.print(feedback, HEX);
    // Serial.print(", ");
    if (feedback != 0xFF) { /* feedback term is non-zero */
      for (j = 1; j < 32; j++)
        bb[j] ^= alpha_to[MODNN(feedback + genpoly[32 - j])];
    }
    /* Shift */
    memmove(&bb[0], &bb[1], sizeof(unsigned char) * (32 - 1));
    if (feedback != 0xFF)
      bb[32 - 1] = alpha_to[MODNN(feedback + genpoly[0])];
    else
      bb[32 - 1] = 0;
  }
  Serial.println("");
}

uint16_t ax25crc16(unsigned char *data_p, uint16_t length) {
  uint16_t crc = 0xFFFF;
  uint32_t data;
  uint16_t crc16_table[] = {
    0x0000, 0x1081, 0x2102, 0x3183,
    0x4204, 0x5285, 0x6306, 0x7387,
    0x8408, 0x9489, 0xa50a, 0xb58b,
    0xc60c, 0xd68d, 0xe70e, 0xf78f
  };

  while (length--) {
    crc = (crc >> 4) ^ crc16_table[(crc & 0xf) ^ (*data_p & 0xf)];
    crc = (crc >> 4) ^ crc16_table[(crc & 0xf) ^ (*data_p++ >> 4)];
  }

  data = crc;
  crc = (crc << 8) | (data >> 8 & 0xff);  // do byte swap here that is needed by AX25 standard
  return (~crc);
}

void send_char_NRZI(unsigned char in_byte, bool enBitStuff) {
  bool bits;
  //packetreport[numchars] = in_byte;
  numchars++;

  for (int i = 0; i < 8; i++) {
    bits = in_byte & 0x01;

    if (bits) {
      set_nada(nada);
      bit_stuff++;

      if ((enBitStuff) && (bit_stuff == 5)) {
        nada ^= 1;
        set_nada(nada);

        bit_stuff = 0;
      }
    } else {
      nada ^= 1;
      set_nada(nada);

      bit_stuff = 0;
    }

    in_byte >>= 1;
  }
}


#define put_bit(value) \
  { \
    if (olen >= osize) return (-1); \
    if (value) out[olen >> 3] |= 1 << (olen & 0x7); \
    olen++; \
  }

static int stuff_it(unsigned char *in, int ilen, unsigned char *out, int osize) {
  const unsigned char flag = 0x7e;
  int ret = -1;
  memset(out, 0, osize);
  out[0] = flag;
  int olen = 8;  // Number of bits in output.
  osize *= 8;    // Now in bits rather than bytes.
  int ones = 0;

  for (int i = 0; i < ilen; i++) {
    for (unsigned char imask = 1; imask != 0; imask <<= 1) {
      int v = in[i] & imask;
      put_bit(v);
      if (v) {
        ones++;
        if (ones == 5) {
          put_bit(0);
          ones = 0;
        }
      } else {
        ones = 0;
      }
    }
  }
  for (unsigned char imask = 1; imask != 0; imask <<= 1) {
    put_bit(flag & imask);
  }
  ret = (olen + 7) / 8;  // Includes any partial byte.

  unsigned char imask = 1;
  while (olen < osize) {
    put_bit(flag & imask);
    imask = (imask << 1) | (imask >> 7);  // Rotate.
  }

  return (ret);

}  // end stuff_it

void set_io(void) {
  // DRA818V Control Pins
  pinMode(PTT_PIN, OUTPUT);
  pinMode(PD_PIN, OUTPUT);
  pinMode(OUT_PIN, OUTPUT);
  pinMode(HL_PIN, INPUT);  // leave floating for 1W output on DRA818V

  // Battery monitor analog input pin
  pinMode(BATT_MONITOR, INPUT);

  // onboard esp32 LED for testing
  pinMode(LED_BUILTIN, OUTPUT);

  // Set PTT pin to high
  digitalWrite(PTT_PIN, HIGH);
  // Set PD pin to high
  digitalWrite(PD_PIN, HIGH);
  //Leave HL pin floating for 1W output
  //digitalWrite(HL_PIN, LOW);

  // SD card reader CS pin
  mcp.pinMode(SD_PIN, OUTPUT);
  mcp.digitalWrite(SD_PIN, HIGH);

  // External LED pin
  mcp.pinMode(LED_PIN, OUTPUT);
  mcp.digitalWrite(LED_PIN, LOW);
}


void setup() {
  switch (SATNUM) {
    case 1:
      myssid = 11;
      header_source[6] = (11 + '0') << 1;
      break;
    case 2:
      myssid = 12;
      header_source[6] = (12 + '0') << 1;
      break;
    case 3:
      myssid = 13;
      header_source[6] = (13 + '0') << 1;
      break;
  }

  delay(2000);
  Serial.begin(115200);

  // Mutex protects data structure and serial interface from being used by both threads
  mutex = xSemaphoreCreateMutex();
  xSemaphoreTake(mutex, portMAX_DELAY);


  Wire.begin();          // GPS, BME680, buzzer i2c interface
  Wire.setTimeOut(100);  // GPS sometimes fails to initialize with timeout failures, default is 50 ms
  delay(300);
  // Wire1.begin();
  // Wire1.setTimeOut(100);

  //////////////////////////////////////////////////////////////////////////////////////////////
  // MCP initialization
  //Serial.println("Starting MCP");

  for (int i = 0; i < 10; i++) {
    if (!mcp.begin_I2C()) {
      Serial.println("MCP Error.");
    }
  }
  set_io();  // Set initial state for all used GPIOs and MCP outputs

  // Set up hardware SPI for LoRa board.  SD card hardware SPI is set up in the header file
  if (SATNUM != 3) {
    hspi = new SPIClass(HSPI);
    hspi->begin(LORA_CLK_PIN, LORA_MISO_PIN, LORA_MOSI_PIN);
    LoRa.setSPI(*hspi);

    // Initialize LoRa modem
    if (!LoRa.begin(915E6, &mcp)) {
      Serial.println("Starting LoRa failed!");
      led_blink(100);
    }
    LoRa.setSpreadingFactor(10);
    LoRa.setSignalBandwidth(20.8E3);
    LoRa.setCodingRate4(8);
    LoRa.enableCrc();
    LoRa.setTxPower(20);
  // Serial.println("Starting LoRa succeeded!");
  }


  //////////////////////////////////////////////////////////////////////////////////////////////
  // SD
  //////////////////////////////////////////////////////////////////////////////////////////////
  mcp.digitalWrite(SD_PIN, LOW);
  delay(10);
  if (!sd.begin(SD_CONFIG)) {
    sd.initErrorPrint(&Serial);
    led_blink(100);
    sdCardFailed = 1;
  } else {
    //Serial.println("SD card started successfully");
  }
  mcp.digitalWrite(SD_PIN, HIGH);

  //////////////////////////////////////////////////////////////////////////////////////////////
  //  BME680 presence
  //////////////////////////////////////////////////////////////////////////////////////////////

  while (bme.begin() == false) {
    Serial.println("BME680 not detected.");
    led_blink(10);
    delay(1000);
  }

  // Set up oversampling, filter initialization, and turn on the gas heater
  bme.setTemperatureOversampling(BME680_OS_8X);
  bme.setHumidityOversampling(BME680_OS_2X);
  bme.setPressureOversampling(BME680_OS_4X);
  bme.setIIRFilterSize(BME680_FILTER_SIZE_3);
  bme.setGasHeater(320, 150);  // 320*C for 150 ms

  //////////////////////////////////////////////////////////////////////////////////////////////
  // GPS
  //////////////////////////////////////////////////////////////////////////////////////////////
  // myGNSS.enableDebugging(); // Uncomment this line to enable helpful debug messages on Serial

  while (myGNSS.begin(Wire, 0x42) == false)  // Connect to the u-blox module using Wire port
  {
    Serial.println(F("u-blox GNSS not detected at default I2C address. Retrying..."));
    led_blink(10);
    delay(1000);
  }
  //Serial.println("GPS connected");

  myGNSS.setI2COutput(COM_TYPE_UBX);             // Set the I2C port to output UBX only (turn off NMEA noise)
  myGNSS.setDynamicModel(DYN_MODEL_AIRBORNE4g);  // Set GPS to high altitude mode
  myGNSS.setNavigationFrequency(1);              // set navigation frequency to 1hz

  //Serial.println("GPS configured");
  //////////////////////////////////////////////////////////////////////////////////////////////
  // Initialize structure

  s1.GPS_date = "NULL";
  s1.GPS_time = "NULL";
  s1.GPS_hour = 0;
  s1.GPS_min = 0;
  s1.GPS_lat = 0;
  s1.GPS_lon = 0;
  s1.GPS_alt = 0;
  s1.GPS_course = 0;
  s1.GPS_speed = 0;
  s1.BME_temp = 0;
  s1.BME_humidity = 0;
  s1.BME_pressure = 0;
  s1.BME_alt = 0;
  s1.BME_gas_R = 0;
  s1.voltage = 0;
  s1.GPS_sec = 0;
  s1.GPS_sats = 0;

  //Serial.println("Structure initialized");

  poll_sensors();
  //check_gps();

  // Set up RMT device to drive DRA818V
  int rmtSetupCtr = 0;
  while (rmtSetupCtr < 5) {
    if (!rmtInit(OUT_PIN, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_1, 1000000)) {
      Serial.println("init sender failed, count = " + String(rmtSetupCtr));
      rmtSetupCtr++;
    } else {
      break;
    }
  }
  if (rmtSetupCtr >= 5) {
    Serial.println("Can't initialize RMT - REBOOTING");
    ESP.restart();
  }


  Serial1.begin(9600, SERIAL_8N1, RX, TX);
  // Wait for UARTS to come up
  delay(100);
  Serial1.write("AT+DMOCONNECT\r\n");
  delay(100);
  Serial1.write("AT+DMOCONNECT\r\n");
  delay(100);
  Serial1.write("AT+DMOCONNECT\r\n");
  delay(100);
  Serial1.write("AT+DMOSETGROUP=0,144.3900,144.3900,0000,0,0000\r\n");
  delay(100);
  Serial1.write("AT+SETFILTER=0,0,0\r\n");
  Serial.println("Radio programmed");
  xSemaphoreGive(mutex);

  xTaskCreatePinnedToCore(
    TaskFlightBrain, "Flight management task", 8192  // Stack size
    ,
    NULL  // When no parameter is used, simply pass NULL
    ,
    2  // Priority
    ,
    NULL  // With task handle we will be able to manipulate with this task.
    ,
    1  // Core on which the task will run
  );

  // Set up two tasks to run independently.
  xTaskCreate(
    TaskIridium, "Iridium task"  // A name just for humans
    ,
    8192  // The stack size can be checked by calling `uxHighWaterMark = uxTaskGetStackHighWaterMark(NULL);`
    ,
    NULL  // Task parameter which can modify the task behavior. This must be passed as pointer to void.
    ,
    3  // Priority
    ,
    NULL  // Task handle is not used here - simply pass NULL
  );

  // This variant of task creation can also specify on which core it will be run (only relevant for multi-core ESPs)
  xTaskCreatePinnedToCore(
    TaskLoRaAPRS, "APRS/LoRa task", 8192  // Stack size
    ,
    NULL  // When no parameter is used, simply pass NULL
    ,
    4  // Priority
    ,
    NULL  // With task handle we will be able to manipulate with this task.
    ,
    1  // Core on which the task will run
  );



  led_blink(5);
}
///////////////////////////////////////////////////////////
// END SETUP


void poll_sensors(void) {
  // BME680
  if (bme.performReading()) {
    //Serial.println("BME reading successful");
    s1.BME_temp = bme.temperature;
    s1.BME_pressure = bme.pressure / 100.0;
    s1.BME_humidity = bme.humidity;
    s1.BME_gas_R = bme.gas_resistance / 1000.0;
    s1.BME_alt = bme.readAltitude(SEALEVELPRESSURE_HPA);
  } else {
    s1.BME_temp = 0.0;
    s1.BME_pressure = 0.0;
    s1.BME_humidity = 0.0;
    s1.BME_gas_R = 0.0;
    s1.BME_alt = 0.0;
  }

  // GPS

  s1.GPS_date = String(myGNSS.getYear()) + "-" + String(myGNSS.getMonth()) + "-" + String(myGNSS.getDay());
  s1.GPS_hour = myGNSS.getHour();
  s1.GPS_min = myGNSS.getMinute();
  s1.GPS_sec = myGNSS.getSecond();
  if (s1.GPS_hour > 5) {
    s1.GPS_time = String(myGNSS.getHour() - 4) + ":" + String(myGNSS.getMinute()) + ":" + String(s1.GPS_sec);
    s1.GPS_hour = s1.GPS_hour - 4;
  } else {
    s1.GPS_time = String(myGNSS.getHour() + 8) + ":" + String(myGNSS.getMinute()) + ":" + String(s1.GPS_sec);
    s1.GPS_hour = s1.GPS_hour + 8;
  }
  s1.GPS_lat = myGNSS.getLatitude();
  s1.GPS_lon = myGNSS.getLongitude();
  s1.GPS_alt = myGNSS.getAltitudeMSL();
  s1.GPS_course = myGNSS.getHeading();
  s1.GPS_speed = myGNSS.getGroundSpeed();
  s1.GPS_sats = myGNSS.getSIV();
  //Serial.println("GPS reading successful");
  //Serial.println("GPS time: " + s1.GPS_time);

  // Battery voltage
  s1.voltage = analogRead(BATT_MONITOR) * batteryVoltageScalingFactor;
  //Serial.println("Battery voltage reading successful");
}

void save_file(void) {
  //Serial.println("setting SD pin low");
  if (sdCardFailed == 0) {
    mcp.digitalWrite(SD_PIN, LOW);
    delay(10);
    String filename = "testdata.csv";
    //Serial.println("opening file");
    if (!file.open(filename.c_str(), FILE_WRITE)) {
      //Serial.println("File open failed!");
    } else {
      //Serial.println("File opened");
    }
    // Write header the first time we run through the loop
    if (loopIterator == 0) {
      file.println("gps_lat, GPS_lon, GPS_alt, GPS_course, GPS_speed, BME_temp, BME_humidity, BME_pressure, BME_alt, BME_gas_R, GPS_date, GPS_time, voltage, GPS_sats");
      //Serial.println("Wrote header");
    }
    file.println(String(s1.GPS_lat) + "," + String(s1.GPS_lon) + "," + String(s1.GPS_alt) + "," + String(s1.GPS_course) + "," + String(s1.GPS_speed) + "," + String(s1.BME_temp) + "," + String(s1.BME_humidity) + "," + String(s1.BME_pressure) + "," + String(s1.BME_alt) + "," + String(s1.BME_gas_R) + "," + s1.GPS_date + "," + s1.GPS_time + "," + String(s1.voltage) + "," + String(s1.GPS_sats));
    file.close();
    mcp.digitalWrite(SD_PIN, HIGH);
    //Serial.println("File Line Saved");
    led_blink(1);
    loopIterator += 1;
  } else {
    led_blink(6);
  }
}


void assemble_json() {


  speed_knots = s1.GPS_speed * 0.00194384;  // convert speed to knots
  rounded_speed = (int)round(speed_knots);  // Round to nearest whole number

  jsonData["call"] = String((const char *)mycall) + "-" + String(myssid);
  jsonData["lat"] = (long)(s1.GPS_lat * 0.001);  // latitude converted into an int compatible format; xy.abcd -> xyabcd
  jsonData["lon"] = (long)(s1.GPS_lon * 0.001);  //longitude converted to an int compatbible format as with lat
  if (s1.GPS_alt > 0) {
    jsonData["alt"] = (int)(s1.GPS_alt * 0.00001);  // altitude mm -> dm (1.1km -> 11)
  } else {
    jsonData["alt"] = (int)0;
  }
  jsonData["dir"] = (int)(s1.GPS_course * 0.00001);  //headingStr; Heading rounded
  jsonData["spd"] = rounded_speed;                   //speedStr;
  jsonData["v"] = (int)(s1.voltage * 10);            // Voltage *10 so it fits in an int.  4.4V -> 44
  jsonData["t"] = s1.GPS_hour * 100 + s1.GPS_min;

  serializeJson(jsonData, Packet);
  //*DataChar = Packet.c_str();
  Serial.println("Assembled packet: " + Packet);
}

void led_blink(int numloops) {
  if (state != FLYING) {  // no sense in blinking the LEDs while we are in the air
    int j = 0;
    while (j < numloops) {
      mcp.digitalWrite(LED_PIN, HIGH);
      vTaskDelay(100 / portTICK_PERIOD_MS);
      mcp.digitalWrite(LED_PIN, LOW);
      delay(100 / portTICK_PERIOD_MS);
      j++;
    }
  }
}


//////////////////////////////////////////////////////////////////////////////////////////////
// Flight management task (also polls sensors and saves data every 10s)
//////////////////////////////////////////////////////////////////////////////////////////////

void TaskFlightBrain(void *pvParameters) {
  state = ONGROUND;
  //state = LANDING;
  int transitioncount = 0;
  for (;;) {
    while (
      xSemaphoreTake(mutex, portMAX_DELAY) == pdFALSE) {}  // wait to take the mutex
    save_file();
    xSemaphoreGive(mutex);
    led_blink(1);
    if (state == ONGROUND) {
      if (s1.GPS_alt > 1000000)  // more than 1km up {
        transitioncount++;
      if (transitioncount > 5) {
        state = FLYING;
        transitioncount = 0;
      }
    }
    if (state == FLYING) {
      if (s1.GPS_alt < 1000000) {  // less than 1km
        transitioncount++;
        if (transitioncount > 5) {
          state = LANDING;
        }
      }
    }

    vTaskDelay(10000 / portTICK_PERIOD_MS);  // every 10 seconds
  }
}

//////////////////////////////////////////////////////////////////////////////////////////////
// Iridium
//////////////////////////////////////////////////////////////////////////////////////////////

void TaskIridium(void *pvParameters) {  // Task to manage iridium modem.

  while (xSemaphoreTake(mutex, portMAX_DELAY) == pdFALSE) {}  // wait to take the mutex

  IridiumSerial.begin(19200, SERIAL_8N1, 15, 33);
  delay(10);

  // Begin satellite modem operation
  Serial.println("Starting modem...");
  err = modem.begin();  // may take tens of seconds!!

  if (err != ISBD_SUCCESS) {
    Serial.print("Begin failed: error ");
    Serial.println(err);
    if (err == ISBD_NO_MODEM_DETECTED) {
      Serial.println("No modem detected: check wiring.");
      led_blink(100);
      //return;
    }
  } else {
    Serial.println("Iridium begin succeeded");
  }

  char version[12];
  err = modem.getFirmwareVersion(version, sizeof(version));
  if (err != ISBD_SUCCESS) {
    Serial.print("FirmwareVersion failed: error ");
    Serial.println(err);
    // return;
  }
  Serial.print("Firmware Version is ");
  Serial.print(version);
  Serial.println(".");
  xSemaphoreGive(mutex);
  modem.sleep();  // put the modem to sleep to save power

  TickType_t iridiumSent = xTaskGetTickCount();  // record the time when we enter the loop
  for (;;) {
    //Serial.println("Checking Iridium, time = " + String(xTaskGetTickCount()));
    if ((pdTICKS_TO_MS(xTaskGetTickCount() - iridiumSent) < 300000) || (pdTICKS_TO_MS(xTaskGetTickCount() - iridiumSent) > -300000)) {  // if it has NOT been > 5 minutes since we sent a message successfully
      // Do nothing
    } else {  // it is our time to send
      if (modem.isAsleep()) {
        err = modem.begin();
      }
      if (err != ISBD_SUCCESS) {
        Serial.print("Begin failed: error ");
        Serial.println(err);
      }
      if (err == ISBD_NO_MODEM_DETECTED) {
        Serial.println("No modem detected: check wiring.");
        led_blink(100);
      }
      //Serial.println("Iridium modem woken up successfully...");

      while (1) {  // no really wake up the modem
        err = modem.getSignalQuality(signalQuality);
        if (err != ISBD_SUCCESS) {
          modem.begin();  // try to wake it up again
          Serial.print("SignalQuality failed: error ");
          Serial.println(err);
          vTaskDelay(10000);
          //led_blink(10);
        } else {
          Serial.print("On a scale of 0 to 5, signal quality is currently ");
          Serial.print(signalQuality);
          Serial.println(".");
          break;
        }
      }

      //  if (singleSend == 0) {
      if (signalQuality > 2) {
        xSemaphoreTake(mutex, portMAX_DELAY);
        poll_sensors();
        assemble_json();
        IridiumPacket = Packet;  // copy the contents of the json packet into a separate string so we can free up the mutex
        xSemaphoreGive(mutex);
        const char *DataChar = IridiumPacket.c_str();
        err = modem.sendSBDText(DataChar);

        if (err != ISBD_SUCCESS) {
          Serial.print("##sendSBDText failed: error ");
          Serial.println(err);
          if (err == ISBD_SENDRECEIVE_TIMEOUT)
            Serial.println("##Transmission timeout.");
        } else {
          //singleSend = 1;
          Serial.println("##Transmission successful.");
          led_blink(2);
          iridiumSent = xTaskGetTickCount();
          modem.sleep();
        }
      }
    }
    // }
    vTaskDelay(10000 / portTICK_PERIOD_MS);  // check every 10 seconds if it is our time to send
  }                                          // end of infinite loop in task
}

void TaskLoRaAPRS(void *pvParameters) {
  TickType_t aprsSent = 0;  //xTaskGetTickCount(); send a packet asap
  for (;;) {
    xSemaphoreTake(mutex, portMAX_DELAY);
    poll_sensors();
    xSemaphoreGive(mutex);
    if ((pdTICKS_TO_MS(xTaskGetTickCount() - aprsSent) > 2000) || (pdTICKS_TO_MS(xTaskGetTickCount() - aprsSent) < 0)) {  // blank out the rest of our window
      bool isSendTime = (SATNUM == 1 && s1.GPS_sec >= 0 && s1.GPS_sec <= 10) || (SATNUM == 2 && s1.GPS_sec >= 20 && s1.GPS_sec <= 30) || (SATNUM == 3 && s1.GPS_sec >= 40 && s1.GPS_sec <= 50);

      if (isSendTime) {
        xSemaphoreTake(mutex, portMAX_DELAY);
        //////////////////////////////////////////////////////////////////////////////////////////////
        // Process GPS data for use with APRS
        double abs_d_lat = abs(s1.GPS_lat) / 10000000.0;
        int latDegrees = (int)abs_d_lat;
        double latMinutes = (abs_d_lat - latDegrees) * 60;

        char latDir = s1.GPS_lat >= 0 ? 'N' : 'S';
        char latStr[10];  // Buffer for latitude string including direction and null terminator
        snprintf(latStr, sizeof(latStr), "%02d%05.2f%c", latDegrees, latMinutes, latDir);

        // Convert to absolute value and degrees
        double abs_d_lon = abs(s1.GPS_lon) / 10000000.0;
        int lonDegrees = (int)abs_d_lon;
        double lonMinutes = (abs_d_lon - lonDegrees) * 60;

        char lonDir = s1.GPS_lon < 0 ? 'W' : 'E';
        char lonStr[11];  // Buffer for longitude string including direction and null terminator
        snprintf(lonStr, sizeof(lonStr), "%03d%05.2f%c", lonDegrees, lonMinutes, lonDir);

        lat = latStr;
        lon = lonStr;

        ////////////////////////////////////////////////////////////////////
        // SPEED
        // Convert speed to knots
        float speed_knots = s1.GPS_speed * 0.00194384;
        // Round to nearest whole number
        int rounded_speed = (int)round(speed_knots);
        // Convert to 3 digit character array, padded with leading zeros if necessary
        char speedStr[4];  // 3 digits + null terminator
        snprintf(speedStr, sizeof(speedStr), "%03d", rounded_speed);
        speed = speedStr;

        /////////////////////////////////////////////////////////////////////
        // COURSE

        // Convert course to degrees
        float course_degrees = s1.GPS_course / 100000.0;

        // Convert to 3 digit character array
        char courseStr[4];  // 3 digits + null terminator
        snprintf(courseStr, sizeof(courseStr), "%03d", (int)round(course_degrees));
        course = courseStr;

        /////////////////////////////////////////////////////////////////////
        // ALTITUDE

        // Example altitude in millimeters as int32_t
        // s1.GPS_alt = 2000000;
        if (s1.GPS_alt > 0) {
          alt_feet = s1.GPS_alt * 0.00328084;  // Convert altitude from millimeters to feet
        } else {
          alt_feet = 0;
        }
        if (alt_feet < 1)  // If the GPS altitude is unreliable, fallback to BME altitude
        {
          alt_feet = s1.BME_alt * 3.28084;  // Convert BME altitude from meters to feet
          //alt_fault = true;
        } else {
          //alt_fault = false;
        }

        char altStr[12];  // Increased buffer size for the formatted string

        // Ensure the altitude is rounded and formatted without decimals
        snprintf(altStr, sizeof(altStr), "/A=%06d", (int)round(alt_feet));

        /////////////////////////////////////////////////////////////////////
        // BATTERY VOLTAGE
        char voltageStr[8];  // Buffer size assumes max float size + null terminator
        snprintf(voltageStr, sizeof(voltageStr), "%.1f", s1.voltage);

        alt = altStr;  // alt now contains the altitude in feet followed by " ft"

        payload[0] = '!';
        unsigned char *payloadPtr = &payload[1];
        memcpy(payloadPtr, lat, 8);
        payload[9] = 0x2f;
        payloadPtr = &payload[10];
        memcpy(payloadPtr, lon, 8);
        payload[19] = 0x4f;
        payloadPtr = &payload[20];
        memcpy(payloadPtr, course, 3);
        payload[23] = 0x2f;
        payloadPtr = &payload[24];
        memcpy(payloadPtr, speed, 3);
        payloadPtr = &payload[27];
        memcpy(payloadPtr, alt, 9);


        Serial.print("Payload: ");

        for (int i = 0; i < 36; i++) {
          Serial.print((char)payload[i]);
        }

        unsigned char *arrayPtr = &ax25_packet[0];
        memcpy(arrayPtr, header_dest, 7);
        arrayPtr += 7;
        memcpy(arrayPtr, header_source, 7);
        arrayPtr += 7;
        memcpy(arrayPtr, header_digi, 7);
        arrayPtr += 7;
        memcpy(arrayPtr, header_ctrl, 1);
        arrayPtr += 1;
        memcpy(arrayPtr, header_PID, 1);
        arrayPtr += 1;
        memcpy(arrayPtr, payload, 36);
        arrayPtr += 36;

        // CALC FCS
        uint16_t mycrc = ax25crc16(ax25_packet, 59);

        Serial.println("");
        Serial.print("CRC first byte: ");
        Serial.print(mycrc & 0xFF, HEX);
        Serial.println("");
        Serial.print("CRC second byte: ");
        Serial.print((mycrc >> 8) & 0xFF, HEX);
        Serial.println("");

        ax25_packet[59] = (mycrc >> 8) & 0xFF;
        ax25_packet[60] = mycrc & 0xFF;

        // Serial.println("ax25_packet assembled.  Contents:");

        // for (int i = 0; i < 61; i++) {
        //   Serial.print(ax25_packet[i], HEX);
        //   Serial.print(" ");
        // }
        // Serial.println(" ");
        // Serial.println("Size: " + String(sizeof(ax25_packet)));

        digitalWrite(PTT_PIN, LOW);
        vTaskDelay(500 / portTICK_PERIOD_MS);
        // Serial.println("Stuff it");
        stuff_it(ax25_packet, 61, stuffed_ax25_packet, 128);

        // Serial.println("Stuffed ax25 packet:");
        //  for (int i = 0; i < 128; i++) {
        //   Serial.print("0x");
        //   Serial.print(stuffed_ax25_packet[i], HEX);
        //   Serial.print(", ");
        // }
        // Serial.println("Size: " + String(sizeof(stuffed_ax25_packet)));


        unsigned char *fxarrayPtr = &fx25_packet[0];
        // Serial.println("Copy correlation tag");
        memcpy(fxarrayPtr, correlation_tag, 8);
        fxarrayPtr += 8;
        // Serial.println("add stuffed ax25 packet");
        memcpy(fxarrayPtr, stuffed_ax25_packet, 128);
        fxarrayPtr += 128;

        unsigned char data[FX25_MAX_DATA + 1];
        const unsigned char fence = 0xaa;
        data[FX25_MAX_DATA] = fence;

        for (int i = 0; i < 128; i++) {
          data[i] = stuffed_ax25_packet[i];
        }

        int k_data_radio = 128;  // length of data part of fx.25 packet
        int shorten_by = FX25_MAX_DATA - k_data_radio;
        if (shorten_by > 0) {
          memset(data + 128, 0, shorten_by);
        }

        encode_rs_char(data, rs_parity);

        // Serial.println("RS copy");
        memcpy(fxarrayPtr, rs_parity, 32);

        Serial.println("fx25 packet:");
        for (int i = 0; i < 168; i++) {
          Serial.print("0x");
          Serial.print(fx25_packet[i], HEX);
          Serial.print(" ");
        }
        Serial.println("Size: " + String(sizeof(fx25_packet)));


        // send_char_NRZI now just fills the buffer, which we send to the RMT device afterwards
        // SEND FOUR FLAGS
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);

        for (int i = 0; i < 168; i++) {
          //for (int i = 0; i < 61; i++) {
          send_char_NRZI(fx25_packet[i], LOW);
          //send_char_NRZI(ax25_packet[i], HIGH);
        }
        // SEND FOUR FLAGS
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);
        send_char_NRZI(_FLAG, LOW);

        Serial.println("RMT buffer built, size = " + String(rmtCtr));

        rmtPtr = &rmt_buffer[0];

        rmtWriteAsync(OUT_PIN, rmtPtr, rmtCtr);
        rmtCtr = 0;

        int xmit_count = 0;
        while (!rmtTransmitCompleted(OUT_PIN)) {
          xmit_count++;
          if (xmit_count > 40) {  // sometimes the RMT hardware locks up; usually takes just over 10 (1.2 seconds or so)
            ESP.restart();
          }
          vTaskDelay(100 / portTICK_PERIOD_MS);
        }
        Serial.println("Transmit loop count: " + String(xmit_count));
        aprsSent = xTaskGetTickCount();
        vTaskDelay(500);
        digitalWrite(PTT_PIN, HIGH);

        // APRS is done

       
          poll_sensors();
          assemble_json();
          xSemaphoreGive(mutex);
        
        if (SATNUM != 3) {
          // send packet
          LoRa.beginPacket();
          LoRa.print(Packet);
          //LoRa.print(counter);
          LoRa.endPacket(true);
        }
        vTaskDelay(12000 / portTICK_PERIOD_MS);  // LoRa packet takes about 10s to send

        led_blink(3);

        // for (int i = 0; i < numchars; i++) {
        //   Serial.print("0x");
        //   Serial.print(packetreport[i], HEX);
        //   Serial.print(" ");
        // }
        // Serial.println(" ");
        numchars = 0;
      }
    }
    vTaskDelay(1000 / portTICK_PERIOD_MS);
  }
}


// Iridium callback to touch the watchdog timer occasionally so we don't panic, hopefully...
bool ISBDCallback() {
  vTaskDelay(10 / portTICK_PERIOD_MS);
  return true;
}

void loop() {
  // put your main code here, to run repeatedly:
  //Serial.println("loop");
}
