import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

function block(startPattern: RegExp): Record<string, string> {
  const start = css.search(startPattern);
  if (start < 0) throw new Error(`block not found: ${startPattern}`);
  const open = css.indexOf('{', start);
  let depth = 0;
  let end = open;
  for (let i = open; i < css.length; i++) {
    if (css[i] === '{') depth++;
    if (css[i] === '}' && --depth === 0) {
      end = i;
      break;
    }
  }
  const body = css.slice(open + 1, end);
  const vars: Record<string, string> = {};
  for (const m of body.matchAll(/--([a-z-]+):\s*(#[0-9a-fA-F]{6})/g)) vars[m[1] as string] = m[2] as string;
  return vars;
}

const light = block(/^:root\s*\{/m);
const dark = block(/:root:not\(\[data-theme='light'\]\)\s*\{/);
const high = block(/:root\[data-contrast='high'\]\s*\{/);

function luminance(hex: string): number {
  const c = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (c[0] as number) + 0.7152 * (c[1] as number) + 0.0722 * (c[2] as number);
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT_PAIRS: [string, string][] = [
  ['text', 'bg'],
  ['text', 'surface'],
  ['muted', 'bg'],
  ['muted', 'surface'],
  ['on-accent', 'accent'],
  ['verified-fg', 'verified-bg'],
  ['inferred-fg', 'inferred-bg'],
  ['unverified-fg', 'unverified-bg'],
  ['rejected-fg', 'rejected-bg'],
  ['on-alert', 'alert-bg'],
  ['on-sim', 'sim-bg'],
];

const UI_PAIRS: [string, string][] = [
  ['border', 'surface'],
  ['border', 'bg'],
  ['focus', 'surface'],
  ['focus', 'bg'],
  ['accent', 'surface'],
];

describe('WCAG 2.2 AA colour contrast, computed from styles.css (WCAG 2.2 AA target, not certification)', () => {
  for (const [theme, tokens] of [
    ['light', light],
    ['dark', dark],
    ['high contrast', high],
  ] as const) {
    describe(theme, () => {
      it('defines every token it needs (dark and high contrast override light)', () => {
        const merged = { ...light, ...tokens };
        for (const [a, b] of [...TEXT_PAIRS, ...UI_PAIRS]) {
          expect(merged[a], `${theme}: --${a}`).toMatch(/^#/);
          expect(merged[b], `${theme}: --${b}`).toMatch(/^#/);
        }
      });
      it.each(TEXT_PAIRS)('text --%s on --%s is at least 4.5:1', (fg, bg) => {
        const m = { ...light, ...tokens };
        expect(contrast(m[fg] as string, m[bg] as string), `${theme}: ${fg}/${bg}`).toBeGreaterThanOrEqual(4.5);
      });
      it.each(UI_PAIRS)('boundary --%s against --%s is at least 3:1', (fg, bg) => {
        const m = { ...light, ...tokens };
        expect(contrast(m[fg] as string, m[bg] as string), `${theme}: ${fg}/${bg}`).toBeGreaterThanOrEqual(3);
      });
    });
  }

  it('the contrast helper matches known WCAG values', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 0);
    expect(contrast('#777777', '#ffffff')).toBeCloseTo(4.48, 1);
  });
});
