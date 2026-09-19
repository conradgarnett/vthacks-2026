// The safety guard lives in @sense/protocol so sense modules can use it without depending on core.
export {
  SafetyViolation,
  assertNoAssurance,
  containsAssurance,
  guardPercept,
  notDetectedUnverified,
  redactAssurance,
} from '@sense/protocol';
