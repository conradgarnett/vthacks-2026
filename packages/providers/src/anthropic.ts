import { ProviderError, type LlmClient, type LlmRequest } from './llm';

export const DEFAULT_MODEL = 'claude-sonnet-5';

export interface AnthropicOptions {
  apiKey: string;
  /** From SENSE_MODEL. */
  model?: string;
  baseUrl?: string;
  timeoutMs?: number;
  /** Injected in tests; no real network is used in unit tests. */
  fetchImpl?: typeof fetch;
}

/**
 * Minimal Messages API client over fetch. It is implemented from the public API shape and is
 * covered by stubbed-fetch tests only: it has NOT been exercised against the live API in this build
 * (no key was available), so treat live behaviour as unverified.
 */
export class AnthropicClient implements LlmClient {
  readonly name = 'anthropic' as const;
  readonly label = 'ANTHROPIC' as const;
  readonly model: string;

  constructor(private readonly opts: AnthropicOptions) {
    this.model = opts.model ?? DEFAULT_MODEL;
  }

  async complete(req: LlmRequest): Promise<string> {
    const fetchImpl = this.opts.fetchImpl ?? fetch;
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), this.opts.timeoutMs ?? 30_000);
    try {
      const content = [
        ...(req.images ?? []).map((i) => ({ type: 'image', source: { type: 'base64', media_type: i.mediaType, data: i.base64 } })),
        { type: 'text', text: req.prompt },
      ];
      const res = await fetchImpl(`${this.opts.baseUrl ?? 'https://api.anthropic.com'}/v1/messages`, {
        method: 'POST',
        signal: ctl.signal,
        headers: {
          'content-type': 'application/json',
          'x-api-key': this.opts.apiKey,
          'anthropic-version': '2023-06-01',
        },
        body: JSON.stringify({
          model: this.model,
          max_tokens: req.maxTokens ?? 1024,
          system: req.system,
          messages: [{ role: 'user', content }],
        }),
      });
      if (!res.ok) throw new ProviderError(`Anthropic API returned ${res.status}`, res.status);
      const data = (await res.json()) as { content?: { type: string; text?: string }[] };
      const text = (data.content ?? [])
        .filter((b) => b.type === 'text')
        .map((b) => b.text ?? '')
        .join('');
      if (!text) throw new ProviderError('Anthropic API returned no text');
      return text;
    } catch (err) {
      if (err instanceof ProviderError) throw err;
      throw new ProviderError(err instanceof Error ? `Anthropic request failed: ${err.message}` : 'Anthropic request failed');
    } finally {
      clearTimeout(timer);
    }
  }
}
