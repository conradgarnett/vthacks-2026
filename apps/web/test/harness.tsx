import { render } from '@testing-library/react';
import { ManualClock } from '@sense/protocol';
import { HapticRenderer, Presenter, SpatialAudioRenderer, SpeechRenderer } from '@sense/render';
import {
  SenseContext,
  buildServer,
  registerDeviceRoutes,
  registerHearingRoutes,
  registerTasteRoutes,
  registerVisionRoutes,
} from '../../server/src';
import { App, type AppProps } from '../src/App';
import type { Scheduler } from '@sense/touchless';
import { ApiError, type SenseApi } from '../src/api';
import { createStore } from '../src/store';

/**
 * Runs the real SENSE server (broker + simulated world) in-process and connects the real React
 * app to it. No mocks between the UI and the broker: what the tests see is what the app does.
 */
export async function mountApp(opts: { vibrate?: boolean; scheduler?: Scheduler; touchless?: AppProps['touchless'] } = {}) {
  const ctx = await SenseContext.create({ env: {}, clock: new ManualClock(), online: false });
  const server = await buildServer(ctx, {
    extra: [registerVisionRoutes, registerHearingRoutes, registerTasteRoutes, registerDeviceRoutes],
  });
  const api: SenseApi = {
    getState: async () => (await server.inject({ method: 'GET', url: '/api/state' })).json(),
    get: async (path) => (await server.inject({ method: 'GET', url: path })).json(),
    post: async (path, body, via = 'pointer') => {
      const res = await server.inject({ method: 'POST', url: path, payload: (body ?? {}) as never, headers: { 'x-sense-via': via } });
      if (res.statusCode >= 400) throw new ApiError((res.json() as { error?: string }).error ?? 'error', res.statusCode);
      return res.json();
    },
    connect(onEvent, onStatus) {
      onStatus('open');
      return ctx.onEvent(onEvent);
    },
  };
  const vibrations: number[][] = [];
  const presenter = new Presenter(
    new SpeechRenderer({}),
    new SpatialAudioRenderer({}),
    new HapticRenderer(
      opts.vibrate
        ? {
            vibrate: (p) => {
              vibrations.push(p);
              return true;
            },
          }
        : {},
    ),
  );
  const store = createStore(api, presenter);
  const view = render(
    <App
      store={store}
      {...(opts.scheduler ? { scheduler: opts.scheduler } : {})}
      {...(opts.touchless ? { touchless: opts.touchless } : {})}
    />,
  );
  return { ctx, server, store, api, vibrations, ...view };
}

export const settle = (ms = 60) => new Promise((r) => setTimeout(r, ms));
