// The sanitizer lives in @sense/protocol so sense modules can use it without depending on core.
export {
  detectAssuranceClaim,
  detectInstructionLike,
  sanitizePayload,
  sanitizeText,
  type PayloadSanitizeEvent,
  type SanitizeFlag,
  type SanitizeResult,
} from '@sense/protocol';
