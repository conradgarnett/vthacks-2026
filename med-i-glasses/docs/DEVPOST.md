## Inspiration

Most "AI describes the world" demos are secretly built for sighted people to
watch. You point a phone at a room, a confident paragraph appears on screen,
everyone nods. We started with a different question: what changes if the
person holding the phone can't see the screen?

The answer changed everything, and it came down to two rules.

**Saying something wrong is worse than saying nothing.** If you can see, you
glance at the sign and catch the mistake. If you can't, you just believe it.
So being quiet is okay. Being confidently wrong is not.

**Nothing on the screen counts.** Connected, listening, thinking, broken —
all of it has to reach you as speech, a sound, or a buzz. A spinner isn't a
status if you can't see it.

Almost every decision below is one of those two rules, turned into code.

## What it does

Point a phone at a room — or clip a small webcam to a pair of glasses — and
hear what's there.

| Button | What happens |
|---|---|
| **Scan** | Tells you what's around you, closest things first |
| **Read** | Reads any text in view: a sign, a door number, a package, a pill bottle |
| **Ask** | Ask a question out loud, or say "take me to the door" |
| **Voice** | Changes the speaking voice |
| **Stop** | Quiet, right now |

Things are described the way you'd describe them to a friend: *"a chair at
your two o'clock, about four steps."* And when you ask to be taken to the
door, you don't get directions — you get a sound that actually comes from
where the door is, pinging faster as you get closer. Telling someone "two
o'clock" makes them do math. A sound coming from their right doesn't.

**Pill bottles get special treatment.** Every other mistake this thing could
make costs you a little convenience. Misreading a dose costs you your health.
"TAKE 2 TABLETS" heard as "take 3" is an overdose instruction, and you can't
double-check the label. So when Med-i-Glasses sees something that looks like
a prescription, it gets stricter: it only says a number out loud if several
photos of the label independently agree on it. Otherwise it says it can't
read the label safely and tells you to get someone to check.

That makes it look *worse* on a spreadsheet — it reads fewer doses out loud.
We tested the version without that rule and it got the dose wrong on 7% of
labels. We'll take the boring version.

## How we built it

Your phone's camera streams to a Python backend that keeps a running picture
of the room around you — not a series of disconnected snapshots, but
something that remembers the chair is still there after you look away.

**We had to teach it what to look for.** The standard object-recognition
dataset knows 80 things, including "frisbee" and "hair drier." It does not
know *door*, *stairs*, or *handrail* — the three things a blind person most
needs found. So we used a detector you can just hand a list of words to. Nice
surprise: it was also faster than the one we replaced.

**Distances come from knowing how big things are.** We tried an AI depth
model, but it only tells you what's nearer and farther, not how far in actual
feet. Guessing the scale wrong means confidently telling someone a wall is
two meters away when it's five — exactly the failure rule one forbids. So we
gave it a table of real-world sizes instead. A door is about two meters tall;
if it takes up a third of the frame, we can do the math. Things we don't have
a size for get a direction and no distance, because a made-up distance is
worse than none.

**The safety warnings don't use AI at all.** Obstacle alerts are plain,
boring code running on the room model. That's on purpose: a warning that
something's in your way can't be waiting on an internet request. It also
can't repeat itself four times a second, because a thing that nags is a thing
you switch off, and a switched-off tool protects nobody.

**Reading text is a whole pipeline, not one call.** Press Read and it grabs
three photos in a burst, reads all three, and keeps only the words that show
up in all of them. Junk moves around between photos; real text stays put.
Then it throws out anything that doesn't look like actual language, and reads
the rest back in the order you'd read it.

**The glasses version.** A webcam on a pair of glasses is the eye, and a
little five-button watch we built is the whole interface — Scan, Read, Ask,
Voice, Stop. No screen involved anywhere.

**We built the grading system before we trusted anything.** We wrote a
program that makes fake signs in 38 fonts, at 8 sizes, in 6 colour schemes,
then messes them up the way a real photo would: blurry, tilted, glared, grainy.
Then it grades us. Crucially it grades *hallucinations* separately from
mistakes, because "read it wrong" and "made it up" are not the same sin.

## Challenges we ran into

**A crash that pretended to be a network problem.** The backend kept dying
with no error message. It looked exactly like the phone losing Wi-Fi, so we
spent hours in the networking code. The real cause was two parts of the AI
trying to use the Mac's graphics chip at the same time, which kills the
program instantly and silently.

**Then we overcorrected.** Having learned "only one thing at a time," we put
the text reader on that same queue too. It was safe — and it now had to wait
behind everything else. One test read took **19.9 seconds**. Given its own
lane, the identical read took **311 milliseconds**.

**Safari lied to us.** The camera only works on a secure connection, which
means certificates. Safari will not warn you when it rejects a homemade
certificate on a live connection — it just silently fails and retries
forever, with no error anywhere on either machine. We rearranged the whole
setup so everything hides behind one certificate.

**Zooming in made reading worse.** Small text was getting missed, so we
cropped and re-read the pieces. Our scores went *down*. Turns out the crop
re-reads a word the full photo already nailed and produces a garbled twin of
it — "Departures" came back as "DLpartiirL Departures." Now it only crops
when the first pass comes back nearly empty.

**Two bad reads of the same word don't look alike.** We were removing
duplicates by comparing text, which failed, because garbled text garbles
differently every time. We had to match by *where the words were* on the
sign instead.

## Accomplishments that we're proud of

- **It never made things up.** Across all our sign tests, zero hallucinations.
  Of everything we measured, that's the number we actually care about.
- **On our best setup it reads signs perfectly 9 times out of 10**, in about
  a quarter of a second.
- **We can prove our improvements were real**, because we built the grading
  system before we built the thing it grades.
- **365 tests**, including one written specifically to catch the crash that
  nearly ended the project.
- **It degrades instead of breaking.** No API key, no internet, no fancy
  graphics chip? Each one costs you a feature and it tells you so, out loud,
  instead of just failing.
- **We built a screen for the judges**, since the actual product is audio and
  they'd have no way to see it working.

## What we learned

**Build the grader first.** Our first version of text reading looked fine and
was quietly bad. The test we were using asked "is the word in there
somewhere?" — which happily passes "Room 204B" read as "Room 2048."

**Knowing when to shut up is a feature.** The instinct is to fill every
silence with description. The better version knows when it doesn't know, says
so plainly, and stops talking.

**When two of us disagreed, we ran both versions through the grader** and
kept whichever scored better. A measurement instead of an argument. That one
habit saved us an entire evening of the kind of debate hackathons are famous
for.

## What's next for Med-i-Glasses

- **Turn the obstacle warnings back on.** They're built and tested but
  currently switched off, because the list of things it looks for still needs
  tuning and a false alarm talks over everything else. Rule one applies to
  warnings too.
- **Small text is still hard.** A normal sign from fifteen meters away is
  about fourteen pixels tall before any blur, and we mostly can't read it.
  That may be a limit of the photo rather than a limit of our code.
- **Put it in front of blind users.** Everything above came from two design
  rules and a test harness. Neither one is a person telling us we got it
  wrong.
- **Cut the cord.** The glasses currently run through a laptop. Getting it
  onto the phone alone removes the last piece of luggage.
