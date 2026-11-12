/* jsdom tests for the review settings controls and the centralized fetch layer. */

'use strict';

const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..', '..');

function read(relative) {
  return fs.readFileSync(path.join(ROOT, relative), 'utf8');
}

const HTML = read('review.html').replace(/<\s*script[^>]*><\/\s*script>/g, '');

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/* Minimal fetch shim: routes map a URL to a response spec, a promise, or a
 * function of the request options. A spec of `{ hang: true }` stays pending so
 * the client timeout/abort path is exercised. The shim honors AbortSignal. */
function createFetch(routes) {
  const calls = [];
  function fetchImpl(url, options = {}) {
    calls.push({ url, options });
    const signal = options.signal;
    return new Promise((resolve, reject) => {
      let settled = false;
      const done = (spec, error) => {
        if (settled) return;
        settled = true;
        if (signal) signal.removeEventListener('abort', onAbort);
        if (error) return reject(error);
        const wrapped = spec && typeof spec === 'object' && 'body' in spec ? spec : { body: spec };
        resolve({
          ok: wrapped.ok !== false,
          status: wrapped.status || 200,
          statusText: wrapped.statusText || 'OK',
          json: async () => (wrapped.body === undefined ? {} : wrapped.body),
        });
      };
      const onAbort = () => {
        done(null, Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' }));
      };
      if (signal && signal.aborted) return onAbort();
      if (signal) signal.addEventListener('abort', onAbort, { once: true });

      const route = routes[url];
      const spec = typeof route === 'function' ? route(options) : route;
      if (spec && typeof spec.then === 'function') {
        spec.then((value) => done(value), (err) => done(null, err));
      } else if (spec && spec.hang) {
        /* intentionally pending until aborted */
      } else if (spec && spec.delay !== undefined) {
        setTimeout(() => done(spec.body), spec.delay);
      } else {
        done(spec);
      }
    });
  }
  return { fetchImpl, calls };
}

function loadPage({ fetch, timeout } = {}) {
  const dom = new JSDOM(HTML, {
    url: 'http://127.0.0.1:8787/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
  });
  const { window } = dom;
  if (timeout !== undefined) window.REVIEW_HTTP_TIMEOUT = timeout;
  window.fetch = fetch || (async () => {
    throw new Error('unexpected fetch');
  });
  window.eval(read('static/review/api.js'));
  window.eval(read('static/review/review.js'));
  window.eval(read('static/review/settings.js'));
  return dom;
}

function settingsRoutes(settings, { hang = false } = {}) {
  return {
    '/api/catalog': { status: 503, ok: false, body: { error: 'Audit is still running' } },
    '/api/folders': [],
    '/api/settings': hang ? { hang: true } : { settings },
  };
}

test('loads the default 40% zoom and disables it while faces are off', async () => {
  const { fetchImpl } = createFetch(settingsRoutes({ enabled: false, zoom_percent: 40 }));
  const dom = loadPage({ fetch: fetchImpl });
  await flush();
  const { document } = dom.window;
  assert.strictEqual(document.getElementById('faceZoom').value, '40');
  assert.strictEqual(document.getElementById('faceEnabled').checked, false);
  assert.strictEqual(document.getElementById('faceZoom').disabled, true);
});

test('reloads saved values into the controls', async () => {
  const { fetchImpl } = createFetch(settingsRoutes({ enabled: true, zoom_percent: 75 }));
  const dom = loadPage({ fetch: fetchImpl });
  await flush();
  const { document } = dom.window;
  assert.strictEqual(document.getElementById('faceEnabled').checked, true);
  assert.strictEqual(document.getElementById('faceZoom').disabled, false);
  assert.strictEqual(document.getElementById('faceZoom').value, '75');
});

test('disabling faces disables the zoom input and retains its value', async () => {
  const { fetchImpl } = createFetch(settingsRoutes({ enabled: true, zoom_percent: 75 }));
  const dom = loadPage({ fetch: fetchImpl });
  await flush();
  const { document, Event } = dom.window;
  const enabled = document.getElementById('faceEnabled');
  const zoom = document.getElementById('faceZoom');
  enabled.checked = false;
  enabled.dispatchEvent(new Event('change', { bubbles: true }));
  assert.strictEqual(zoom.disabled, true);
  assert.strictEqual(zoom.value, '75');
});

test('invalid zoom values are rejected locally without a save request', async () => {
  const { fetchImpl, calls } = createFetch(settingsRoutes({ enabled: false, zoom_percent: 40 }));
  const dom = loadPage({ fetch: fetchImpl });
  await flush();
  const { document } = dom.window;
  const zoom = document.getElementById('faceZoom');
  const save = document.getElementById('saveSettings');
  const status = document.getElementById('settingsStatus');

  zoom.value = '500';
  save.click();
  await flush();
  assert.match(status.textContent, /between 0 and 300/);

  zoom.value = 'abc';
  save.click();
  await flush();
  assert.match(status.textContent, /between 0 and 300/);

  assert.ok(!calls.some((c) => c.url === '/api/settings' && c.options.method === 'POST'));
});

test('a hung settings request times out with a visible error', async () => {
  const { fetchImpl } = createFetch(settingsRoutes(null, { hang: true }));
  const dom = loadPage({ fetch: fetchImpl, timeout: 20 });
  await new Promise((resolve) => setTimeout(resolve, 80));
  const status = dom.window.document.getElementById('settingsStatus');
  assert.match(status.textContent, /timed out/);
});

test('rapid settings updates ignore a stale earlier response', async () => {
  let resolveFirst;
  const first = new Promise((resolve) => {
    resolveFirst = resolve;
  });
  const routes = {
    '/api/catalog': { status: 503, ok: false, body: { error: 'Audit is still running' } },
    '/api/settings': (options) => {
      if (!options.method || options.method === 'GET') {
        return { settings: { enabled: false, zoom_percent: 40 } };
      }
      const body = JSON.parse(options.body);
      if (body.zoom_percent === 60) return first;
      return { settings: body, applied: true, message: 'saved' };
    },
  };
  const { fetchImpl } = createFetch(routes);
  const dom = loadPage({ fetch: fetchImpl });
  await flush();
  const { document } = dom.window;
  const zoom = document.getElementById('faceZoom');
  const save = document.getElementById('saveSettings');

  zoom.value = '60';
  save.click();
  await flush();

  zoom.value = '80';
  save.click();
  await flush();
  assert.strictEqual(zoom.value, '80');

  resolveFirst({ settings: { enabled: false, zoom_percent: 60 }, applied: true, message: 'saved' });
  await flush();
  assert.strictEqual(zoom.value, '80');
  assert.strictEqual(document.getElementById('settingsStatus').textContent, 'saved');
});

test('navigation away aborts requests and late responses cannot overwrite state', async () => {
  let resolveSave;
  const savePromise = new Promise((resolve) => {
    resolveSave = resolve;
  });
  const routes = {
    '/api/catalog': { status: 503, ok: false, body: { error: 'Audit is still running' } },
    '/api/settings': (options) => {
      if (!options.method || options.method === 'GET') {
        return { settings: { enabled: false, zoom_percent: 40 } };
      }
      return savePromise;
    },
  };
  const { fetchImpl } = createFetch(routes);
  const dom = loadPage({ fetch: fetchImpl });
  await flush();
  const { document, Event } = dom.window;

  document.getElementById('saveSettings').click();
  await flush();
  dom.window.dispatchEvent(new Event('pagehide'));
  resolveSave({ settings: { enabled: true, zoom_percent: 55 }, applied: true, message: 'saved' });
  await flush();

  assert.strictEqual(document.getElementById('faceEnabled').checked, false);
  assert.strictEqual(document.getElementById('faceZoom').value, '40');
  assert.notStrictEqual(document.getElementById('settingsStatus').textContent, 'saved');
});
