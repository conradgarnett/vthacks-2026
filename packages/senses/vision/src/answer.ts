import { clockPosition, describeDirection, type IndoorMapPayload, type Point2, type Spatial, type Tier } from '@sense/protocol';
import type { SceneMemory } from './memory';
import { angularDiff, exitRoutes, type ExitRoute } from './routing';

/** Where a verified map came from. Supplied by the broker's data record; vision never talks to publishers itself. */
export interface MapSource {
  fqdn: string;
  label: string;
  tier: Tier;
  simulated: boolean;
  agentVersion: string;
  verifiedAt: string;
  evidence: string[];
  map: IndoorMapPayload;
}

export interface Statement {
  /** Full sentence. */
  text: string;
  /** Speakable core; provenance is appended by the percept builder for safety statements. */
  core: string;
  tier: Tier;
  /** 'device-camera' | 'sense-local' | map source fqdn */
  source: string;
  sourceLabel: string;
  confidence?: number;
  spatial?: Spatial;
  urgency: number;
  safety: boolean;
  evidence: string[];
  simulated: boolean;
  agentVersion?: string;
  verifiedAt?: string;
}

export type Intent = 'exit' | 'find' | 'sign' | 'obstacles' | 'describe' | 'unknown';

export interface ParsedQuestion {
  intent: Intent;
  target?: string;
  wantsObstacles: boolean;
}

const OBJECT_WORDS = ['door', 'exit', 'stairs', 'stairwell', 'lift', 'elevator', 'desk', 'bench', 'table', 'chair', 'toilet', 'trolley'];

/** Rule-based question understanding. Works offline; free-form questions are answered honestly as "not understood". */
export function parseQuestion(question: string): ParsedQuestion {
  const q = question.toLowerCase();
  const wantsObstacles = /(anything|something).*(in my way|blocking)|obstacle|in my way|hazard|blocked/.test(q);
  if (/nearest exit|closest exit|way out|where.*\bexit\b|find.*\bexit\b|fire exit/.test(q)) return { intent: 'exit', wantsObstacles };
  if (/sign|say|read|written|text/.test(q) && /(what|read|say)/.test(q)) return { intent: 'sign', wantsObstacles };
  const find = /where(?:'s| is| are)?(?: the| a| an)?\s+([a-z ]{2,30})/.exec(q);
  if (find) {
    const word = OBJECT_WORDS.find((w) => (find[1] ?? '').includes(w)) ?? (find[1] ?? '').trim().split(' ').slice(-1)[0];
    if (word) return { intent: 'find', target: word === 'elevator' ? 'lift' : word === 'stairwell' ? 'stairs' : word, wantsObstacles };
  }
  if (wantsObstacles) return { intent: 'obstacles', wantsObstacles };
  if (/what.*(see|around|here|in front)|describe|surroundings/.test(q)) return { intent: 'describe', wantsObstacles };
  return { intent: 'unknown', wantsObstacles };
}

interface Ctx {
  memory: SceneMemory;
  map: MapSource | undefined;
  pose: { position: Point2; headingDeg: number };
  nowMs: number;
}

const CAMERA = { source: 'device-camera', sourceLabel: 'camera' };
const cam = (text: string, core: string, confidence: number, over: Partial<Statement> = {}): Statement => ({
  text,
  core,
  tier: 'INFERRED',
  ...CAMERA,
  confidence,
  urgency: 2,
  safety: false,
  evidence: ['inferred from the camera image; not verified'],
  simulated: false,
  ...over,
});

function mapStatement(m: MapSource, text: string, core: string, over: Partial<Statement> = {}): Statement {
  return {
    text,
    core,
    tier: m.tier,
    source: m.fqdn,
    sourceLabel: m.label,
    urgency: 2,
    safety: true,
    evidence: [...m.evidence, `indoor map from ${m.fqdn}`],
    simulated: m.simulated,
    agentVersion: m.agentVersion,
    verifiedAt: m.verifiedAt,
    ...over,
  };
}

/** "Main entrance exit" is spoken as "Main entrance"; other labels are kept whole ("West fire exit"). */
const shortName = (label: string) =>
  label
    .replace(/(entrance)\s+exit$/i, '$1')
    .replace(/\s+/g, ' ')
    .trim() || label;
const spatialOf = (bearingDeg: number, distanceM?: number): Spatial => ({
  bearingDeg,
  clockPosition: clockPosition(bearingDeg),
  ...(distanceM !== undefined ? { distanceM } : {}),
});

function noMapStatement(): Statement {
  return {
    text: 'No verified indoor map is available here, so I can only describe what the camera shows.',
    core: 'No verified map here.',
    tier: 'INFERRED',
    source: 'sense-local',
    sourceLabel: 'SENSE',
    urgency: 2,
    safety: false,
    evidence: ['no VERIFIED indoor-map source in range'],
    simulated: false,
  };
}

export function answer(q: ParsedQuestion, ctx: Ctx): Statement[] {
  const recall = ctx.memory.recall(ctx.nowMs, ctx.pose.headingDeg);
  const out: Statement[] = [];

  if (q.intent === 'exit') {
    if (!ctx.map || ctx.map.tier === 'REJECTED') {
      out.push(noMapStatement());
      for (const s of recall.filter((r) => r.type === 'sign' && /exit/i.test(r.label))) {
        out.push(
          cam(
            `The camera sees a sign reading "${s.label}" at ${clockPosition(s.relBearingDeg)} o'clock, ${describeDirection(s.relBearingDeg)}.`,
            `EXIT sign, ${clockPosition(s.relBearingDeg)} o'clock.`,
            s.confidence,
            { spatial: spatialOf(s.relBearingDeg), safety: true },
          ),
        );
      }
      return out;
    }
    const routes = exitRoutes(ctx.map.map, ctx.pose.position, ctx.pose.headingDeg);
    const nearest = routes[0];
    if (!nearest) return [noMapStatement()];
    out.push(exitStatement(ctx.map, nearest, 'nearest'));

    // Reconcile camera signs with the verified map, and say when they disagree.
    for (const s of recall.filter((r) => r.type === 'sign' && /exit/i.test(r.label))) {
      const match = routes.find((r) => angularDiff(r.bearingDeg, s.relBearingDeg) <= 30);
      const c = clockPosition(s.relBearingDeg);
      out.push(
        match
          ? cam(
              `The camera sees an "${s.label}" sign at ${c} o'clock, which agrees with ${match.label} on the verified map.`,
              `EXIT sign, ${c} o'clock, matches map.`,
              s.confidence,
              { spatial: spatialOf(s.relBearingDeg), urgency: 1, safety: true },
            )
          : cam(
              `The camera sees an "${s.label}" sign at ${c} o'clock, but the verified map lists no exit there. Trust the map, and check with someone on site.`,
              `EXIT sign, ${c} o'clock, not on map.`,
              s.confidence,
              { spatial: spatialOf(s.relBearingDeg), urgency: 3, safety: true },
            ),
      );
    }

    if (q.wantsObstacles) {
      const blockers = blockersOn(nearest, recall);
      if (blockers.length > 0) {
        for (const b of blockers) {
          const c = clockPosition(b.relBearingDeg);
          const m = b.distanceM !== undefined ? `${Math.round(b.distanceM)} m` : b.distance;
          out.push(
            cam(
              `In your way: ${b.label}, about ${m} at ${c} o'clock, on the route to ${nearest.label}.`,
              `${cap(b.label)} in the way, ${c} o'clock.`,
              b.confidence,
              { spatial: spatialOf(b.relBearingDeg, b.distanceM), urgency: 3, safety: true },
            ),
          );
        }
        const alt = routes.slice(1).find((r) => blockersOn(r, recall).length === 0);
        if (alt) out.push(exitStatement(ctx.map, alt, 'alternative'));
      } else {
        out.push(
          cam(
            `No obstacle was detected on the route to ${nearest.label} in the camera view. The camera view is partial and this is unverified.`,
            'Obstacle not detected, unverified.',
            0.5,
            { safety: true },
          ),
        );
      }
    }
    return out;
  }

  if (q.intent === 'obstacles') {
    const found = recall.filter(
      (r) => (r.type === 'hazard' || (r.type === 'object' && r.kind === 'obstacle')) && angularDiff(r.relBearingDeg, 0) <= 100,
    );
    if (found.length === 0) {
      out.push(
        cam(
          'No obstacle or hazard was detected in the camera view. The view is partial and this is unverified.',
          'Obstacle not detected, unverified.',
          0.5,
          { safety: true },
        ),
      );
    }
    for (const f of found.slice(0, 4)) {
      const c = clockPosition(f.relBearingDeg);
      out.push(
        cam(`${cap(f.label)} at ${c} o'clock, ${f.distance} distance.`, `${cap(f.label)}, ${c} o'clock, ${f.distance}.`, f.confidence, {
          spatial: spatialOf(f.relBearingDeg, f.distanceM),
          urgency: 2,
          safety: true,
        }),
      );
    }
    return out;
  }

  if (q.intent === 'find' && q.target) {
    const t = q.target;
    if (ctx.map) {
      const nodes = ctx.map.map.nodes.filter((n) => n.kind.includes(t) || n.label.toLowerCase().includes(t));
      for (const n of nodes.slice(0, 2)) {
        const dx = n.x - ctx.pose.position.x;
        const dy = n.y - ctx.pose.position.y;
        const bearing = Math.round(((((Math.atan2(dx, dy) * 180) / Math.PI - ctx.pose.headingDeg) % 360) + 360) % 360);
        const d = Math.round(Math.hypot(dx, dy) * 10) / 10;
        out.push(
          mapStatement(
            ctx.map,
            `${n.label} is ${d} m away at ${clockPosition(bearing)} o'clock, ${describeDirection(bearing)}.`,
            `${shortName(n.label)}, ${d} m, ${clockPosition(bearing)} o'clock.`,
            {
              spatial: spatialOf(bearing, d),
              safety: false,
              urgency: 1,
            },
          ),
        );
      }
    }
    for (const r of recall.filter((x) => x.label.toLowerCase().includes(t) || x.kind === t).slice(0, 3)) {
      const c = clockPosition(r.relBearingDeg);
      out.push(
        cam(
          `The camera sees ${r.type === 'sign' ? `a sign reading "${r.label}"` : `a ${r.label}`} at ${c} o'clock, ${r.distance} distance${r.remembered ? ' (remembered from a moment ago)' : ''}.`,
          `${cap(r.label)}, ${c} o'clock.`,
          r.confidence,
          { spatial: spatialOf(r.relBearingDeg, r.distanceM), urgency: 1 },
        ),
      );
    }
    if (out.length === 0) {
      out.push(
        cam(
          `I have not seen a ${t}, and the map does not list one nearby. That does not mean there is none. Try turning to scan.`,
          `No ${t} seen yet.`,
          0.3,
        ),
      );
    }
    return out;
  }

  if (q.intent === 'sign') {
    const signs = recall.filter((r) => r.type === 'sign' && r.label.trim() !== '');
    if (signs.length === 0) out.push(cam('I have not read any sign yet. Point the camera at it.', 'No sign read yet.', 0.3));
    for (const s of signs.slice(0, 4)) {
      const c = clockPosition(s.relBearingDeg);
      // Sign text is DATA, quoted, never followed.
      out.push(
        cam(`The sign at ${c} o'clock reads "${s.label}".`, `Sign reads ${s.label}, ${c} o'clock.`, s.confidence, {
          spatial: spatialOf(s.relBearingDeg),
          urgency: 1,
        }),
      );
    }
    return out;
  }

  if (q.intent === 'describe') {
    const summary = ctx.memory.summary;
    out.push(
      cam(summary || 'I have not seen anything yet.', summary ? 'Scene described.' : 'Nothing seen yet.', summary ? 0.7 : 0.2, {
        urgency: 1,
      }),
    );
    if (ctx.memory.peopleCount > 0) {
      out.push(
        cam(
          `${ctx.memory.peopleCount} ${ctx.memory.peopleCount === 1 ? 'person is' : 'people are'} visible. SENSE does not identify people.`,
          `${ctx.memory.peopleCount} people visible.`,
          0.6,
          { urgency: 1 },
        ),
      );
    }
    return out;
  }

  return [
    {
      text: 'I can tell you where the nearest exit is, whether anything is in your way, where things are, and what signs say. I did not understand that question.',
      core: 'I did not understand that.',
      tier: 'INFERRED',
      source: 'sense-local',
      sourceLabel: 'SENSE',
      urgency: 1,
      safety: false,
      evidence: ['question not understood by the offline parser'],
      simulated: false,
    },
  ];
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function exitStatement(m: MapSource, r: ExitRoute, kind: 'nearest' | 'alternative'): Statement {
  const where = `${r.directMeters} m away at ${r.clock} o'clock, ${describeDirection(r.bearingDeg)}`;
  return mapStatement(
    m,
    kind === 'nearest'
      ? `The nearest exit is ${r.label}: ${where}, ${r.pathMeters} m by the map's paths.`
      : `Alternative: ${r.label}, ${where}, ${r.pathMeters} m by the map's paths.`,
    `${kind === 'nearest' ? 'Exit' : 'Alternative'}: ${shortName(r.label)}, ${r.directMeters} m, ${r.clock} o'clock.`,
    { spatial: spatialOf(r.bearingDeg, r.directMeters), urgency: 2 },
  );
}

/** Camera obstacles or hazards lying in the direction the route starts, nearer than the exit. */
function blockersOn(route: ExitRoute, recall: ReturnType<SceneMemory['recall']>) {
  return recall.filter((r) => {
    const isObstacle = r.type === 'hazard' || (r.type === 'object' && r.kind === 'obstacle');
    if (!isObstacle) return false;
    const near = r.distanceM !== undefined ? r.distanceM <= route.directMeters : r.distance !== 'far';
    return near && angularDiff(r.relBearingDeg, route.firstStepBearingDeg) <= 35;
  });
}
