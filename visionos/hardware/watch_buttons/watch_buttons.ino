// Watch buttons for VisionOS.
//
// Five momentary buttons, each wired from a pin to ground; the internal
// pull-ups do the rest. A press sends one command two ways at once:
//
//   1. as a line on the USB serial port ("READ\n"), which the app reads
//      after someone presses the Watch button in Chrome and picks the port.
//      Works on every Arduino.
//   2. on boards with native USB (Leonardo, Micro, Pro Micro, Due, Zero,
//      MKR, Nano 33 IoT) also as a key press, which the app understands
//      with nothing in between: 1 scan, 2 read, 3 ask, 4 voice, 0 stop.
//
// Only presses are sent, never holds: the app toggles listening on a second
// press of Ask, and everything else is one shot.

#include <Arduino.h>

#if defined(USBCON)
#include <Keyboard.h>
#define HAS_KEYBOARD 1
#else
#define HAS_KEYBOARD 0
#endif

const uint8_t BUTTON_COUNT = 5;
const uint8_t PINS[BUTTON_COUNT] = {2, 3, 4, 5, 6};
const char* COMMANDS[BUTTON_COUNT] = {"SCAN", "READ", "ASK", "VOICE", "STOP"};
const char KEYS[BUTTON_COUNT] = {'1', '2', '3', '4', '0'};
const unsigned long DEBOUNCE_MS = 40;

bool wasDown[BUTTON_COUNT];
unsigned long lastChange[BUTTON_COUNT];

void setup() {
  Serial.begin(9600);
  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    pinMode(PINS[i], INPUT_PULLUP);
    wasDown[i] = false;
    lastChange[i] = 0;
  }
#if HAS_KEYBOARD
  Keyboard.begin();
#endif
}

void loop() {
  unsigned long now = millis();
  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    bool down = digitalRead(PINS[i]) == LOW;
    if (down == wasDown[i] || now - lastChange[i] < DEBOUNCE_MS) continue;
    wasDown[i] = down;
    lastChange[i] = now;
    if (!down) continue;
    Serial.println(COMMANDS[i]);
#if HAS_KEYBOARD
    Keyboard.write(KEYS[i]);
#endif
  }
}
