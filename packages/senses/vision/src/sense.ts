import { ProviderDegraded, type Frame, type SceneGraph, type VisionProvider } from '@sense/providers';
import { guardPercept, iso, sanitizePayload, type Clock, type Percept, type Point2, type SenseModule } from '@sense/protocol';
import { answer, parseQuestion, type MapSource, type Statement } from './answer';
import { SceneMemory } from './memory';

export interface VisionDeps {
  provider: VisionProvider;
  clock: Clock;
  nextId: () => string;
  getPose: () => { position: Point2; headingDeg: number };
  /** The best VERIFIED (or UNVERIFIED) indoor map currently known, if any. */
  getMap: () => MapSource | undefined;
}

const words = (s: string) => s.trim().split(/\s+/).length;
const trimCore = (core: string, suffix: string) => {
  let w = core.trim().split(/\s+/);
  while (w.length + words(suffix) > 10 && w.length > 1) w = w.slice(0, -1);
  return `${w
    .join(' ')
    .replace(/[,;:]+$/, '')
    .replace(/\.?$/, '.')} ${suffix}`.trim();
};

/**
 * Vision (blind / low vision). Camera frames become a structured scene graph, remembered across
 * frames; questions are answered from that memory, a fresh frame, and the verified indoor map,
 * with provenance on every statement. People are counted, never identified.
 */
export class VisionSense {
  readonly memory = new SceneMemory();

  constructor(private readonly deps: VisionDeps) {}

  get label(): 'ANTHROPIC' | 'MOCK AI' {
    return this.deps.provider.label;
  }

  /** Describe a frame, remember it, and return description percepts. Degrades honestly on bad model output. */
  async see(frame: Frame): Promise<Percept[]> {
    const { clock, provider } = this.deps;
    let graph: SceneGraph;
    try {
      graph = await provider.describe(frame);
    } catch (err) {
      const why =
        err instanceof ProviderDegraded ? 'The AI reply was not valid twice in a row.' : 'The image description service was not available.';
      return [
        this.statusPercept(
          `Scene description failed. ${provider.label}.`,
          `${why} Nothing was remembered from this frame. I will not guess.`,
        ),
      ];
    }
    // Model output is untrusted: strings are sanitized before they are remembered or spoken.
    const clean = sanitizePayload(graph, { maxLen: 120 }).value;
    this.memory.update(clean, clock.now(), this.deps.getPose().headingDeg);
    const p = this.deps.getPose();
    const peopleNote =
      clean.peopleCount > 0 ? ` ${clean.peopleCount} ${clean.peopleCount === 1 ? 'person' : 'people'} visible, not identified.` : '';
    return [
      this.percept({
        kind: 'description',
        urgency: 1,
        core: 'Scene described.',
        suffix: `Inferred, ${provider.label === 'MOCK AI' ? 'mock camera' : 'camera'}.`,
        long: `${clean.summary}${peopleNote} Lighting: ${clean.lighting}.`,
        confidence: 0.7,
        evidence: [
          `${provider.label} scene graph, ${clean.objects.length} objects, ${clean.signs.length} signs`,
          'people are counted, never identified',
        ],
        heading: p.headingDeg,
      }),
    ];
  }

  /** Answer a question. Every statement becomes its own percept with its own provenance, grouped by question. */
  async ask(question: string, frame?: Frame): Promise<Percept[]> {
    const out: Percept[] = [];
    if (frame) out.push(...(await this.see(frame)));
    const statements = answer(parseQuestion(question), {
      memory: this.memory,
      map: this.deps.getMap(),
      pose: this.deps.getPose(),
      nowMs: this.deps.clock.now(),
    });
    const groupId = `q-${this.deps.nextId()}`;
    for (const s of statements) out.push(this.statementPercept(s, groupId));
    return out;
  }

  private statementPercept(s: Statement, groupId: string): Percept {
    const suffix = `${s.tier === 'VERIFIED' ? 'Verified' : s.tier === 'UNVERIFIED' ? 'Unverified' : 'Inferred'}, ${s.sourceLabel}.`;
    const short = s.safety || s.tier !== 'VERIFIED' ? trimCore(s.core, suffix) : trimCore(s.core, '');
    return guardPercept({
      id: this.deps.nextId(),
      timestamp: iso(this.deps.clock),
      sense: 'vision',
      kind: 'answer',
      urgency: s.urgency,
      short: s.safety || s.tier !== 'VERIFIED' ? short : short.trim(),
      long: s.text,
      ...(s.spatial ? { spatial: s.spatial } : {}),
      provenance: {
        tier: s.tier,
        source: s.source,
        sourceLabel: s.sourceLabel,
        ...(s.confidence !== undefined ? { confidence: s.confidence } : {}),
        ...(s.agentVersion ? { agentVersion: s.agentVersion } : {}),
        ...(s.verifiedAt ? { verifiedAt: s.verifiedAt } : {}),
        evidence: s.evidence,
      },
      ...(s.safety ? { safety: true } : {}),
      ...(s.simulated ? { simulated: true } : {}),
      groupId,
    });
  }

  private percept(a: {
    kind: Percept['kind'];
    urgency: number;
    core: string;
    suffix: string;
    long: string;
    confidence: number;
    evidence: string[];
    heading: number;
  }): Percept {
    return guardPercept({
      id: this.deps.nextId(),
      timestamp: iso(this.deps.clock),
      sense: 'vision',
      kind: a.kind,
      urgency: a.urgency,
      short: trimCore(a.core, a.suffix),
      long: a.long,
      provenance: { tier: 'INFERRED', source: 'device-camera', sourceLabel: 'camera', confidence: a.confidence, evidence: a.evidence },
    });
  }

  private statusPercept(short: string, long: string): Percept {
    return guardPercept({
      id: this.deps.nextId(),
      timestamp: iso(this.deps.clock),
      sense: 'vision',
      kind: 'status',
      urgency: 1,
      short,
      long,
      provenance: {
        tier: 'INFERRED',
        source: 'device-camera',
        sourceLabel: 'camera',
        confidence: 0,
        evidence: ['description failed; nothing inferred'],
      },
    });
  }

  /** SenseModule seam: vision produces percepts when frames are pushed in. */
  module(frames: () => AsyncIterable<Frame>): SenseModule {
    return {
      id: 'vision',
      inputs: ['device-camera', 'verified indoor-map'],
      produce: async function* (this: VisionSense) {
        for await (const f of frames()) yield* await this.see(f);
      }.bind(this),
    };
  }
}
