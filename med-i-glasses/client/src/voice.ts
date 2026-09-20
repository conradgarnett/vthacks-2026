/**
 * Tap-to-talk speech input: one tap starts listening, recognition ends itself
 * after one utterance, a second tap ends it early.
 *
 * A tap rather than a wake word: continuous listening drains the battery,
 * fires on other people's conversation, and on iOS pops a permission prompt
 * the user cannot see. It is a tap rather than a held button because phones
 * cancel a held press as a scroll or a long-press before the user has spoken.
 *
 * Web Speech recognition is Chrome and Safari only, and both keep it behind a
 * vendor prefix, so callers must check `isSupported` and offer another route
 * when it is false.
 */

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: any) => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
};

type Handlers = {
  onTranscript: (text: string) => void;
  onStateChange: (listening: boolean) => void;
  onError: (message: string) => void;
};

const getRecognitionCtor = (): (new () => SpeechRecognitionLike) | null => {
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

export class Voice {
  private recognition: SpeechRecognitionLike | null = null;
  private listening = false;
  private settled = false;

  constructor(private handlers: Handlers, private lang = "en-US") {}

  get isSupported(): boolean {
    return getRecognitionCtor() !== null;
  }

  get isListening(): boolean {
    return this.listening;
  }

  start(): void {
    if (this.listening) return;

    const Ctor = getRecognitionCtor();
    if (!Ctor) {
      this.handlers.onError("Voice input isn't supported in this browser.");
      return;
    }

    const recognition = new Ctor();
    recognition.lang = this.lang;
    recognition.continuous = false;
    // Only a final result is ever used, so interim ones are not requested.
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    this.settled = false;

    recognition.onresult = (event: any) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (!result.isFinal) continue;
        const transcript = String(result[0].transcript).trim();
        if (transcript && !this.settled) {
          this.settled = true;
          this.handlers.onTranscript(transcript);
        }
      }
    };

    recognition.onerror = (event: any) => {
      // "aborted" and "no-speech" are normal when releasing the button early;
      // announcing them would make the assistant sound broken.
      const code = event?.error;
      if (code !== "aborted" && code !== "no-speech") {
        this.handlers.onError(
          code === "not-allowed"
            ? "Microphone permission was denied."
            : "I didn't catch that."
        );
      }
      this.finish();
    };

    recognition.onend = () => this.finish();

    try {
      recognition.start();
      this.recognition = recognition;
      this.listening = true;
      this.handlers.onStateChange(true);
    } catch {
      this.handlers.onError("Couldn't start listening.");
      this.finish();
    }
  }

  /** Stop capturing but let a final result arrive. */
  stop(): void {
    if (!this.listening) return;
    try {
      this.recognition?.stop();
    } catch {
      this.finish();
    }
  }

  private finish(): void {
    this.listening = false;
    this.recognition = null;
    this.handlers.onStateChange(false);
  }
}
