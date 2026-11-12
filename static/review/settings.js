/* Face-framing settings controls.
 *
 * Settings load independently of the catalog so the panel still works while an
 * audit is running. Disabling face detection disables the zoom input without
 * discarding its value. Validation errors and save failures are shown inline;
 * a rapid or stale save can never overwrite the current state.
 */
(function () {
  'use strict';

  const { api, StaleRequestError } = window.PhotoReviewApi;

  const enabledInput = document.getElementById('faceEnabled');
  const zoomInput = document.getElementById('faceZoom');
  const saveButton = document.getElementById('saveSettings');
  const status = document.getElementById('settingsStatus');

  let settings = { enabled: false, zoom_percent: 40 };
  let saveSequence = 0;

  function showStatus(text, kind) {
    status.textContent = text || '';
    status.classList.toggle('error', kind === 'error');
    status.classList.toggle('warning', kind === 'warning');
  }

  function applySettings() {
    enabledInput.checked = settings.enabled;
    zoomInput.value = String(settings.zoom_percent);
    zoomInput.disabled = !settings.enabled;
  }

  async function loadSettings() {
    try {
      const result = await api('/api/settings');
      settings = result.settings;
      applySettings();
      showStatus('', 'ok');
    } catch (error) {
      if (error instanceof StaleRequestError) return;
      showStatus(error.message, 'error');
    }
  }

  function readForm() {
    const text = zoomInput.value.trim();
    if (text === '') {
      throw new Error('Enter a zoom increase between 0 and 300.');
    }
    const zoom = Number(text);
    if (!Number.isFinite(zoom) || zoom < 0 || zoom > 300) {
      throw new Error('Zoom increase must be a number between 0 and 300.');
    }
    return { enabled: enabledInput.checked, zoom_percent: zoom };
  }

  async function saveSettings() {
    const sequence = ++saveSequence;
    let body;
    try {
      body = readForm();
    } catch (error) {
      showStatus(error.message, 'error');
      return;
    }
    showStatus('Saving…', 'ok');
    try {
      const result = await api('/api/settings', body);
      if (sequence !== saveSequence) return;
      settings = result.settings;
      applySettings();
      showStatus(
        result.message || 'Face settings saved.',
        result.applied === false ? 'warning' : 'ok'
      );
    } catch (error) {
      if (error instanceof StaleRequestError || sequence !== saveSequence) return;
      showStatus(error.message, 'error');
    }
  }

  enabledInput.addEventListener('change', () => {
    zoomInput.disabled = !enabledInput.checked;
  });
  saveButton.addEventListener('click', saveSettings);
  zoomInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') saveSettings();
  });

  function init() {
    applySettings();
    loadSettings();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
