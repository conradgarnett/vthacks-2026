import { AnthropicClient, DEFAULT_MODEL } from './anthropic';
import { LlmTasteProvider, MockTasteProvider, type TasteProvider } from './taste';
import { LlmVisionProvider, MockVisionProvider, type VisionProvider } from './scene';
import type { LlmClient, LlmRequest } from './llm';

export * from './llm';
export * from './anthropic';
export * from './scene';
export * from './taste';
export * from './audio';

/** A scripted LLM for tests and offline demos: returns whatever the responder produces. */
export class MockLlmClient implements LlmClient {
  readonly name = 'mock' as const;
  readonly label = 'MOCK AI' as const;
  readonly calls: LlmRequest[] = [];
  constructor(private readonly responder: (req: LlmRequest, call: number) => string | object) {}
  async complete(req: LlmRequest): Promise<string> {
    this.calls.push(req);
    const out = this.responder(req, this.calls.length);
    return typeof out === 'string' ? out : JSON.stringify(out);
  }
}

export interface Providers {
  vision: VisionProvider;
  taste: TasteProvider;
  /** "MOCK AI" or "ANTHROPIC": what the UI shows. */
  label: 'MOCK AI' | 'ANTHROPIC';
  model?: string;
}

/**
 * Choose providers from the environment. The live provider is used only when SENSE_PROVIDER is
 * "anthropic", or is unset and ANTHROPIC_API_KEY is present. Everything else uses offline mocks.
 */
export function createProviders(env: Record<string, string | undefined> = process.env, fetchImpl?: typeof fetch): Providers {
  const wantsLive = env.SENSE_PROVIDER === 'anthropic' || (env.SENSE_PROVIDER !== 'mock' && Boolean(env.ANTHROPIC_API_KEY));
  if (wantsLive && env.ANTHROPIC_API_KEY) {
    const client = new AnthropicClient({
      apiKey: env.ANTHROPIC_API_KEY,
      model: env.SENSE_MODEL ?? DEFAULT_MODEL,
      ...(fetchImpl ? { fetchImpl } : {}),
    });
    return { vision: new LlmVisionProvider(client), taste: new LlmTasteProvider(client), label: 'ANTHROPIC', model: client.model };
  }
  return { vision: new MockVisionProvider(), taste: new MockTasteProvider(), label: 'MOCK AI' };
}
