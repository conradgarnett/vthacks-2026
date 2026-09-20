# vthacks-2026: VisionOS

Assistive "digital senses" for blind and low-vision people. `visionos/` is the
sight module: a webcam on a pair of glasses (or a phone) feeds a scene model,
and the wearer hears what is around them, reads signs and labels, asks
questions by voice, remembers places, and is warned when a label names
something they are allergic to, with an email to their doctor.

## Run it from a fresh clone

Install Python 3.10 to 3.12 and Node LTS, then:

```
git clone https://github.com/conradgarnett/vthacks-2026.git
cd vthacks-2026/visionos
python run.py
```

The first run builds the environment, downloads the model weights and creates
`.env`; it then asks whether you will use a phone on the Wi-Fi or a browser on
this computer, and starts everything. `python run.py --check` only reports
what the machine can do. Keys are optional and go in `visionos/.env`; the
[VisionOS README](visionos/README.md) says which key does what, how to use
the app, and how it is measured.

- [visionos/README.md](visionos/README.md): running it, using it, the allergy
  scanner, the numbers.
- [visionos/hardware/README.md](visionos/hardware/README.md): the three-button
  watch (a LOLIN S2 Mini) and its serial protocol.
- [CLAUDE.md](CLAUDE.md): the engineering notes and the measured log, for
  whoever works on it next.

On Windows keep the checkout at a short path such as `C:\Users\<you>\vthacks-2026`;
long paths break Python imports there.
