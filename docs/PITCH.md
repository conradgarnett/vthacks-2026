# SENSE: pitch

> A hackathon-grade prototype. Everything demonstrated is simulated. This page makes only claims the
> repository supports.

## The problem

Assistive technology usually tries to _perceive_ the world for a person: a camera that describes, a
microphone that captions. That has two limits. Perception by a phone is a guess, and a guess presented as
fact is dangerous, especially about fire alarms, air, or allergens. And the things that matter most
(a building's fire panel, a kitchen's allergen list, a city's air sensors) already _know_ the answer but
that knowledge is not reachable by a person's assistant, or not trustworthy if it is.

## The insight

**SENSE should not be the thing that perceives the world. It should be the thing that finds and trusts
the parts of the world that already perceive themselves.**

- Places and services publish their own agents under domains they control, with a machine-readable
  **Sense Card** (alt text for physical reality: what they can tell you, from what basis, how fresh).
- A personal SENSE agent **discovers** them, **verifies** who they are, **talks** to them with a
  throwaway identity and minimum scope, and **translates** the result into the user's own senses:
  speech, spatial audio, visual cards, haptics, or gaze and switch input.
- Cameras and microphones are the **fallback** when no verified source exists, and are always labelled
  as inference, with a confidence.
- Every statement carries visible **provenance**: verified, inferred, unverified, or rejected.

Think of it as alt text for physical reality, plus an agent that can read it safely.

## What you can see today (`npm run demo`, or `npm run demo:verify`)

Six scenes, each with assertions, all simulated and offline:

1. **Arrive**: seven identity checks per agent, shown step by step; no profile data sent.
2. **Ask**: verified map and inferred camera merged, each statement with its own provenance.
3. **Eat**: a verified restaurant list overrides a photo that says nothing about peanut; "safe" is never said.
4. **Alarm and spoof**: a real alarm reaches a Deaf user as visual plus vibration with direction; a spoofed "false alarm" is rejected and logged.
5. **Smoke risk**: transparent rules escalate 1 to 3 with evidence and freshness; stale data is downgraded and admitted.
6. **Touchless**: acknowledge and switch persona using only a switch or dwell input.

Plus eight attack scenarios (impersonation, revocation, silent code swap, unlogged agent, spoofed
all-clear, prompt injection from a verified agent, stale data, flooding), each built and tested.

## Why identity matters here

For most apps a wrong answer is an annoyance. Here a false all-clear can hurt someone. Anyone can claim
to be "the fire panel". The ANS-style idea, anchoring an agent's identity to a domain name with
certificates and a transparency log, gives a principled way to say _this is really riverside-hall.sim,
running the code it registered_. SENSE **models** that here; it does not claim ANS compliance and does not
yet integrate with a real registry. And identity is not trust in content: even a verified agent's text is
validated, sanitized and treated as data, which the prompt-injection scenario demonstrates.

## Why publishers might adopt it (a hypothesis, not a finding)

Publishing an accessibility agent could be cheap. In this repository `npm run create-sense-agent`
generates a working agent, a Sense Card and a self-check; generating and verifying one took about 2
seconds of machine time (see [PUBLISH_AN_AGENT.md](PUBLISH_AN_AGENT.md)). We did **not** measure how long
it takes a real business to fill in its own data, and we have not tested whether publishers see enough
benefit to do so. That is one of the first things to learn.

## Honest state

- Real: the certificates, signatures, Merkle proofs, validation, sanitization, routing, renderers, DSP, input-device logic, and accessibility work.
- Simulated: the world, the registry and DNS, the sensors, and the "AI" (mock fixtures offline).
- Not done: a real ANS integration, real sensors, wearables, indoor positioning, a webcam tracker model, evaluation with disabled users, and any security audit.
- **It is not a medical device and not a certified safety system.**

## Roadmap

Live ANS integration, real sensors and wearables, indoor positioning, crowd-sourced hazard reports with
reputation, more languages, publisher incentives, a Sense Card standards effort, and above all
**co-design and evaluation with disabled users**. See [ROADMAP.md](ROADMAP.md).

## The ask

1. **Co-design partners**: blind, low-vision, deaf, hard-of-hearing, motor-limited, anosmic and taste-impaired people, and their organisations, to tell us what is wrong before anything is built on this.
2. **Pilot publishers** willing to publish a Sense Card for a real place, and to tell us what it cost.
3. **Standards and security review**: people working on agent identity to critique the Sense Card and the way SENSE models ANS.
