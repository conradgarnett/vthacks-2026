// Watch buttons for VisionOS.
//
// Five momentary buttons, each wired from a pin to ground; the internal
// pull-ups do the rest. A press sends one command two ways at once:
//
//   1. as a line on the USB serial port ("READ\n"), which the app reads
//      after someone presses the Watch button in Chrome and picks the port.
//      Works on every Arduino. 9600 baud, matching client/src/main.ts.
//   2. on boards with native USB (Leonardo, Micro, Pro Micro, Due, Zero,
//      MKR, Nano 33 IoT) also as a key press, which the app understands
//      with nothing in between: 1 scan, 2 read, 3 ask, 4 voice, 0 stop.
//
// Only presses are sent, never holds: the app toggles listening on a second
// press of Ask, and everything else is one shot.
//
// WIRING
//   button 1 (SCAN)  pin 2 -- button -- GND
//   button 2 (READ)  pin 3 -- button -- GND
//   button 3 (ASK)   pin 4 -- button -- GND
//   button 4 (VOICE) pin 5 -- button -- GND
//   button 5 (STOP)  pin 6 -- button -- GND
// No resistors needed; INPUT_PULLUP handles it. Wire to GND, not to 5V.
//
// IF NOTHING HAPPENS, in this order:
//   1. Open Serial Monitor at 9600. You should see a banner naming each pin.
//      No banner means the sketch is not running or the baud is wrong.
//   2. Press a button. The built-in LED blinks and a command line appears.
//      LED but no line means the serial port is taken by another program.
//      Neither means the button or its ground wire is the problem.
//   3. Both working but the app does nothing: the browser has not been given
//      the port. Press the Watch button in the page and pick it.
// The banner also reports any button that is already down at boot, which is
// what a stuck switch or a miswired pin looks like.

#include <Arduino.h>

// USBCON says the board *can* act as a keyboard; __has_include says the
// library to do it is actually installed. Testing only the first fails to
// compile on a Leonardo or Micro with a stock library folder, which is the
// one place keyboard emulation matters.
#if defined(USBCON) && defined(__has_include)
#if __has_include(<Keyboard.h>)
#include <Keyboard.h>
#define HAS_KEYBOARD 1
#endif
#endif
#ifndef HAS_KEYBOARD
#define HAS_KEYBOARD 0
#endif

// Keyboard emulation types into whichever window has focus, so a press with
// the browser in the background lands in a terminal or an editor instead.
// Set to 0 to send commands over serial only, which is always safe.
#define SEND_KEYSTROKES 1

const uint8_t BUTTON_COUNT = 5;
const uint8_t PINS[BUTTON_COUNT] = {2, 3, 4, 5, 6};
const char* COMMANDS[BUTTON_COUNT] = {"SCAN", "READ", "ASK", "VOICE", "STOP"};
const char KEYS[BUTTON_COUNT] = {'1', '2', '3', '4', '0'};
const unsigned long DEBOUNCE_MS = 40;
// Long enough to see, short enough not to delay a second press.
const unsigned long BLINK_MS = 60;

bool wasDown[BUTTON_COUNT];
unsigned long lastChange[BUTTON_COUNT];
unsigned long blinkUntil = 0;

void setup() {
  Serial.begin(9600);
  pinMode(LED_BUILTIN, OUTPUT);

  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    pinMode(PINS[i], INPUT_PULLUP);
    wasDown[i] = false;
    lastChange[i] = 0;
  }

  // A moment for the pull-ups to settle before reading them, so the
  // stuck-button report below is not just a floating pin.
  delay(50);

  // Native-USB boards enumerate after setup() starts, so a banner printed
  // immediately is lost. Wait briefly, but never block a board running on
  // battery with no host attached.
  unsigned long waitUntil = millis() + 1500;
  while (!Serial && millis() < waitUntil) {}

  Serial.println();
  Serial.println(F("VisionOS watch ready. 9600 baud."));
  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    Serial.print(F("  pin "));
    Serial.print(PINS[i]);
    Serial.print(F(" -> "));
    Serial.print(COMMANDS[i]);
    // A button reading LOW at boot is held down, which in practice means a
    // stuck switch or a pin wired to ground by mistake.
    if (digitalRead(PINS[i]) == LOW) Serial.print(F("   *** DOWN AT BOOT"));
    Serial.println();
  }
#if HAS_KEYBOARD && SEND_KEYSTROKES
  Serial.println(F("  keystrokes: on (they go to the focused window)"));
  Keyboard.begin();
#elif SEND_KEYSTROKES
  // Either the board has no native USB, or the Keyboard library is not
  // installed. Serial still works, so say which rather than failing.
  Serial.println(F("  keystrokes: off (no Keyboard library, or no native USB)"));
#else
  Serial.println(F("  keystrokes: off (serial only)"));
#endif
}

void loop() {
  unsigned long now = millis();

  if (blinkUntil && now > blinkUntil) {
    digitalWrite(LED_BUILTIN, LOW);
    blinkUntil = 0;
  }

  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    bool down = digitalRead(PINS[i]) == LOW;
    if (down == wasDown[i] || now - lastChange[i] < DEBOUNCE_MS) continue;
    wasDown[i] = down;
    lastChange[i] = now;
    if (!down) continue;

    // Visible proof the button and its wiring work, with no computer
    // attached and nothing to read.
    digitalWrite(LED_BUILTIN, HIGH);
    blinkUntil = now + BLINK_MS;

    Serial.println(COMMANDS[i]);
#if HAS_KEYBOARD && SEND_KEYSTROKES
    Keyboard.write(KEYS[i]);
#endif
  }
}
