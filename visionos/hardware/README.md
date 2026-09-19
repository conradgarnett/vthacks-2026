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
| 5 | Voice | `4` | `VOICE` |
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
