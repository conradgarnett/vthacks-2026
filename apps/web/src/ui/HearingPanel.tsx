import { useRef, useState } from 'react';
import { LocalHeuristicClassifier, WindowedClassifier, spatialize, synthSiren } from '@sense/hearing';
import { SimBadge } from './Badges';

interface PostFn {
  (path: string, body?: unknown): Promise<unknown>;
}

/**
 * Echo: sound to sight and touch. Audio is analysed on this device and never stored or uploaded;
 * only the resulting sound event (label, confidence, direction) is sent to the local server.
 */
export function HearingPanel({ post }: { post: PostFn }) {
  const [note, setNote] = useState('');
  const [listening, setListening] = useState(false);
  const stop = useRef<(() => void) | null>(null);

  async function analyseSample() {
    // Synthesize a siren-like wail on this device, place it on the left, and run the real DSP on it.
    const sr = 16_000;
    const s = spatialize(synthSiren({ sampleRate: sr, seconds: 2 }), 285);
    const [event] = new LocalHeuristicClassifier().classify({ left: s.left, right: s.right, sampleRate: sr, at: 0 });
    if (!event) return setNote('Nothing recognised in the sample.');
    setNote(`Analysed a synthetic sample on this device: ${event.label}, ${Math.round(event.confidence * 100)}% confidence.`);
    await post('/api/sound', { ...event });
  }

  async function startMic() {
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 2, echoCancellation: false }, video: false });
      const ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(media);
      const node = ctx.createScriptProcessor(4096, 2, 1);
      const windowed = new WindowedClassifier(new LocalHeuristicClassifier(), ctx.sampleRate, 2, 1);
      node.onaudioprocess = (e) => {
        const l = e.inputBuffer.getChannelData(0);
        const r = e.inputBuffer.numberOfChannels > 1 ? e.inputBuffer.getChannelData(1) : undefined;
        for (const ev of windowed.push(new Float32Array(l), r ? new Float32Array(r) : undefined)) void post('/api/sound', { ...ev });
      };
      src.connect(node);
      node.connect(ctx.destination);
      stop.current = () => {
        node.disconnect();
        src.disconnect();
        media.getTracks().forEach((t) => t.stop());
        void ctx.close();
      };
      setListening(true);
      setNote(
        'Listening. Audio is analysed in memory on this device and is not stored. A phone microphone is usually mono, so direction will often be unknown.',
      );
    } catch {
      setNote('The microphone is not available or permission was not given. Use the simulated soundscape or the synthetic sample instead.');
    }
  }

  return (
    <section aria-labelledby="hearing-h" className="panel">
      <h2 id="hearing-h">
        Echo: sounds around you <SimBadge label="SIMULATED SOUNDS" />
      </h2>
      <p className="meta">
        Sound labels are inferences with a confidence, from simple signal rules, not a trained model. A verified building alarm always
        outranks them.
      </p>
      <div className="toolbar" role="group" aria-label="Sound sources">
        <button type="button" onClick={() => void post('/api/soundscape', {})}>
          Play simulated soundscape
        </button>
        <button type="button" onClick={() => void analyseSample()}>
          Analyse a synthetic siren on this device
        </button>
        {listening ? (
          <button
            type="button"
            onClick={() => {
              stop.current?.();
              setListening(false);
              setNote('Microphone stopped.');
            }}
          >
            Stop microphone
          </button>
        ) : (
          <button type="button" onClick={() => void startMic()}>
            Start microphone (local only)
          </button>
        )}
      </div>
      <p className="meta" role="status">
        {note}
      </p>
    </section>
  );
}
