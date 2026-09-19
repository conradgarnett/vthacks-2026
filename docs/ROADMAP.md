# Roadmap

None of this is built. It is what would have to be true for SENSE to be more than a prototype. The
first and most important item is people, not code.

## 1. Co-design and evaluation with disabled users (first priority)

Everything in this repository was designed **without** disabled users. That is the biggest gap. Before
any further engineering: work with blind, low-vision, deaf, hard-of-hearing, motor-limited, anosmic and
taste-impaired people, paid and credited, to test whether the personas, the wording, the urgency scale,
the haptic patterns, the earcons, and the touchless flows are actually usable and actually wanted.
Expect to throw parts away. Measure with real tasks, not opinions about the demo.

## 2. Live ANS integration

Replace the simulated registry, CA and log with a real ANS registry: ACME/DNS-based domain-control
validation, the dual-certificate model, real transparency-log receipts and consistency proofs, and
revocation as a service. Requires reading the actual specifications and OpenAPI, which the
`LiveAnsClient` stub deliberately does not fake. Add DNSSEC-aware resolution and report its absence
honestly as a lower trust tier.

## 3. Real-world sensors and publishers

Adapters for real fire-panel and building-management interfaces, real air-quality networks, restaurant
allergen systems, and transit feeds, each behind the same Sense Card. Solve data quality, calibration,
maintenance and who is accountable when a feed is wrong.

## 4. Wearables and haptics

Bone-conduction and spatial audio, haptic wearables, and a proper direction language for vibration
learned with users. Replace the phone-vibration stand-in.

## 5. Indoor positioning

Where the user actually is (and which way they face) currently comes from the simulation. Real indoor
positioning (BLE, UWB, visual) with an explicit uncertainty that flows into every spatial statement.

## 6. Better perception fallbacks

A vendored, evaluated sound-event model behind the existing `AudioClassifier` interface; real
microphone arrays for direction; a vetted vision model behind `VisionProvider` with measured error
rates; the webcam head, eye and hand tracker (MediaPipe-class) behind `PointerSource`, with real
calibration. Report accuracy per condition; never hide failure.

## 7. Crowd-sourced hazard reports with reputation

Let users report "this exit is blocked", weighted by an accountable reputation, cross-checked against
verified sources and shown as its own tier. Design for abuse from the start.

## 8. Cross-language support

Speech, captions and sanitizer rules in more languages, including right-to-left text and sign-language
video options. The sanitizer and safety guard currently target English wording and must be extended and
tested per language.

## 9. Publisher incentives and onboarding

Learn why a business would publish: liability reduction, legal accessibility obligations, customer
loyalty, or insurance. Measure real onboarding time with real businesses (our 2-second machine time is
not that). Provide hosted tooling, monitoring, and a revocation and incident process.

## 10. Standards engagement for a Sense Card

The Sense Card is SENSE's own draft. Take it to the agent-identity and accessibility communities (ANS,
MCP/A2A discussions, accessibility standards bodies), align vocabulary, and version it in the open.

## 11. Protocol and security hardening

RFC 6962-style consistency proofs; sign requests with the session's ephemeral key to stop in-session
replay; automated reaction to a misbehaving verified publisher; an independent security review and a
formal threat-model review; fuzzing of the parsers and sanitizer.

## 12. Regulatory and liability considerations

A system that tells people about fire, air and allergens sits close to regulated territory. Before any
real-world use: legal advice on medical-device and safety-system classification in each jurisdiction,
clear allocation of liability between SENSE, publishers and users, incident reporting, accessibility
law obligations (SENSE's own interface targets WCAG 2.2 AA but is not certified), and privacy law for
disability-related data. Until then: **prototype only, not for life-safety decisions.**
