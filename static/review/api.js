/* Centralized timed fetches for the review page.
 *
 * Every request is bounded by a timeout, GETs are sent with a no-store cache
 * directive, and every response is checked for a successful HTTP status and a
 * JSON body. When the page is hidden or navigated away, in-flight requests are
 * aborted and a generation counter is bumped so that any already-resolved stale
 * response is ignored instead of overwriting current state.
 */
(function () {
  'use strict';

  const timeoutMs = window.REVIEW_HTTP_TIMEOUT || 10000;
  let generation = 0;
  let controller = new AbortController();

  function departView() {
    generation += 1;
    controller.abort();
    controller = new AbortController();
  }

  class StaleRequestError extends Error {
    constructor() {
      super('stale request');
      this.name = 'StaleRequestError';
    }
  }

  async function api(path, body) {
    const token = generation;
    const requestController = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      requestController.abort();
    }, timeoutMs);
    const onDepart = () => requestController.abort();
    if (controller.signal.aborted) {
      onDepart();
    } else {
      controller.signal.addEventListener('abort', onDepart, { once: true });
    }
    try {
      const options = body === undefined
        ? { method: 'GET', cache: 'no-store', signal: requestController.signal }
        : {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: requestController.signal,
          };
      const response = await fetch(path, options);
      if (token !== generation) throw new StaleRequestError();
      const data = await response.json().catch(() => ({}));
      if (token !== generation) throw new StaleRequestError();
      if (!response.ok || data.error) {
        throw new Error(data.error || response.statusText || 'Request failed');
      }
      return data;
    } catch (error) {
      if (error instanceof StaleRequestError) throw error;
      if (requestController.signal.aborted) {
        if (timedOut) throw new Error('The request timed out.');
        if (token !== generation) throw new StaleRequestError();
        throw new Error('The request was cancelled.');
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') departView();
  });
  window.addEventListener('pagehide', departView);

  window.PhotoReviewApi = { api, departView, StaleRequestError };
})();
