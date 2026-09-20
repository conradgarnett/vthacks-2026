// Med-i-Glasses watch -- LOLIN S2 Mini with three Grove buttons.
//
// Replaces the five-button AVR sketch in ../watch_buttons for the ESP32-S2
// rig. Two differences matter and both are easy to lose an afternoon to:
//
//   ACTIVE HIGH. A Grove button drives SIG and carries its own pull-down, so
//   the pins are plain INPUT and a press reads 1. The AVR sketch used
//   INPUT_PULLUP and a press read 0. Wiring a Grove module to a sketch
//   expecting the other polarity looks exactly like a dead button.
//
//   THE BAUD NUMBER IS NOMINAL. The S2 Mini's USB is native CDC: there is no
//   UART between the chip and the browser, so 9600 and 115200 behave the
//   same. The client (client/src/main.ts) opens the port at 115200 and, if
//   the first bytes are not text, once more at 9600, so it also copes with
//   a board that does have a real UART (the older AVR sketch). A watch that
//   "does nothing" is wiring, polarity, or another program holding the port,
//   never the rate.
//
// WIRING
//   GPIO9   Grove button 1  -> SCAN   tap, or hold to STOP
//   GPIO1   Grove button 2  -> READ   tap, or hold to STOP
//   GPIO16  Grove button 3  -> ASK    tap only, any press length
//
//   Not GPIO37, and not 33 or 35: those share a power domain with the
//   SPI flash and PSRAM on this chip (the datasheet makes GPIO33-37's
//   supply switchable to VDD_SPI), and a button on 37 read as two pins
//   at once. 37 is also the default UART RX. 9, 1 and 16 are plain.
//
// Three buttons cover four commands: STOP is a hold, so it stays reachable
// from whichever button is under the thumb when speech is running.
//
// ASK is deliberately exempt from the hold. It toggles listening in the app
// -- tap to start talking, tap again to stop -- so it has to answer every
// press whatever its length. A deliberate press on a wrist button runs well
// past any hold threshold worth having, and "I pressed Ask and it did not
// listen" is the worst failure of the three.
//
// IF NOTHING HAPPENS, in this order:
//   1. Serial Monitor at 115200 (any rate works on native USB): a banner
//      should name each pin after a reset. No banner means the sketch is not
//      running, or the board was not reset since the monitor opened.
//   2. The banner prints live pin levels for three seconds. Press each button
//      and watch its number go 0 -> 1. Nothing moving is wiring; the wrong
//      pin moving is a swapped cable.
//   3. The LED blinks on every accepted press, with no computer attached.
//   4. All of that working but the app silent: the browser has not been given
//      the port. Press the Watch button in the page and pick it.

#include <Arduino.h>

const uint8_t BUTTON_COUNT = 3;
const uint8_t PINS[BUTTON_COUNT] = {9, 1, 16};
const char* COMMANDS[BUTTON_COUNT] = {"SCAN", "READ", "ASK"};

// Grove modules bounce less than a bare switch, but a thumb on a wearable
// still double-taps; 35 ms is below a deliberate second press.
const unsigned long DEBOUNCE_MS = 35;
// Measured on the rig on 2026-09-20: one tap chattered into four SCAN
// lines within 250 ms (the release bounces far longer than 35 ms, likely a
// connector). After a command is sent, the button is ignored for this
// long, so one press is one command whatever the contact does.
const unsigned long LOCKOUT_MS = 400;
// Long enough that a deliberate press is never mistaken for a hold, short
// enough that stopping speech still feels immediate.
const unsigned long HOLD_MS = 900;
// ASK toggles listening in the app, so it must fire on every press however
// long. Index into PINS/COMMANDS.
const uint8_t ASK_INDEX = 2;
const unsigned long BLINK_MS = 60;
// Nominal on native USB (see the header); the client opens at this rate
// first and falls back to 9600 if the bytes are not text.
const unsigned long BAUD = 115200;

bool wasDown[BUTTON_COUNT];
unsigned long pressedAt[BUTTON_COUNT];
unsigned long lastChange[BUTTON_COUNT];
bool holdSent[BUTTON_COUNT];
unsigned long lockoutUntil[BUTTON_COUNT];
unsigned long blinkUntil = 0;

void blink() {
  digitalWrite(LED_BUILTIN, HIGH);
  blinkUntil = millis() + BLINK_MS;
}

void setup() {
  Serial.begin(BAUD);
  pinMode(LED_BUILTIN, OUTPUT);

  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    // Plain INPUT: the Grove module supplies its own pull-down. An internal
    // pull-up here would hold every pin high and report a permanent press.
    pinMode(PINS[i], INPUT);
    wasDown[i] = false;
    pressedAt[i] = 0;
    lastChange[i] = 0;
    holdSent[i] = false;
    lockoutUntil[i] = 0;
  }

  // The S2 has native USB and enumerates after setup() begins, so a banner
  // printed immediately is lost. Wait briefly, but never block a board
  // running on battery with nothing attached.
  unsigned long waitUntil = millis() + 1500;
  while (!Serial && millis() < waitUntil) {}

  Serial.println();
  // Starts with "Ready" because that is the word the client shows as the
  // watch's banner (client/src/main.ts, listenToWatch).
  Serial.println(F("Ready - SCAN(9) READ(1) ASK(16), hold to STOP. Med-i-Glasses watch, S2 Mini, active HIGH."));
  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    Serial.print(F("  GPIO"));
    Serial.print(PINS[i]);
    Serial.print(F(" -> "));
    Serial.print(COMMANDS[i]);
    // A Grove button reading HIGH at rest is stuck, or SIG is on the wrong
    // pin. Either way the command would fire continuously.
    if (digitalRead(PINS[i]) == HIGH) Serial.print(F("   *** HIGH AT BOOT"));
    Serial.println();
  }
  Serial.println(F("  hold SCAN or READ ~0.9 s -> STOP"));
  Serial.println(F("  ASK toggles listening: tap to talk, tap again to stop"));

  // Three seconds of live levels, so a button can be checked by pressing it
  // and watching its column, before any command is sent.
  Serial.println(F("  levels for 3 s -- press each button:"));
  unsigned long until = millis() + 3000;
  while (millis() < until) {
    Serial.print(F("    "));
    for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
      Serial.print(COMMANDS[i]);
      Serial.print('=');
      Serial.print(digitalRead(PINS[i]));
      Serial.print(' ');
    }
    Serial.println();
    delay(250);
  }
  Serial.println(F("  listening."));
}

void loop() {
  unsigned long now = millis();

  if (blinkUntil && now > blinkUntil) {
    digitalWrite(LED_BUILTIN, LOW);
    blinkUntil = 0;
  }

  for (uint8_t i = 0; i < BUTTON_COUNT; i++) {
    bool down = digitalRead(PINS[i]) == HIGH;  // Grove: pressed reads HIGH

    // Just sent a command for this button: whatever the contact does for
    // the next LOCKOUT_MS is the same press, not a new one.
    if (now < lockoutUntil[i]) {
      wasDown[i] = down;
      lastChange[i] = now;
      continue;
    }

    if (down != wasDown[i] && now - lastChange[i] >= DEBOUNCE_MS) {
      lastChange[i] = now;
      wasDown[i] = down;

      if (down) {
        pressedAt[i] = now;
        holdSent[i] = false;
      } else if (!holdSent[i]) {
        // Released before the hold threshold, so it was a tap. Sent on
        // release rather than on press so a hold never also sends the tap.
        Serial.println(COMMANDS[i]);
        blink();
        lockoutUntil[i] = now + LOCKOUT_MS;
      }
      continue;
    }

    // Still held past the threshold: stop speech without waiting for release,
    // because the point of STOP is that it is immediate. ASK never gets here.
    if (i != ASK_INDEX && down && !holdSent[i] && now - pressedAt[i] >= HOLD_MS) {
      holdSent[i] = true;
      Serial.println(F("STOP"));
      blink();
      lockoutUntil[i] = now + LOCKOUT_MS;
    }
  }
}
