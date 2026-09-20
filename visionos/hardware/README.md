# Glasses, watch and Arduino

The wearable form of VisionOS: a USB webcam on a pair of glasses is the eye,
this computer runs the app in Chrome, and a watch with five buttons is the
whole interface. Nothing on the screen is load-bearing.

## The camera on the glasses

Plug the webcam in, open http://localhost:5174 in Chrome and press Start.
The app lists the cameras and, on a computer, prefers the one that is not
built in, then says which one it is using ("Using USB Camera."). If it picks
the wrong one, pin it by part of its name in the address:

```
http://localhost:5174/?camera=usb
```

## The buttons on the watch

Five momentary buttons, each between a pin and ground. `watch_buttons.ino`
uses the internal pull-ups, so no resistors.

| pin | button | key it sends | serial line |
|---|---|---|---|
| 2 | Scan | `1` | `SCAN` |
| 3 | Read | `2` | `READ` |
| 4 | Ask (press again to stop listening) | `3` | `ASK` |
| 5 | spare (the app ignores it; it used to change the voice) | `4` | `VOICE` |
| 6 | Stop speaking | `0` | `STOP` |

Two ways for the presses to reach the app; the sketch does both at once.

**As a keyboard.** Boards with native USB (Leonardo, Micro, Pro Micro, Due,
Zero, MKR, Nano 33 IoT) type the key. Chrome sees a keyboard; nothing else is
needed, and the keys work from a real keyboard too, which is how to test
without the watch. Any mapped key also presses Start.

**Over the serial cable.** Every board, including an Uno, prints the command
as a line at 9600 baud. In the app press the Watch button (it appears only
in browsers with Web Serial: Chrome and Edge on a computer), pick the
Arduino's port, and the app says "Watch connected." Presses arrive as lines
and run the same actions as the buttons on screen.

The serial route is the one to use on an Uno, and the fallback if the
keyboard route ever types into the wrong window: a serial line only ever
reaches this page.

## The three-button watch: LOLIN S2 Mini with Grove buttons

The board that exists (Conrad's, 2026-09-20) is a LOLIN S2 Mini (ESP32-S2)
with three Grove button modules, running `button_commands.ino` on his
machine (`watch_s2/watch_s2.ino` here is the earlier sketch for the same
board and differs in two ways: 9600 baud, and STOP on a long press). What
the board on the bench actually does:

- Native USB serial, **115200 baud**, VID `0x303A` PID `0x80C2`. The port
  name changes across resets; the app asks you to pick it once and reopens
  it by itself afterwards.
- One line per press, on the press, nothing on release: `SCAN`, `READ`,
  `ASK`. Pins: GPIO9 scan, GPIO1 read, GPIO16 ask (ASK was on GPIO37
  until 2026-09-20 and read as two pins on one press: GPIO33 to 37 share a
  power rail with the SPI/PSRAM group on this ESP32-S2FN4R2, so avoid 33,
  35 and 37 for buttons, as well as 19/20, the native USB pair, and 15,
  the LED; and keep Tools > PSRAM disabled in the IDE). There is no Stop
  button; Stop is key `0` or the button on screen.
- After a physical reset it prints one banner, `Ready - SCAN(9) READ(1)
  ASK(16)`. Opening the port does not reset the board, so the banner is
  not a handshake. The app shows "Watch ready" when it sees any line that
  starts with Ready, whatever pins it names.
- A line starting `ignored:` (`ignored: READ(1) + ASK(16)`, naming the
  pins that fired together) is a diagnostic from the firmware's guard, not
  a command; the app logs it to the console and moves on. In normal use it
  never appears. The rig's one fault so far, one press reading as two
  pins, went away after a reflash without a code change, so it was
  physical and can come back: if the app suddenly stops receiving presses
  or `ignored:` lines appear, that is the board or a connector, not the
  app; check the hardware before the client.
- The port is exclusive: close the Arduino IDE's Serial Monitor (and the
  `serial-monitor` daemon it leaves behind) or the app reads nothing.
- The port vanishes for a second or two on a reset or a reflash and comes
  back; the app says "The watch was unplugged" and then "Watch
  reconnected."

The app opens the port at 115200 and, if the first bytes are not text,
once more at 9600 for the older sketches; that first press is lost, and it
says "Press again."

**The gotcha, in Conrad's words:** Grove buttons are powered modules that
drive their signal pin high when pressed, not bare switches to ground. With
`INPUT_PULLUP` they read backwards, and with a bare `INPUT` an unpowered
module leaves the pin floating, so all three "buttons" fire together in
clean periodic bursts that look exactly like a short. `INPUT_PULLDOWN`
fixes it. Suspect floating or unpowered inputs before suspecting wiring.
