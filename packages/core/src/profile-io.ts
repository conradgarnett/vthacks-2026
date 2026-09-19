import {
  SensoryProfileSchema,
  SignedProfileSchema,
  canonicalJson,
  type SensoryProfile,
  type SignedProfile,
} from '@sense/protocol';
import { exportSpki, importSpki, signBytes, verifyBytes, type KeyPair } from '@sense/identity';

/** Export the profile as a signed local file the user owns. The key stays on the user's device. */
export async function exportProfile(
  profile: SensoryProfile,
  keys: KeyPair,
): Promise<SignedProfile> {
  const valid = SensoryProfileSchema.parse(profile);
  return {
    format: 'sense-profile/0.1',
    profile: valid,
    publicKey: await exportSpki(keys.publicKey),
    signature: await signBytes(keys.privateKey, new TextEncoder().encode(canonicalJson(valid))),
  };
}

/**
 * Import a signed profile. Fails on schema errors or a bad signature. If `expectedPublicKey` is
 * given (the user's own key), files signed by anyone else are rejected.
 */
export async function importProfile(
  raw: unknown,
  expectedPublicKey?: string,
): Promise<SensoryProfile> {
  const file = SignedProfileSchema.parse(raw);
  if (expectedPublicKey && file.publicKey !== expectedPublicKey) {
    throw new Error('profile was signed by a different key');
  }
  const ok = await verifyBytes(
    await importSpki(file.publicKey),
    new TextEncoder().encode(canonicalJson(file.profile)),
    file.signature,
  );
  if (!ok) throw new Error('profile signature is invalid');
  return file.profile;
}
