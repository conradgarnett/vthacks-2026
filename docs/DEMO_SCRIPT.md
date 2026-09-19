# Demo script

Start it with `npm run demo` (add `-- --open` to open the browser, `-- --auto` to have it play every
scene for you). Everything you will see is **simulated**: the badge in the header says so and stays.
No microphone, camera or account is needed. To see the same six scenes with assertions and no browser:
`npm run demo:verify` (exit code 0 means every expectation held) or `npm run demo:cli` (colored text).

Panel names below are exactly as they appear on screen.

## 3-minute version (spoken script and exact clicks)

**0:00 Frame it.** _"SENSE is alt text for physical reality, plus an agent that can read it safely. It
doesn't try to perceive the world itself. It finds the parts of the world that already perceive
themselves, checks who they really are, and translates what they say into the senses you prefer. Every
line you'll see says where it came from and how far to trust it. Everything here is simulated, and the
badge says so."_
Point at the header badges: **SIMULATED WORLD**, **ANS-modeled (simulated)**, **MOCK AI**.

**0:20 Scene 1: Arrive (Blind persona).** Click **Arrive at Riverside Hall** in _Simulated world controls_.
_"SENSE discovered the agents in range and ran seven identity checks on each."_ In _Trust Inspector_ click
**riverside-hall.sim**: _"Seven passes, with evidence for every step. And look at the Disclosure Log:
profile data sent to remote agents, none. Each agent got its own throwaway session id."_

**0:50 Scene 2: Ask.** In _Ask about your surroundings_ click **Ask** (the sample question is already
filled in). _"The exit comes from the verified building map. The trolley in my way comes from the camera,
and it says inferred, with a confidence. Different provenance on each line."_

**1:15 Scene 3: Eat (Impaired taste persona).** Select the **Impaired taste** persona. In _Sensory profile_
type `peanut` in the allergens box and click **Save allergens**. In _TasteLens_ click **Arrive at Bella
Cucina**, choose **Sesame noodle bowl**, click **Describe and check allergens**. _"The photo says nothing
about peanut. The restaurant's verified list says peanut is in the sauce. The alert says verified, names
the restaurant, and says it overrides the photo. And nowhere does SENSE say safe. It can't know that."_

**1:50 Scene 4: Alarm and spoof (Deaf persona).** Select the **Deaf** persona, click **Fire alarm**, then
**Spoofed “false alarm”**. _"Visual and vibration, with direction: three o'clock. Someone just tried to
cancel it. That sender was rejected at step two and logged in Security events. No all-clear is ever
shown."_ Click **Acknowledge** on the alarm card.

**2:15 Scene 5: Smoke risk (Cannot smell persona).** Select **Cannot smell**, click **Smoke rising
(script)**. _"ScentGuard escalates from level one, each alert citing the rule, the reading and how old it
is. If the data goes stale, it says last known and unverified, and never assumes things got better."_
(In demo mode: **Skip 2 minutes (simulated time)** shows the stale downgrade instantly.)

**2:40 Scene 6: Touchless (Motor-limited persona).** In _Touchless_ choose **One-switch scanning**. _"No
mouse. Focus scans by itself. I press Space when it reaches what I want."_ Press Space on
**Acknowledge**, then on **Persona: Deaf**. _"Every action is recorded as coming from the input device,
never from a pointer."_ Press **Esc**: _"Pause tracking is always one key away."_

**2:55 Close.** _"Identity plus provenance plus honesty about what it can't know. That's the idea. It's a
prototype, not a safety system, and the next step is working with the people it's for."_

## 60-second version

1. (0:00) _"Simulated world, mock AI, ANS-modeled identity. It says so in the header."_ Click **Arrive at Riverside Hall**; open **riverside-hall.sim** in _Trust Inspector_: seven passes.
2. (0:15) Select **Deaf**, click **Fire alarm** then **Spoofed “false alarm”**: verified alarm with direction and vibration; spoof rejected at step two, no all-clear.
3. (0:35) Click **Ask**: verified map exit versus inferred trolley, each with its own badge.
4. (0:45) Select **Impaired taste**, save allergen `peanut`, **Arrive at Bella Cucina**, **Describe and check allergens**: verified peanut overrides the photo; the word "safe" never appears.
5. (0:55) _"Profile data sent to remote agents: none. Prototype, not a safety system."_ Point at the Disclosure Log.

## If something goes wrong

- **Nothing happens on a click:** check the header says _Connected to your local SENSE server_. Restart `npm run demo`.
- **No voice:** browsers may need one click first; use **Test sound and voice**. Captions always show what would be spoken.
- **Ports busy:** the server picks a free port and prints the URL.
