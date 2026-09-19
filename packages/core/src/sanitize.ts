/**
 * Remote agent content is DATA, never instructions. This module is one of three layers:
 *   1. strict schema validation + length limits (protocol package),
 *   2. this sanitizer (unicode cleanup, instruction-like and assurance-like text removal),
 *   3. the policy layer, which is frozen and which no remote content can reach.
 * SENSE never forwards remote free text to a language model as instructions, and it renders
 * remote free text only as a quoted "publisher note" when it survived sanitization.
 */

export type SanitizeFlag = 'control-chars' | 'truncated' | 'instruction-like' | 'assurance-claim';

export interface SanitizeResult {
  text: string;
  /** Empty string plus `neutralized: true` when the whole field was dropped. */
  neutralized: boolean;
  flags: SanitizeFlag[];
  /** Names of the rules that matched (for the security log). */
  matched: string[];
}

// Zero-width, bidi override/isolate and other invisible formatting characters.
const INVISIBLE = /[\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF\u00AD]/g;
// C0/C1 control characters except tab and newline (which are turned into spaces).
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/g;

const INSTRUCTION_RULES: [string, RegExp][] = [
  [
    'override-instructions',
    /\b(ignore|disregard|forget|override|bypass|skip)\b[^.\n]{0,50}\b(previous|prior|above|earlier|all|any|your|these|the)\b[^.\n]{0,30}\b(instructions?|rules?|prompts?|polic(?:y|ies)|guidelines?|constraints?|safeguards?)\b/i,
  ],
  [
    'role-change',
    /\b(you are now|act as|pretend (?:to be|you are)|from now on,? you|new instructions?|system prompt|developer (?:message|mode)|admin(?:istrator)? mode|jailbreak|as an? (?:ai|assistant|system))\b/i,
  ],
  [
    'exfiltration',
    /\b(reveal|show|print|send|leak|disclose|output)\b[^.\n]{0,40}\b(profile|prompt|system|secret|password|api key|credentials?|allergens?|disabilit(?:y|ies))\b/i,
  ],
  [
    'address-the-assistant',
    /\b(tell|inform|assure|convince|instruct|order|command)\b[^.\n]{0,20}\b(the )?(user|users|them|everyone|people|assistant|model)\b/i,
  ],
  [
    'suppress-warning',
    /\b(do not|don't|never)\b[^.\n]{0,20}\b(tell|warn|alert|inform|notify|mention)\b/i,
  ],
  [
    'markup-injection',
    /```|<\/?\s*(system|assistant|tool|instructions?|prompt)\b|\[\/?INST\]|<\|[a-z_]+\|>/i,
  ],
  [
    'tool-invocation',
    /\b(call|invoke|execute|run|use)\b[^.\n]{0,20}\b(the )?(tool|function|command|script)\b/i,
  ],
];

/** Claims that would reassure the user. SENSE never asserts safety it cannot verify. */
const ASSURANCE_RULES: [string, RegExp][] = [
  [
    'all-clear',
    /\b(all[- ]clear|false alarm|ignore (?:the |this )?alarm|stand down|nothing to worry|no need to (?:worry|evacuate|leave))\b/i,
  ],
  [
    'safety-claim',
    /\b(?:is|are|be|it'?s|everything(?:'s| is)) (?:completely |perfectly |totally )?(?:safe|harmless|fine|ok(?:ay)?)\b|\bsafe to (?:eat|enter|breathe|proceed|stay|ignore)\b|\bno (?:danger|risk|hazard)s?\b/i,
  ],
];

export function detectInstructionLike(text: string): string[] {
  const squashed = text.replace(/[\s._\-*]+/g, '');
  const hits = INSTRUCTION_RULES.filter(([, re]) => re.test(text)).map(([name]) => name);
  // Catch letter-spacing tricks such as "i g n o r e  p r e v i o u s  r u l e s".
  if (/ignore(?:all)?(?:previous|prior|above|your)(?:instructions|rules|prompts)/i.test(squashed)) {
    if (!hits.includes('override-instructions')) hits.push('override-instructions');
  }
  return hits;
}

export function detectAssuranceClaim(text: string): string[] {
  return ASSURANCE_RULES.filter(([, re]) => re.test(text)).map(([name]) => name);
}

export function sanitizeText(input: string, opts: { maxLen?: number } = {}): SanitizeResult {
  const maxLen = opts.maxLen ?? 300;
  const flags: SanitizeFlag[] = [];
  let text = input.normalize('NFKC');
  const stripped = text.replace(INVISIBLE, '').replace(CONTROL, ' ');
  if (stripped !== text) flags.push('control-chars');
  text = stripped.replace(/\s+/g, ' ').trim();
  if (text.length > maxLen) {
    text = text.slice(0, maxLen).trimEnd();
    flags.push('truncated');
  }
  const instruction = detectInstructionLike(text);
  const assurance = detectAssuranceClaim(text);
  if (instruction.length > 0) flags.push('instruction-like');
  if (assurance.length > 0) flags.push('assurance-claim');
  if (instruction.length > 0 || assurance.length > 0) {
    return { text: '', neutralized: true, flags, matched: [...instruction, ...assurance] };
  }
  return { text, neutralized: false, flags, matched: [] };
}

export interface PayloadSanitizeEvent {
  /** JSON path of the field, e.g. `features[0].notes`. */
  path: string;
  flags: SanitizeFlag[];
  matched: string[];
  neutralized: boolean;
}

/**
 * Sanitize every string in an already schema-validated payload. Fields that look like
 * instructions or reassurance are dropped entirely (set to ""), never partially edited.
 */
export function sanitizePayload<T>(
  payload: T,
  opts: { maxLen?: number } = {},
): { value: T; events: PayloadSanitizeEvent[] } {
  const events: PayloadSanitizeEvent[] = [];
  const walk = (v: unknown, path: string): unknown => {
    if (typeof v === 'string') {
      const r = sanitizeText(v, opts);
      if (r.flags.length > 0) {
        events.push({ path, flags: r.flags, matched: r.matched, neutralized: r.neutralized });
      }
      return r.text;
    }
    if (Array.isArray(v)) return v.map((x, i) => walk(x, `${path}[${i}]`));
    if (v !== null && typeof v === 'object') {
      return Object.fromEntries(
        Object.entries(v as Record<string, unknown>).map(([k, x]) => [
          k,
          walk(x, path ? `${path}.${k}` : k),
        ]),
      );
    }
    return v;
  };
  return { value: walk(payload, '') as T, events };
}
