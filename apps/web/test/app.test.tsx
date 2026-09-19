// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { mountApp, settle } from './harness';

afterEach(cleanup);

async function a11yViolations(container: HTMLElement) {
  const results = await axe.run(container, {
    // jsdom cannot compute layout or colors: contrast is verified from the design tokens (contrast.test.ts).
    // Page-level rules are irrelevant to a component subtree.
    rules: {
      'color-contrast': { enabled: false },
      'document-title': { enabled: false },
      'html-has-lang': { enabled: false },
      'landmark-one-main': { enabled: false },
      'page-has-heading-one': { enabled: false },
    },
  });
  return results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(' ')).join(' | ')})`);
}

describe('honesty labels and initial state', () => {
  it('always shows SIMULATED WORLD, ANS-modeled, MOCK AI and offline mode, plus the limitations notice', async () => {
    await mountApp();
    const badges = await screen.findByRole('list', { name: /what is real and what is simulated/i });
    expect(within(badges).getByText('SIMULATED WORLD')).toBeTruthy();
    expect(within(badges).getByText('ANS-modeled (simulated)')).toBeTruthy();
    expect(within(badges).getByText('MOCK AI')).toBeTruthy();
    expect(within(badges).getByText('Offline mode')).toBeTruthy();
    expect(screen.getByText(/not a medical device and not a certified safety system/i)).toBeTruthy();
    expect(screen.getByText(/Profile data sent to remote agents: none \(of 0 messages\)/)).toBeTruthy();
  });

  it('has no axe violations on the empty state', async () => {
    const { container } = await mountApp();
    await screen.findByText('Live percept feed');
    expect(await a11yViolations(container)).toEqual([]);
  });
});

describe('scene 1: arrive (Blind persona)', () => {
  it('shows a VERIFIED card naming the source, 7 passes in the Trust Inspector, and no profile data disclosed', async () => {
    const user = userEvent.setup();
    const { container } = await mountApp();
    await user.click(await screen.findByRole('button', { name: /arrive at riverside hall/i }));
    const feed = await screen.findByRole('list', { name: /percepts, newest first/i });
    await waitFor(() => expect(within(feed).getAllByText(/Riverside Hall: Verified, 7 of 7 checks/).length).toBeGreaterThan(0));
    const card = within(feed)
      .getAllByText(/Riverside Hall: Verified, 7 of 7 checks/)[0]
      ?.closest('li') as HTMLElement;
    expect(within(card).getByText('VERIFIED')).toBeTruthy();
    expect(within(card).getAllByText('SIMULATED').length).toBeGreaterThan(0);
    expect(card.textContent).toContain('riverside-hall.sim');

    await user.click(screen.getByRole('button', { name: /riverside-hall\.sim/i }));
    const steps = await screen.findByRole('list', { name: /verification steps for riverside-hall\.sim/i });
    expect(within(steps).getAllByText('Pass')).toHaveLength(7);
    expect(screen.getByText(/7 of 7 steps passed/)).toBeTruthy();

    expect(screen.getByText(/Profile data sent to remote agents: none \(of \d+ messages\)/)).toBeTruthy();
    expect(await a11yViolations(container)).toEqual([]);
  });
});

describe('scene 4: alarm and spoof (Deaf persona)', () => {
  it('announces the verified alarm assertively with direction, shows vibration, and rejects the spoof', async () => {
    const user = userEvent.setup();
    const { container } = await mountApp();
    await user.click(await screen.findByLabelText(/Deaf \/ hard of hearing/));
    await user.click(screen.getByRole('button', { name: /arrive at riverside hall/i }));
    await screen.findAllByText(/7 of 7 checks/);
    await user.click(screen.getByRole('button', { name: /^fire alarm$/i }));

    const alert = await screen.findByRole('alert', {}, { timeout: 3000 }).catch(() => undefined);
    const region = alert ?? (container.querySelector('#alert-region') as HTMLElement);
    await waitFor(() => expect(region.textContent).toContain('Fire alarm, East stairwell. Verified, Riverside Hall.'));
    expect(region.getAttribute('aria-live')).toBe('assertive');
    expect(region.textContent).toMatch(/3 o'clock, 14 m/);

    const feed = screen.getByRole('list', { name: /percepts, newest first/i });
    const card = within(feed).getAllByText('Fire alarm, East stairwell. Verified, Riverside Hall.')[0]?.closest('li') as HTMLElement;
    expect(card.getAttribute('data-urgency')).toBe('4');
    // Deaf persona: visual + haptic, and this jsdom device cannot vibrate, so the pattern is shown as text.
    expect(within(card).getByText(/Vibration: four very long buzzes, preceded by 2 quick taps \(right\)/)).toBeTruthy();
    expect(card.textContent).toMatch(/cannot vibrate/);
    expect(card.textContent).not.toMatch(/Caption:/); // no audio for a Deaf user
    expect(within(card).getByText('Life safety', { exact: false })).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /spoofed .false alarm./i }));
    const security = await screen.findByRole('list', { name: /security events/i }, { timeout: 3000 });
    await waitFor(() => expect(within(security).getAllByText(/Identity rejected/).length).toBeGreaterThan(0));
    expect(security.textContent).toMatch(/failed step 2: Server certificate chains to a trusted root and matches the FQDN/);
    expect(security.textContent).toMatch(/Spoofed dismissal blocked/);
    expect(feed.textContent).not.toMatch(/false alarm|all clear/i);

    await user.click(within(card).getByRole('button', { name: /^acknowledge$/i }));
    await waitFor(() => expect(within(card).getByRole('button', { name: /acknowledged/i })).toBeTruthy());
    expect((within(card).getByRole('button', { name: /acknowledged/i }) as HTMLButtonElement).disabled).toBe(true);
    expect(await a11yViolations(container)).toEqual([]);
  });

  it('uses the Vibration API when the device has one', async () => {
    const user = userEvent.setup();
    const { vibrations } = await mountApp({ vibrate: true });
    await user.click(await screen.findByLabelText(/Deaf \/ hard of hearing/));
    await user.click(screen.getByRole('button', { name: /arrive at riverside hall/i }));
    await screen.findAllByText(/7 of 7 checks/);
    await user.click(screen.getByRole('button', { name: /^fire alarm$/i }));
    await waitFor(() => expect(vibrations.length).toBeGreaterThan(0));
    expect(vibrations.at(-1)).toEqual([40, 70, 40, 70, 160, 500, 150, 500, 150, 500, 150, 500]);
    const feed = screen.getByRole('list', { name: /percepts, newest first/i });
    expect(within(feed).getAllByText(/Sent to your device/).length).toBeGreaterThan(0);
  });
});

describe('scene 2: ask (Blind persona)', () => {
  it('answers with the verified map and the inferred camera scene, each with its own badge, labelled mock', async () => {
    const user = userEvent.setup();
    const { container } = await mountApp();
    await user.click(await screen.findByRole('button', { name: /arrive at riverside hall/i }));
    await screen.findAllByText('Riverside Hall: Verified, 7 of 7 checks.');
    expect(screen.getByText('SAMPLE VIEW')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /^ask$/i }));
    const feed = screen.getByRole('list', { name: /percepts, newest first/i });
    await waitFor(() => expect(within(feed).getAllByText(/Exit: Main entrance/).length).toBeGreaterThan(0));
    const exit = within(feed)
      .getAllByText(/Exit: Main entrance, 6 m, 6 o'clock/)[0]
      ?.closest('li') as HTMLElement;
    expect(within(exit).getByText('VERIFIED')).toBeTruthy();
    expect(exit.textContent).toContain('Riverside Hall');
    const trolley = within(feed)
      .getAllByText(/in the way/i)[0]
      ?.closest('li') as HTMLElement;
    expect(within(trolley).getByText(/INFERRED/)).toBeTruthy();
    expect(trolley.textContent).toMatch(/74%/);
    expect(trolley.textContent).toMatch(/Inferred, camera/);
    expect(feed.textContent).not.toMatch(/\bsafe\b|all clear/i);
    // Blind persona: audio modalities, captions shown because this device has no voice.
    expect(trolley.textContent).toMatch(/Caption:/);
    expect(screen.getAllByText('MOCK AI').length).toBeGreaterThan(0);
    expect(await a11yViolations(container)).toEqual([]);
  });
});

describe('Echo: sounds (Deaf persona)', () => {
  it('runs the real DSP on a synthetic siren on this device and shows an inferred, directional, hedged alert', async () => {
    const user = userEvent.setup();
    const { container } = await mountApp();
    await user.click(await screen.findByLabelText(/Deaf \/ hard of hearing/));
    await user.click(screen.getByRole('button', { name: /analyse a synthetic siren on this device/i }));
    const feed = await screen.findByRole('list', { name: /percepts, newest first/i });
    await waitFor(() => expect(within(feed).getAllByText('Siren-like sound, left. Inferred, microphone.').length).toBeGreaterThan(0));
    const card = within(feed).getAllByText('Siren-like sound, left. Inferred, microphone.')[0]?.closest('li') as HTMLElement;
    expect(within(card).getByText(/INFERRED \d+%/)).toBeTruthy();
    expect(card.textContent).toMatch(/Vibration:/);
    expect(card.textContent).toMatch(/Direction: .*bearing/);
    expect(screen.getByText(/Analysed a synthetic sample on this device: siren/)).toBeTruthy();
    expect(await a11yViolations(container)).toEqual([]);
  });

  it('plays the simulated soundscape, labelled simulated', async () => {
    const user = userEvent.setup();
    await mountApp();
    await user.click(await screen.findByRole('button', { name: /play simulated soundscape/i }));
    const feed = await screen.findByRole('list', { name: /percepts, newest first/i });
    await waitFor(() => expect(within(feed).getAllByText(/Knock-like sound/).length).toBeGreaterThan(0));
    expect(within(feed).getAllByText('SIMULATED').length).toBeGreaterThan(0);
  });
});

describe('ScentGuard panel (Anosmia persona)', () => {
  it('shows the level with text and a meter, the tier, and never says safe', async () => {
    const user = userEvent.setup();
    const { container } = await mountApp();
    await user.click(await screen.findByLabelText(/Cannot smell/));
    await user.click(screen.getByRole('button', { name: /arrive at riverside hall/i }));
    await screen.findAllByText('Riverside Hall: Verified, 7 of 7 checks.');
    await user.click(screen.getByRole('button', { name: /smoke rising \(script\)/i }));
    const panel = screen.getByRole('region', { name: /scentguard: smoke risk/i });
    await waitFor(() => expect(within(panel).getByText(/Level 1 of 4: Elevated/)).toBeTruthy());
    expect(within(panel).getByRole('meter', { name: /smoke risk level 1 of 4/i })).toBeTruthy();
    expect(within(panel).getByText('VERIFIED')).toBeTruthy();
    expect(within(panel).getByText(/Rules that fired: S1/)).toBeTruthy();
    const feed = screen.getByRole('list', { name: /percepts, newest first/i });
    const card = within(feed).getAllByText('Smoke risk level 1. Verified, Riverside Hall.')[0]?.closest('li') as HTMLElement;
    expect(card.textContent).toMatch(/Vibration:/);
    expect(panel.textContent).not.toMatch(/\bsafe\b(?! guarantee)/);
    expect(await a11yViolations(container)).toEqual([]);
  });
});

describe('profile controls', () => {
  it('switches all five personas, keeps allergens, and applies display preferences', async () => {
    const user = userEvent.setup();
    await mountApp();
    const allergens = (await screen.findByLabelText(/declared allergens/i)) as HTMLInputElement;
    await user.clear(allergens);
    await user.type(allergens, 'Peanut, sesame');
    await user.click(screen.getByRole('button', { name: /save allergens/i }));
    for (const label of [/Deaf/, /Motor-limited/, /Cannot smell/, /Impaired taste/, /Blind/]) {
      await user.click(screen.getByLabelText(label));
      await waitFor(() => expect((screen.getByLabelText(label) as HTMLInputElement).checked).toBe(true));
    }
    expect(document.documentElement.getAttribute('data-contrast')).toBe('high'); // Blind persona is high-contrast
    expect(document.documentElement.style.getPropertyValue('--text-scale')).toBe('1.5');
    await user.click(screen.getByLabelText(/Motor-limited/));
    await waitFor(() => expect(document.documentElement.getAttribute('data-contrast')).toBeNull());
    expect(document.documentElement.getAttribute('data-reduced-motion')).toBe('true');
    expect((screen.getByLabelText(/declared allergens/i) as HTMLInputElement).value).toBe('Peanut, sesame');
  });

  it('applies situational presets and builds a custom profile', async () => {
    const user = userEvent.setup();
    const { store } = await mountApp();
    await user.click(await screen.findByLabelText('Noisy room', { exact: false }));
    await waitFor(() => expect(store.getState().snapshot?.profile.situational?.preset).toBe('noisy-room'));
    expect(store.getState().snapshot?.profile.output['4']).not.toContain('speech');

    await user.click(screen.getByText('Custom profile builder'));
    const name = screen.getByLabelText('Name');
    await user.clear(name);
    await user.type(name, 'Café mode');
    await user.click(screen.getByRole('button', { name: /use this profile/i }));
    await waitFor(() => expect(store.getState().snapshot?.profile.name).toBe('Café mode'));
  });

  it('mutes voice and sounds and reports a problem when the server rejects something', async () => {
    const user = userEvent.setup();
    const { store } = await mountApp();
    await user.click(await screen.findByRole('button', { name: /mute voice and sounds/i }));
    expect(store.getState().muted).toBe(true);
    expect(screen.getByRole('button', { name: /muted/i }).getAttribute('aria-pressed')).toBe('true');
    await store.post('/api/profile/persona', { personaId: 'wizard' });
    expect((await screen.findByText(/invalid request/i)).closest('[role="alert"]')).toBeTruthy();
  });
});

describe('keyboard-only operation', () => {
  it('reaches every control in a sensible order and operates the golden path without a mouse', async () => {
    const user = userEvent.setup();
    const { container } = await mountApp();
    await screen.findByText('Live percept feed');

    const tabbables: string[] = [];
    (document.body as HTMLElement).focus();
    for (let i = 0; i < 80; i++) {
      await user.tab();
      const el = document.activeElement as HTMLElement;
      const label = (el.getAttribute('aria-label') ?? el.textContent ?? el.id ?? el.tagName).trim().slice(0, 40);
      if (tabbables.includes(label + i) || (tabbables.length > 3 && label === tabbables[0]?.replace(/\d+$/, ''))) break;
      tabbables.push(label + i);
    }
    expect(tabbables[0]).toMatch(/^Skip to content/);
    expect(tabbables.join('|')).toMatch(/Mute voice and sounds/);
    expect(tabbables.join('|')).toMatch(/Arrive at Riverside Hall/);
    expect(tabbables.join('|')).toMatch(/Save allergens/);

    // Every enabled interactive control is in the tab order (nothing is mouse-only).
    const interactive = Array.from(
      container.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select, summary, a[href]'),
    );
    expect(interactive.length).toBeGreaterThan(15);
    for (const el of interactive) expect(el.tabIndex).toBeGreaterThanOrEqual(0);

    // Operate: Enter on "Arrive", then Space on "Fire alarm".
    const arrive = screen.getByRole('button', { name: /arrive at riverside hall/i });
    arrive.focus();
    await user.keyboard('{Enter}');
    await screen.findAllByText(/7 of 7 checks/);
    const fire = screen.getByRole('button', { name: /^fire alarm$/i });
    fire.focus();
    await user.keyboard(' ');
    await waitFor(() => expect(container.querySelector('#alert-region')?.textContent).toContain('Fire alarm'));

    // Acknowledge with the keyboard.
    const feed = screen.getByRole('list', { name: /percepts, newest first/i });
    const ack = within(feed).getAllByRole('button', { name: /^acknowledge$/i })[0] as HTMLElement;
    ack.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(within(feed).getAllByRole('button', { name: /acknowledged/i }).length).toBeGreaterThan(0));

    // Arrow keys change persona in the radio group.
    const blind = screen.getByLabelText(/Blind/) as HTMLInputElement;
    blind.focus();
    await user.keyboard('{ArrowDown}');
    await waitFor(() => expect((screen.getByLabelText(/Deaf/) as HTMLInputElement).checked).toBe(true));
    expect(await a11yViolations(container)).toEqual([]);
  });

  it('text and motion preferences are honoured: rem-based sizing and a reduced-motion stylesheet', async () => {
    const { readFileSync } = await import('node:fs');
    const { join } = await import('node:path');
    const css = readFileSync(join(import.meta.dirname, '../src/styles.css'), 'utf8');
    expect(css).toMatch(/prefers-reduced-motion: reduce/);
    expect(css).toMatch(/font-size: calc\(100% \* var\(--text-scale\)\)/);
    expect(css).toMatch(/:focus-visible/);
    expect(css).not.toMatch(/font-size:\s*\d+px/); // no fixed pixel font sizes
    await settle(1);
  });
});
