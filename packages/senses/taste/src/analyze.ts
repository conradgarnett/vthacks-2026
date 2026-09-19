import type { Tier } from '@sense/protocol';
import type { TasteInference } from '@sense/providers';
import { canonicalAllergen, declaredCanonical, parseLabelText } from './allergens';

/**
 * Allergen policy (invariants, property-tested):
 *  1. Precedence: verified restaurant agent > label text > photo inference.
 *  2. ANY claim of presence from ANY tier about a declared allergen raises an alert that states the tier.
 *  3. "May contain" is treated as present.
 *  4. Inference-only results are worded "not detected, unverified", never "safe".
 *  5. Absence from a list is a statement about the list, not about the food.
 */

export type ClaimSource = 'verified-agent' | 'label' | 'photo';
export type Presence = 'contains' | 'may-contain' | 'suspected';

export interface AllergenClaim {
  allergen: string;
  presence: Presence;
  source: ClaimSource;
  tier: Tier;
  /** Only for photo suspicions. */
  confidence?: number;
  /** Who said it, for provenance. */
  sourceId: string;
  sourceLabel: string;
  /** Why: "listed under contains", "ingredient: peanut sauce", "seen in photo". */
  basis: string;
}

export interface MenuSource {
  fqdn: string;
  label: string;
  /** Tier of the agent's data as delivered (UNVERIFIED if identity incomplete or data stale). */
  tier: Tier;
  simulated: boolean;
  agentVersion: string;
  verifiedAt: string;
  evidence: string[];
}

export interface MenuItemInput {
  id: string;
  name: string;
  ingredients: string[];
  allergens: { contains: string[]; mayContain: string[] };
  preparation: string;
  spice: number;
  texture?: string | undefined;
  temperature?: 'cold' | 'cool' | 'warm' | 'hot' | undefined;
  culture?: string | undefined;
}

export interface AnalysisInput {
  menu?: { source: MenuSource; item: MenuItemInput };
  labelText?: string;
  photo?: TasteInference;
}

const PRECEDENCE: Record<ClaimSource, number> = { 'verified-agent': 0, label: 1, photo: 2 };
const PRESENCE_RANK: Record<Presence, number> = { contains: 0, 'may-contain': 1, suspected: 2 };
const TIER_RANK: Record<Tier, number> = { VERIFIED: 0, INFERRED: 1, UNVERIFIED: 2, REJECTED: 3 };

export function collectClaims(input: AnalysisInput): { claims: AllergenClaim[]; ignoredLabelLines: number; consulted: ClaimSource[] } {
  const claims: AllergenClaim[] = [];
  const consulted: ClaimSource[] = [];
  let ignored = 0;

  if (input.menu) {
    consulted.push('verified-agent');
    const { source: s, item } = input.menu;
    const base = { source: 'verified-agent' as const, tier: s.tier, sourceId: s.fqdn, sourceLabel: s.label };
    for (const a of item.allergens.contains)
      claims.push({
        ...base,
        allergen: canonicalAllergen(a) ?? a.toLowerCase(),
        presence: 'contains',
        basis: `listed under "contains": ${a}`,
      });
    for (const a of item.allergens.mayContain)
      claims.push({
        ...base,
        allergen: canonicalAllergen(a) ?? a.toLowerCase(),
        presence: 'may-contain',
        basis: `listed under "may contain": ${a}`,
      });
    for (const ing of item.ingredients) {
      const a = canonicalAllergen(ing);
      if (a) claims.push({ ...base, allergen: a, presence: 'contains', basis: `ingredient: ${ing}` });
    }
  }

  if (input.labelText && input.labelText.trim()) {
    consulted.push('label');
    const p = parseLabelText(input.labelText);
    ignored = p.ignored;
    const base = { source: 'label' as const, tier: 'INFERRED' as Tier, sourceId: 'label-text', sourceLabel: 'label text' };
    for (const a of p.contains) claims.push({ ...base, allergen: a, presence: 'contains', basis: 'listed under "contains" on the label' });
    for (const a of p.mayContain)
      claims.push({ ...base, allergen: a, presence: 'may-contain', basis: 'listed under "may contain" on the label' });
    for (const a of p.fromIngredients)
      claims.push({ ...base, allergen: a, presence: 'contains', basis: 'named in the ingredient list on the label' });
  }

  if (input.photo) {
    consulted.push('photo');
    const base = { source: 'photo' as const, tier: 'INFERRED' as Tier, sourceId: 'device-camera', sourceLabel: 'camera' };
    for (const s of input.photo.allergenSuspicions) {
      claims.push({
        ...base,
        allergen: canonicalAllergen(s.allergen) ?? s.allergen.toLowerCase(),
        presence: 'suspected',
        confidence: s.confidence,
        basis: 'seen or suspected in the photo',
      });
    }
    for (const ing of input.photo.ingredients) {
      const a = canonicalAllergen(ing.name);
      if (a)
        claims.push({
          ...base,
          allergen: a,
          presence: 'suspected',
          confidence: ing.confidence,
          basis: `ingredient seen in the photo: ${ing.name}`,
        });
    }
  }
  return { claims, ignoredLabelLines: ignored, consulted };
}

/** The claim that leads for one allergen: highest-precedence source first, then strongest presence. */
export function leadingClaim(claims: AllergenClaim[]): AllergenClaim | undefined {
  return [...claims].sort(
    (a, b) =>
      PRECEDENCE[a.source] - PRECEDENCE[b.source] ||
      TIER_RANK[a.tier] - TIER_RANK[b.tier] ||
      PRESENCE_RANK[a.presence] - PRESENCE_RANK[b.presence],
  )[0];
}

export interface AllergenFinding {
  allergen: string;
  /** True when the user declared this allergen. */
  declared: boolean;
  leader: AllergenClaim | undefined;
  /** All claims about this allergen, best first. */
  claims: AllergenClaim[];
  /** Sources that were consulted but said nothing about it. */
  silent: ClaimSource[];
  /** Plain-language notes on disagreements and precedence. */
  notes: string[];
}

export interface Analysis {
  findings: AllergenFinding[];
  /** Findings for allergens the user declared, in order. */
  declaredFindings: AllergenFinding[];
  consulted: ClaimSource[];
  ignoredLabelLines: number;
}

const SOURCE_NAME: Record<ClaimSource, string> = { 'verified-agent': 'the restaurant agent', label: 'the label text', photo: 'the photo' };

export function analyze(input: AnalysisInput, declared: string[]): Analysis {
  const { claims, ignoredLabelLines, consulted } = collectClaims(input);
  const byAllergen = new Map<string, AllergenClaim[]>();
  for (const c of claims) byAllergen.set(c.allergen, [...(byAllergen.get(c.allergen) ?? []), c]);

  const declaredCanon = [...new Set(declared.map(declaredCanonical))];
  const names = new Set<string>([...byAllergen.keys(), ...declaredCanon]);

  const findings: AllergenFinding[] = [...names].map((allergen) => {
    // Unknown (custom) allergens also match by substring so "kiwi" finds "kiwi fruit".
    const own = claims.filter(
      (c) => c.allergen === allergen || (!ALLERGEN_KNOWN(allergen) && (c.allergen.includes(allergen) || c.basis.includes(allergen))),
    );
    const sorted = [...own].sort(
      (a, b) =>
        PRECEDENCE[a.source] - PRECEDENCE[b.source] ||
        TIER_RANK[a.tier] - TIER_RANK[b.tier] ||
        PRESENCE_RANK[a.presence] - PRESENCE_RANK[b.presence],
    );
    const leader = leadingClaim(sorted);
    const said = new Set(sorted.map((c) => c.source));
    const silent = consulted.filter((s) => !said.has(s));
    const notes: string[] = [];
    if (leader) {
      for (const s of silent) {
        if (PRECEDENCE[s] > PRECEDENCE[leader.source]) {
          notes.push(
            `${cap(SOURCE_NAME[s])} did not detect or list ${allergen}. That is overridden by ${SOURCE_NAME[leader.source]}: absence in ${SOURCE_NAME[s]} is never evidence of absence.`,
          );
        } else {
          notes.push(
            `${cap(SOURCE_NAME[s])} does not list ${allergen}, but ${SOURCE_NAME[leader.source]} reports it and is not fully verified. Treat it as present and check with staff.`,
          );
        }
      }
      const lower = sorted.find((c) => PRECEDENCE[c.source] > PRECEDENCE[leader.source]);
      if (lower && lower.presence !== leader.presence)
        notes.push(
          `${cap(SOURCE_NAME[lower.source])} said ${lower.presence.replace('-', ' ')}; the higher-precedence source says ${leader.presence.replace('-', ' ')}.`,
        );
    }
    return { allergen, declared: declaredCanon.includes(allergen), leader, claims: sorted, silent, notes };
  });

  return {
    findings,
    declaredFindings: declaredCanon.map((d) => findings.find((f) => f.allergen === d) as AllergenFinding),
    consulted,
    ignoredLabelLines,
  };
}

const ALLERGEN_KNOWN = (a: string) => canonicalAllergen(a) === a;
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
