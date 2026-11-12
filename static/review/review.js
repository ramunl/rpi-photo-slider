/* Library review: catalog loading, filtering, decisions, and player controls.
 *
 * All data is written into the DOM through createElement/textContent rather
 * than innerHTML, and every fetch goes through the centralized api helper so
 * responses are timed, checked, and never allowed to overwrite a newer view.
 */
(function () {
  'use strict';

  const { api } = window.PhotoReviewApi;

  let catalog;
  let filtered = [];
  let page = 0;
  const selection = new Set();
  const size = 60;
  const $ = (id) => document.getElementById(id);

  function element(tag, text) {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    return e;
  }

  async function task(fn) {
    try {
      $('message').textContent = 'Working…';
      const result = await fn();
      $('message').textContent = (result && result.message) || 'Done.';
    } catch (e) {
      $('message').textContent = e.message;
    }
  }

  function savedAlbum() {
    try {
      return localStorage.getItem('photo-library-album') || '';
    } catch {
      return '';
    }
  }

  function rememberAlbum() {
    try {
      localStorage.setItem('photo-library-album', $('playYear').value);
    } catch {
      /* storage can be unavailable in private browsing */
    }
  }

  $('playYear').onchange = rememberAlbum;

  async function loadAlbums(preferred) {
    const selected = preferred || $('playYear').value || savedAlbum();
    const folders = await api('/api/folders');
    const options = [
      ...Object.entries(catalog.summary.years).map(([year, n]) => {
        const o = element('option', `${year} — ${n} photos`);
        o.value = year;
        return o;
      }),
      ...folders.map((f) => {
        const o = element('option', `${f.name} — ${f.count} photos (folder)`);
        o.value = 'folder:' + f.id;
        o.title = f.path;
        return o;
      }),
    ];
    $('playYear').replaceChildren(...options);
    if (options.some((o) => o.value === selected)) $('playYear').value = selected;
    else if (catalog.summary.years['2010']) $('playYear').value = '2010';
    rememberAlbum();
  }

  async function load() {
    catalog = await api('/api/catalog');
    const s = catalog.summary;
    $('summary').textContent = `${s.scanned.toLocaleString()} files checked · ${
      s.status.included || 0
    } included · ${s.status.review || 0} need review · ${
      s.status.broken || 0
    } broken${s.complete ? '' : ' · SCAN STILL IN PROGRESS'}`;
    await loadAlbums();
    const value = $('reason').value;
    $('reason').replaceChildren(element('option', 'Any reason'));
    $('reason').firstChild.value = '';
    for (const [r, n] of Object.entries(s.review_reasons)) {
      const o = element('option', `${r.replaceAll('_', ' ')} (${n})`);
      o.value = r;
      $('reason').append(o);
    }
    $('reason').value = value;
    filter();
  }

  function filter() {
    const status = $('status').value;
    const reason = $('reason').value;
    const query = $('search').value.toLowerCase();
    const similar = new Set(catalog.near_duplicates.flatMap((p) => [p.a, p.b]));
    filtered = catalog.items.filter(
      (r) =>
        (status === 'all' ||
          (status === 'near' ? similar.has(r.id) : r.status === status)) &&
        (!reason || r.review_reasons.includes(reason)) &&
        (!query || (r.path + ' ' + (r.year || '')).toLowerCase().includes(query))
    );
    page = 0;
    render();
  }

  function render() {
    const rows = filtered.slice(page * size, (page + 1) * size);
    $('grid').replaceChildren();
    $('count').textContent = `${filtered.length} items · ${selection.size} selected`;
    $('page').textContent = `Page ${page + 1} of ${Math.max(
      1,
      Math.ceil(filtered.length / size)
    )}`;
    $('prev').disabled = page === 0;
    $('next').disabled = (page + 1) * size >= filtered.length;

    for (const r of rows) {
      const card = element('article');
      if (r.kind === 'photo') {
        const img = element('img');
        img.src = '/thumb/' + r.id;
        img.loading = 'lazy';
        img.alt = r.path.split('/').pop();
        img.onerror = () => {
          img.hidden = true;
        };
        img.onclick = () => detail(r);
        card.append(img);
      }
      const label = element('label');
      const box = element('input');
      box.type = 'checkbox';
      box.checked = selection.has(r.id);
      box.onchange = () => {
        if (box.checked) selection.add(r.id);
        else selection.delete(r.id);
        $('count').textContent = `${filtered.length} items · ${selection.size} selected`;
      };
      label.append(box, document.createTextNode(` ${r.year || 'Unknown year'} · ${r.status}`));
      card.append(
        label,
        element('small', r.path.split('/').pop()),
        element('small', r.review_reasons.join(', ').replaceAll('_', ' ')),
        element('small', r.date_source || 'No capture date')
      );
      if (r.decision && Object.keys(r.decision).length) {
        card.append(element('small', 'Decision: ' + JSON.stringify(r.decision)));
      }
      const more = element('button', 'Details');
      more.onclick = () => detail(r);
      card.append(more);
      $('grid').append(card);
    }
  }

  function detail(r) {
    const container = $('detailBody');
    container.replaceChildren();
    const img = element('img');
    img.src = '/thumb/' + r.id;
    container.append(
      img,
      element('p', r.path),
      element('p', `Date: ${r.date || 'unknown'} (${r.date_source || 'none'})`),
      element('p', 'Date evidence: ' + JSON.stringify(r.date_candidates)),
      element('p', 'Health: ' + r.health + ' ' + (r.error || '')),
      element('p', 'Warnings: ' + (r.warnings || []).join('; '))
    );
    if (r.duplicate_of) {
      const other = catalog.items.find((x) => x.id === r.duplicate_of);
      container.append(element('p', 'Duplicate of: ' + other.path));
    }
    const matches = catalog.near_duplicates
      .filter((p) => p.a === r.id || p.b === r.id)
      .slice(0, 12);
    for (const pair of matches) {
      const other = catalog.items.find((x) => x.id === (pair.a === r.id ? pair.b : pair.a));
      const match = element('img');
      match.src = '/thumb/' + other.id;
      match.className = 'similar-thumb';
      container.append(element('p', 'Similar: ' + other.path), match);
    }
    $('detail').showModal();
  }

  async function decide(classification, year) {
    if (!selection.size) throw Error('Select pictures first.');
    const result = await api('/api/decision', {
      ids: [...selection],
      classification,
      year,
    });
    selection.clear();
    render();
    return result;
  }

  $('keep').onclick = () => task(() => decide('photo'));
  $('exclude').onclick = () => task(() => decide('exclude'));
  $('reset').onclick = () => task(() => decide('reset'));
  $('date').onclick = () =>
    task(() => {
      if (!$('setYear').value) throw Error('Enter the correct year.');
      return decide(null, $('setYear').value);
    });
  $('apply').onclick = () =>
    task(async () => {
      await api('/api/apply', {});
      await load();
      return { message: 'Year library and playlists updated.' };
    });

  $('play').onclick = () =>
    task(() => {
      const value = $('playYear').value;
      if (!value) throw Error('Select a year or add a photo folder.');
      return api('/api/player', {
        command: 'start',
        ...(value.startsWith('folder:') ? { folder: value.slice(7) } : { year: value }),
      });
    });

  $('addFolder').onclick = () =>
    task(async () => {
      const button = $('addFolder');
      button.disabled = true;
      $('message').textContent = 'Choose a photo folder in the Pi folder chooser…';
      try {
        const result = await api('/api/folders/add', {});
        if (result.album) await loadAlbums('folder:' + result.album.id);
        return result;
      } finally {
        button.disabled = false;
      }
    });

  document.querySelectorAll('[data-command]').forEach((b) => {
    b.onclick = () => task(() => api('/api/player', { command: b.dataset.command }));
  });

  for (const id of ['status', 'reason']) $(id).onchange = filter;
  $('search').oninput = filter;
  $('selectPage').onclick = () => {
    filtered.slice(page * size, (page + 1) * size).forEach((r) => selection.add(r.id));
    render();
  };
  $('clear').onclick = () => {
    selection.clear();
    render();
  };
  $('prev').onclick = () => {
    page--;
    render();
  };
  $('next').onclick = () => {
    page++;
    render();
  };
  $('close').onclick = () => $('detail').close();

  function init() {
    task(load);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
