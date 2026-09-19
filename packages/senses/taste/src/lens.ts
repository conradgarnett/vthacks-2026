import { ProviderDegraded, type TasteInference, type TasteInput, type TasteProvider } from '@sense/providers';
import {
  guardPercept,
  iso,
  sanitizePayload,
  type Clock,
  type Percept,
  type Provenance,
  type SenseModule,
  type SensoryProfile,
  type Tier,
} from '@sense/protocol';
import {
  analyze,
  type AllergenClaim,
  type AllergenFinding,
  type Analysis,
  type AnalysisInput,
  type ClaimSource,
  type MenuItemInput,
  type MenuSource,
} from './analyze';

export interface TasteDeps {
  provider: TasteProvider;
  clock: Clock;
  nextId: () => string;
  /** Local profile: the declared allergens are read from here and never leave the device. */
  getProfile: () => SensoryProfile;
}

export interface TasteRequest {
  menu?: { source: MenuSource; item: MenuItemInput };
  labelText?: string;
  photo?: TasteInput;
}

export interface TasteResult {
  percepts: Percept[];
  analysis: Analysis;
  inference?: TasteInference;
  /** Set when the photo could not be interpreted; other sources are still used. */
  degraded?: string;
}

const tierWord = (t: Tier) => t.charAt(0) + t.slice(1).toLowerCase();
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const PHRASE = { contains: 'in', 'may-contain': 'may be in', suspected: 'suspected in' } as const;

function fit(core: string, suffix: string): string {
  let w = core.trim().split(/\s+/);
  const sw = suffix.split(/\s+/).length;
  while (w.length + sw > 10 && w.length > 1) w = w.slice(0, -1);
  return `${w
    .join(' ')
    .replace(/[,;:]+$/, '')
    .replace(/\.?$/, '.')} ${suffix}`.trim();
}

/**
 * TasteLens (taste): a multisensory description of food plus an allergen policy that never
 * reassures. Verified restaurant data outranks label text, which outranks photo inference; any
 * claim of a declared allergen from any tier raises an alert that names its tier.
 */
export class TasteLens {
  constructor(private readonly deps: TasteDeps) {}

  get label(): 'ANTHROPIC' | 'MOCK AI' {
    return this.deps.provider.label;
  }

  async analyze(req: TasteRequest): Promise<TasteResult> {
    const out: Percept[] = [];
    let inference: TasteInference | undefined;
    let degraded: string | undefined;

    if (req.photo && (req.photo.image || req.photo.text || req.photo.fixture)) {
      try {
        // Model output is untrusted: sanitize every string before it is used or spoken.
        inference = sanitizePayload(await this.deps.provider.infer(req.photo), { maxLen: 160 }).value;
      } catch (err) {
        degraded =
          err instanceof ProviderDegraded
            ? 'The AI reply was not valid twice in a row.'
            : 'The dish description service was not available.';
        out.push(
          this.status(
            `Photo analysis failed. ${this.deps.provider.label}.`,
            `${degraded} Nothing was inferred from the photo. Allergen checks below use only the other sources.`,
          ),
        );
      }
    }

    const input: AnalysisInput = {
      ...(req.menu ? { menu: req.menu } : {}),
      ...(req.labelText ? { labelText: req.labelText } : {}),
      ...(inference ? { photo: inference } : {}),
    };
    const declared = this.deps.getProfile().allergens;
    const analysis = analyze(input, declared);
    const dish = req.menu?.item.name ?? inference?.dish ?? 'this dish';

    for (const f of analysis.declaredFindings) out.push(this.declaredPercept(f, dish, input, analysis));
    if (analysis.ignoredLabelLines > 0) {
      out.push(
        this.status(
          'Some label text was ignored.',
          `${analysis.ignoredLabelLines} line(s) of label text looked like instructions or reassurance and were dropped. They were treated as data, never followed.`,
        ),
      );
    }
    out.push(...this.otherAllergens(analysis, req));
    out.push(...this.facets(req, inference, dish));
    return { percepts: out, analysis, ...(inference ? { inference } : {}), ...(degraded ? { degraded } : {}) };
  }

  // ── Declared allergens ────────────────────────────────────────────────────────────────────

  private declaredPercept(f: AllergenFinding, dish: string, input: AnalysisInput, a: Analysis): Percept {
    const leader = f.leader;
    if (!leader) return this.notFound(f, dish, input, a);
    const verifiedAgent = leader.source === 'verified-agent';
    const urgency = leader.tier === 'VERIFIED' && leader.presence !== 'suspected' ? 4 : 3;
    const label = leader.sourceLabel;
    const suffix = `${tierWord(leader.tier)}, ${label}.`;
    // Keep the dish name whole if it fits the spoken budget; otherwise say "this dish" rather than cutting a name mid-phrase.
    const budget = 10 - suffix.split(/\s+/).length - `${cap(f.allergen)} ${PHRASE[leader.presence]}`.split(/\s+/).length;
    const dishShort = dish.split(/\s+/).length <= budget ? dish : 'this dish';
    const short = fit(`${cap(f.allergen)} ${PHRASE[leader.presence]} ${dishShort}.`, suffix);
    const others = f.claims
      .filter((c) => c !== leader)
      .map((c) => `${cap(c.sourceLabel)} (${c.tier}): ${c.presence.replace('-', ' ')}, ${c.basis}.`);
    const long = [
      `${cap(leader.sourceLabel)} (${leader.tier}) reports ${f.allergen} ${leader.presence === 'contains' ? 'in' : leader.presence === 'may-contain' ? 'as a possible ingredient of' : 'as suspected in'} ${dish}: ${leader.basis}.`,
      leader.confidence !== undefined ? `Confidence ${Math.round(leader.confidence * 100)}%.` : '',
      ...f.notes,
      ...others,
      `You declared ${f.allergen} as an allergen. SENSE treats "may contain" as present, and a claim from any source raises this alert.`,
      'Check with the staff. SENSE cannot confirm that any dish is free of an allergen.',
    ]
      .filter(Boolean)
      .join(' ');
    const provenance = this.provenance(leader, input, f);
    return guardPercept({
      id: this.deps.nextId(),
      timestamp: iso(this.deps.clock),
      sense: 'taste',
      kind: 'alert',
      urgency,
      short,
      long,
      provenance,
      safety: true,
      ...(verifiedAgent && input.menu?.source.simulated ? { simulated: true } : {}),
      actions: [{ id: 'ack', label: 'Acknowledge' }],
    });
  }

  /** No source claimed the allergen. The wording says exactly which source looked and what it can and cannot mean. */
  private notFound(f: AllergenFinding, dish: string, input: AnalysisInput, a: Analysis): Percept {
    const best: ClaimSource | undefined = (['verified-agent', 'label', 'photo'] as const).find((s) => a.consulted.includes(s));
    const A = cap(f.allergen);
    let core: string;
    let tier: Tier = 'INFERRED';
    let source = 'sense-local';
    let sourceLabel = 'SENSE';
    let detail: string;
    if (best === 'verified-agent' && input.menu) {
      const s = input.menu.source;
      core = `${A} not listed for ${dish.split(/\s+/).slice(0, 3).join(' ')}.`;
      tier = s.tier;
      source = s.fqdn;
      sourceLabel = s.label;
      detail = `${s.label} (${s.tier}) does not list ${f.allergen} for ${dish}. That describes their list, not the food: kitchens can have cross-contact. Ask the staff.`;
    } else if (best === 'label') {
      core = `${A} not listed on label.`;
      source = 'label-text';
      sourceLabel = 'label text';
      detail = `${A} was not named on the label text you gave SENSE. Labels can be incomplete or misread. Not verified.`;
    } else if (best === 'photo') {
      core = `${A} not detected, unverified.`;
      source = 'device-camera';
      sourceLabel = 'camera';
      detail = `${A} not detected in the photo, unverified. A photo cannot show hidden ingredients such as sauces, oils or cross-contact, so this is not evidence that it is absent.`;
    } else {
      core = `No source checked for ${f.allergen}.`;
      detail = `SENSE had no menu record, label text or photo to check ${f.allergen} against. Ask the staff.`;
    }
    const suffix = `${tierWord(tier)}, ${sourceLabel}.`;
    return guardPercept({
      id: this.deps.nextId(),
      timestamp: iso(this.deps.clock),
      sense: 'taste',
      kind: 'status',
      urgency: 2,
      short: fit(core, suffix),
      long: `${detail} You declared ${f.allergen} as an allergen. SENSE cannot confirm that any dish is free of an allergen.`,
      provenance: {
        tier,
        source,
        sourceLabel,
        ...(tier === 'INFERRED' && best === 'photo' ? { confidence: 0.3 } : {}),
        ...(input.menu && best === 'verified-agent'
          ? { agentVersion: input.menu.source.agentVersion, verifiedAt: input.menu.source.verifiedAt }
          : {}),
        evidence: [
          ...(best === 'verified-agent' && input.menu ? input.menu.source.evidence : []),
          `${a.consulted.join(', ') || 'nothing'} consulted; ${f.allergen} not claimed`,
        ],
      },
      safety: true,
      ...(best === 'verified-agent' && input.menu?.source.simulated ? { simulated: true } : {}),
    });
  }

  private provenance(leader: AllergenClaim, input: AnalysisInput, f: AllergenFinding): Provenance {
    const m = input.menu?.source;
    const verifiedAgent = leader.source === 'verified-agent' && m;
    return {
      tier: leader.tier,
      source: leader.sourceId,
      sourceLabel: leader.sourceLabel,
      ...(leader.confidence !== undefined ? { confidence: leader.confidence } : {}),
      ...(verifiedAgent ? { agentVersion: m.agentVersion, verifiedAt: m.verifiedAt } : {}),
      evidence: [
        ...(verifiedAgent ? m.evidence : []),
        ...f.claims.map((c) => `${c.source}: ${c.presence} (${c.tier}), ${c.basis}`),
        'precedence: verified agent > label text > photo inference',
      ],
    };
  }

  // ── Other allergens and facets ────────────────────────────────────────────────────────────

  private otherAllergens(a: Analysis, req: TasteRequest): Percept[] {
    const declared = new Set(a.declaredFindings.map((f) => f.allergen));
    const rest = a.findings.filter((f) => !declared.has(f.allergen) && f.leader);
    const out: Percept[] = [];
    const agent = rest.filter((f) => f.leader?.source === 'verified-agent');
    if (agent.length > 0 && req.menu) {
      const s = req.menu.source;
      const contains = agent.filter((f) => f.leader?.presence === 'contains').map((f) => f.allergen);
      const may = agent.filter((f) => f.leader?.presence === 'may-contain').map((f) => f.allergen);
      const core =
        `${contains.length ? `Contains ${contains.join(', ')}.` : ''}${may.length ? ` May contain ${may.join(', ')}.` : ''}`.trim();
      out.push(
        guardPercept({
          id: this.deps.nextId(),
          timestamp: iso(this.deps.clock),
          sense: 'taste',
          kind: 'description',
          urgency: 1,
          short: fit(core, `${tierWord(s.tier)}, ${s.label}.`),
          long: `${s.label} (${s.tier}) lists for ${req.menu.item.name}: contains ${contains.join(', ') || 'nothing listed'}; may contain ${may.join(', ') || 'nothing listed'}. "May contain" means possible cross-contact.`,
          provenance: {
            tier: s.tier,
            source: s.fqdn,
            sourceLabel: s.label,
            agentVersion: s.agentVersion,
            verifiedAt: s.verifiedAt,
            evidence: s.evidence,
          },
          safety: true,
          ...(s.simulated ? { simulated: true } : {}),
        }),
      );
    }
    const inferred = rest.filter((f) => f.leader?.source !== 'verified-agent');
    if (inferred.length > 0) {
      out.push(
        guardPercept({
          id: this.deps.nextId(),
          timestamp: iso(this.deps.clock),
          sense: 'taste',
          kind: 'description',
          urgency: 1,
          short: fit(`Possibly contains ${inferred.map((f) => f.allergen).join(', ')}.`, 'Inferred, camera.'),
          long: `From the photo or label text (not verified): ${inferred.map((f) => `${f.allergen} (${f.leader?.source}, ${f.leader?.presence})`).join('; ')}. Inferences can be wrong and can miss hidden ingredients.`,
          provenance: {
            tier: 'INFERRED',
            source: 'device-camera',
            sourceLabel: 'camera',
            confidence: 0.6,
            evidence: ['photo or label inference'],
          },
          safety: true,
        }),
      );
    }
    return out;
  }

  private facets(req: TasteRequest, inf: TasteInference | undefined, dish: string): Percept[] {
    const out: Percept[] = [];
    if (req.menu) {
      const { source: s, item } = req.menu;
      out.push(
        guardPercept({
          id: this.deps.nextId(),
          timestamp: iso(this.deps.clock),
          sense: 'taste',
          kind: 'description',
          urgency: 1,
          short: fit(`${item.name}: spice ${item.spice} of 5${item.temperature ? `, ${item.temperature}` : ''}.`, ''),
          long: [
            `${item.name} from ${s.label} (${s.tier}).`,
            item.texture ? `Texture: ${item.texture}.` : '',
            `Spice ${item.spice} of 5.`,
            item.temperature ? `Served ${item.temperature}.` : '',
            `Preparation: ${item.preparation}`,
            item.culture ? `Cultural context: ${item.culture}` : '',
            `Ingredients: ${item.ingredients.join(', ')}.`,
          ]
            .filter(Boolean)
            .join(' '),
          provenance: {
            tier: s.tier,
            source: s.fqdn,
            sourceLabel: s.label,
            agentVersion: s.agentVersion,
            verifiedAt: s.verifiedAt,
            evidence: s.evidence,
          },
          ...(s.simulated ? { simulated: true } : {}),
        }),
      );
    }
    if (inf) {
      out.push(
        guardPercept({
          id: this.deps.nextId(),
          timestamp: iso(this.deps.clock),
          sense: 'taste',
          kind: 'description',
          urgency: 1,
          short: fit(`Photo suggests umami ${inf.umami}, salt ${inf.salt} of 5.`, ''),
          long: [
            `Inferred from the photo (${this.deps.provider.label}), so it may be wrong: ${inf.dish}.`,
            `Texture: ${inf.texture}. Spice ${inf.spice}, salt ${inf.salt}, sweet ${inf.sweet}, acid ${inf.acid}, umami ${inf.umami}, richness ${inf.richness} (each 0 to 5). Served ${inf.temperature}.`,
            `Aroma notes: ${inf.aroma.join(', ') || 'none'}.`,
            `Ingredients seen: ${inf.ingredients.map((i) => `${i.name} ${Math.round(i.confidence * 100)}%`).join(', ') || 'none'}.`,
            `Preparation: ${inf.preparation} Cultural context: ${inf.culture}`,
          ].join(' '),
          provenance: {
            tier: 'INFERRED',
            source: 'device-camera',
            sourceLabel: 'camera',
            confidence: 0.6,
            evidence: [`${this.deps.provider.label} photo inference`, `dish guess: ${dish}`],
          },
        }),
      );
    }
    return out;
  }

  private status(short: string, long: string): Percept {
    return guardPercept({
      id: this.deps.nextId(),
      timestamp: iso(this.deps.clock),
      sense: 'taste',
      kind: 'status',
      urgency: 1,
      short,
      long,
      provenance: { tier: 'INFERRED', source: 'device-camera', sourceLabel: 'camera', confidence: 0, evidence: ['nothing inferred'] },
    });
  }

  module(requests: () => AsyncIterable<TasteRequest>): SenseModule {
    return {
      id: 'taste',
      inputs: ['verified menu-allergens', 'label text', 'dish photo'],
      produce: async function* (this: TasteLens) {
        for await (const r of requests()) yield* (await this.analyze(r)).percepts;
      }.bind(this),
    };
  }
}
