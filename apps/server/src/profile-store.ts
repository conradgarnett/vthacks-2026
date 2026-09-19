import { applySituation, getPersona, SensoryProfileSchema, type PersonaId, type SensoryProfile } from '@sense/protocol';

/**
 * The user's local profile. It lives in this process (the user's own device) and is never sent to
 * a remote agent. Allergens belong to the user, not to a persona, so they survive persona switches.
 */
export class ProfileStore {
  private base: SensoryProfile = getPersona('blind');
  private allergens: string[] = [];
  private situation: 'hands-full' | 'noisy-room' | undefined;
  private readonly listeners = new Set<(p: SensoryProfile) => void>();

  current(): SensoryProfile {
    let p: SensoryProfile = { ...structuredClone(this.base), allergens: [...this.allergens] };
    if (this.situation) p = applySituation(p, this.situation);
    return p;
  }

  onChange(fn: (p: SensoryProfile) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private changed(): void {
    const p = this.current();
    for (const fn of this.listeners) fn(p);
  }

  setPersona(id: PersonaId): SensoryProfile {
    this.base = getPersona(id);
    this.situation = undefined;
    this.changed();
    return this.current();
  }

  setCustom(profile: SensoryProfile): SensoryProfile {
    const valid = SensoryProfileSchema.parse(profile);
    this.base = valid;
    this.allergens = [...valid.allergens];
    this.situation = valid.situational?.preset;
    this.changed();
    return this.current();
  }

  setAllergens(list: string[]): SensoryProfile {
    this.allergens = Array.from(new Set(list.map((a) => a.trim().toLowerCase()).filter(Boolean)));
    this.changed();
    return this.current();
  }

  setSituation(preset: 'hands-full' | 'noisy-room' | null): SensoryProfile {
    this.situation = preset ?? undefined;
    this.changed();
    return this.current();
  }
}
