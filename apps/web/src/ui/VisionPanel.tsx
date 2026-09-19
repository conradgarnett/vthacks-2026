import { useEffect, useRef, useState } from 'react';
import { SimBadge } from './Badges';

interface PostFn {
  (path: string, body?: unknown): Promise<unknown>;
}

const SAMPLE_QUESTIONS = [
  'Where is the nearest exit and is anything in my way?',
  'What does the sign say?',
  'Where is the door?',
  'What do you see around me?',
];

function toBase64(dataUrl: string): { base64: string; mediaType: 'image/jpeg' | 'image/png' } {
  const [head, data = ''] = dataUrl.split(',');
  return { base64: data, mediaType: head?.includes('png') ? 'image/png' : 'image/jpeg' };
}

/** Ask about the surroundings. Works with a mock sample view offline, an uploaded image, or the device camera. */
export function VisionPanel({ post, aiLabel }: { post: PostFn; aiLabel: string }) {
  const [question, setQuestion] = useState(SAMPLE_QUESTIONS[0] as string);
  const [source, setSource] = useState<'sample' | 'upload' | 'camera'>('sample');
  const [image, setImage] = useState<{ base64: string; mediaType: 'image/jpeg' | 'image/png' } | undefined>();
  const [cameraNote, setCameraNote] = useState<string>('');
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);

  useEffect(
    () => () => {
      stream.current?.getTracks().forEach((t) => t.stop());
    },
    [],
  );

  async function startCamera() {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
      stream.current = s;
      if (video.current) video.current.srcObject = s;
      setCameraNote('Camera on. Frames stay in memory and are not saved.');
    } catch {
      setCameraNote('The camera is not available or permission was not given. Use the sample view or upload an image instead.');
    }
  }

  function captureFrame() {
    const v = video.current;
    if (!v || !v.videoWidth) return setCameraNote('Start the camera first.');
    const canvas = document.createElement('canvas');
    canvas.width = Math.min(v.videoWidth, 1024);
    canvas.height = Math.round((canvas.width / v.videoWidth) * v.videoHeight);
    canvas.getContext('2d')?.drawImage(v, 0, 0, canvas.width, canvas.height);
    setImage(toBase64(canvas.toDataURL('image/jpeg', 0.8)));
    setCameraNote('Frame captured (kept in memory only).');
  }

  function onUpload(file: File | undefined) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setImage(toBase64(String(reader.result)));
    reader.readAsDataURL(file);
  }

  const frame = source === 'sample' ? { fixture: 'lobby' } : image ? { imageBase64: image.base64, mediaType: image.mediaType } : {};

  return (
    <section aria-labelledby="vision-h" className="panel">
      <h2 id="vision-h">Ask about your surroundings {source === 'sample' && <SimBadge label="SAMPLE VIEW" />}</h2>
      <p className="meta">
        Answers combine the verified building map with what the camera seems to show. Each statement says which. AI: {aiLabel}. People are
        counted, never identified.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void post('/api/ask', { question, ...frame });
        }}
      >
        <label htmlFor="question">Your question</label>
        <div className="toolbar">
          <input id="question" type="text" value={question} onChange={(e) => setQuestion(e.target.value)} size={44} />
          <button type="submit" className="primary" data-touchless="Ask the question">
            Ask
          </button>
        </div>
      </form>
      <div className="toolbar" role="group" aria-label="Sample questions">
        {SAMPLE_QUESTIONS.map((q) => (
          <button key={q} type="button" onClick={() => setQuestion(q)}>
            {q}
          </button>
        ))}
      </div>
      <fieldset>
        <legend>Camera view</legend>
        <label>
          <input type="radio" name="vision-source" checked={source === 'sample'} onChange={() => setSource('sample')} /> Sample view (works
          offline, labelled as mock)
        </label>{' '}
        <label>
          <input type="radio" name="vision-source" checked={source === 'upload'} onChange={() => setSource('upload')} /> Upload an image
        </label>{' '}
        <label>
          <input type="radio" name="vision-source" checked={source === 'camera'} onChange={() => setSource('camera')} /> Device camera
        </label>
        {source === 'upload' && (
          <p>
            <label htmlFor="vision-file">Image file</label>{' '}
            <input id="vision-file" type="file" accept="image/*" onChange={(e) => onUpload(e.target.files?.[0])} />
          </p>
        )}
        {source === 'camera' && (
          <div>
            <div className="toolbar">
              <button type="button" onClick={() => void startCamera()}>
                Start camera
              </button>
              <button type="button" onClick={captureFrame}>
                Capture frame
              </button>
            </div>
            <video ref={video} autoPlay playsInline muted aria-label="Camera preview" style={{ maxWidth: '100%' }} />
          </div>
        )}
        <p className="meta" role="status">
          {cameraNote}
          {source !== 'sample' && !image ? ' No image yet.' : ''}
        </p>
        <button type="button" disabled={source !== 'sample' && !image} onClick={() => void post('/api/see', frame)}>
          Describe this view
        </button>
      </fieldset>
    </section>
  );
}
