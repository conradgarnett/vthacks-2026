import { sanitizeText } from '@sense/protocol';

/**
 * Allergen vocabulary. Deliberately small and documented: SENSE maps words to a canonical allergen
 * name so a declared "peanuts" matches a menu's "peanut sauce". Unknown allergens fall back to plain
 * word matching. "gluten" is folded into "wheat" here (a simplification noted in the docs).
 */
export const ALLERGEN_PATTERNS: Record<string, RegExp> = {
  peanut: /\b(peanuts?|groundnuts?|arachis)\b/i,
  'tree nut': /\b(tree ?nuts?|almonds?|walnuts?|cashews?|hazelnuts?|pecans?|pistachios?|macadamias?|brazil nuts?)\b/i,
  milk: /\b(milk|dairy|lactose|cheese|butter|cream|whey|casein|yoghurt|yogurt|mozzarella|parmesan|ghee)\b/i,
  egg: /\b(eggs?|mayonnaise|albumen)\b/i,
  wheat: /\b(wheat|gluten|flour|semolina|spelt|barley|rye|noodles?|pasta|couscous|breadcrumbs?)\b/i,
  soy: /\b(soy|soya|soybeans?|tofu|edamame|miso|tempeh)\b/i,
  sesame: /\b(sesame|tahini)\b/i,
  fish: /\b(fish|anchov(?:y|ies)|salmon|tuna|cod|sardines?)\b/i,
  shellfish: /\b(shellfish|prawns?|shrimps?|crab|lobster|crayfish|mollus[ck]s?|mussels?|oysters?|clams?|squid|scallops?)\b/i,
  mustard: /\b(mustard)\b/i,
  celery: /\b(celery|celeriac)\b/i,
  sulphite: /\b(sulphites?|sulfites?)\b/i,
};

/** Canonical allergen for a word or phrase, or undefined if it names no known allergen. */
export function canonicalAllergen(text: string): string | undefined {
  for (const [name, re] of Object.entries(ALLERGEN_PATTERNS)) if (re.test(text)) return name;
  return undefined;
}

/** Canonical form of a user-declared allergen; unknown allergens keep their own lower-case name. */
export function declaredCanonical(declared: string): string {
  return canonicalAllergen(declared) ?? declared.trim().toLowerCase();
}

export interface LabelParse {
  contains: string[];
  mayContain: string[];
  /** Allergens implied by words in the ingredient list. */
  fromIngredients: string[];
  ingredients: string[];
  /** Sentences dropped because they looked like instructions or reassurance. */
  ignored: number;
}

const splitList = (s: string) =>
  s
    .split(/[,;]| and | & /i)
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);

/**
 * Parse label / menu text. Text is UNTRUSTED (it may come from OCR of a hostile sign): each sentence
 * is sanitized separately, so an injected line is dropped without losing the real ingredient list.
 * "May contain", "traces of" and "produced in a facility that also" all count as PRESENT.
 */
export function parseLabelText(raw: string): LabelParse {
  const out: LabelParse = { contains: [], mayContain: [], fromIngredients: [], ingredients: [], ignored: 0 };
  const sentences = raw
    .split(/[\n.]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  for (const sentence of sentences) {
    const clean = sanitizeText(sentence, { maxLen: 400 });
    if (clean.neutralized) {
      out.ignored++;
      continue;
    }
    const s = clean.text;
    const may =
      /(?:may contain|might contain|traces? of|also processes|also handles|made in a facility|produced in a facility)[:\s]+(.*)/i.exec(s);
    const contains = /(?:^|\b)contains?[:\s]+(.*)/i.exec(s);
    const ingredients = /ingredients?[:\s]+(.*)/i.exec(s);
    if (may) {
      for (const item of splitList(may[1] ?? '')) out.mayContain.push(canonicalAllergen(item) ?? item);
    } else if (contains) {
      for (const item of splitList(contains[1] ?? '')) out.contains.push(canonicalAllergen(item) ?? item);
    } else if (ingredients) {
      for (const item of splitList(ingredients[1] ?? '')) {
        out.ingredients.push(item);
        const a = canonicalAllergen(item);
        if (a) out.fromIngredients.push(a);
      }
    }
  }
  const uniq = (v: string[]) => [...new Set(v)];
  return {
    contains: uniq(out.contains),
    mayContain: uniq(out.mayContain),
    fromIngredients: uniq(out.fromIngredients),
    ingredients: uniq(out.ingredients),
    ignored: out.ignored,
  };
}
