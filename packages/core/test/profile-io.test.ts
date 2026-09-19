import { describe, expect, it } from 'vitest';
import { applySituation, getPersona, type SignedProfile } from '@sense/protocol';
import { exportSpki, generateKeyPair } from '@sense/identity';
import { exportProfile, importProfile } from '../src';

describe('portable signed profile', () => {
  it('round-trips a profile signed by its owner, including a situational preset', async () => {
    const keys = await generateKeyPair();
    const profile = applySituation({ ...getPersona('deaf'), allergens: ['peanut'] }, 'noisy-room');
    const file = await exportProfile(profile, keys);
    expect(file.format).toBe('sense-profile/0.1');
    const back = await importProfile(JSON.parse(JSON.stringify(file)), await exportSpki(keys.publicKey));
    expect(back).toEqual(profile);
  });

  it('rejects a tampered profile', async () => {
    const keys = await generateKeyPair();
    const file = await exportProfile(getPersona('blind'), keys);
    const tampered: SignedProfile = { ...file, profile: { ...file.profile, speechRate: 3 } };
    await expect(importProfile(tampered)).rejects.toThrow(/signature is invalid/);
  });

  it('rejects a file signed by someone else when the owner key is known', async () => {
    const owner = await generateKeyPair();
    const other = await generateKeyPair();
    const file = await exportProfile(getPersona('motor'), other);
    await expect(importProfile(file, await exportSpki(owner.publicKey))).rejects.toThrow(/different key/);
  });

  it('rejects files that fail schema validation', async () => {
    await expect(importProfile({ format: 'sense-profile/0.1', profile: {}, publicKey: 'x', signature: 'y' })).rejects.toThrow();
  });
});
