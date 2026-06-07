'use strict';

let currentPath = '';
let currentPlayingPath = null;
let initialNavDone = false;
let selectedPath = null;
let selectedItem = null;   // DOM <li> reference for the selected row
let lastState = 'stopped';
let lastDuration = 0;

// ---------------------------------------------------------------------------
// File browser
// ---------------------------------------------------------------------------

function browse(path) {
  if (path !== currentPath) {
    selectedPath = null;
    selectedItem = null;
    updatePlayButton();
  }
  currentPath = path;
  fetch('/api/files?path=' + encodeURIComponent(path))
    .then(r => r.json())
    .then(data => {
      if (data.error) { showBrowserError(data.message || data.error); return; }
      renderBrowser(data);
    })
    .catch(() => showBrowserError('Could not reach the server.'));
}

function showBrowserError(msg) {
  document.getElementById('breadcrumb').innerHTML = '';
  const ul = document.getElementById('file-list');
  ul.innerHTML = '';
  const li = document.createElement('li');
  li.className = 'config-error';
  li.textContent = msg;
  ul.appendChild(li);
}

function renderBrowser(data) {
  renderBreadcrumb(data.path);
  const ul = document.getElementById('file-list');
  ul.innerHTML = '';

  if (data.parent !== null && data.parent !== undefined) {
    ul.appendChild(makeItem({ name: '..', type: 'parent', path: data.parent }));
  }

  (data.entries || []).forEach(e => ul.appendChild(makeItem(e)));

  const active = ul.querySelector('li.active');
  if (active) active.scrollIntoView({ behavior: 'smooth', block: 'center' });

  if (data.pending_durations > 0) {
    const refreshPath = data.path;
    setTimeout(() => { if (currentPath === refreshPath) browse(refreshPath); }, 5000);
  }
}

function makeItem(entry) {
  const li = document.createElement('li');
  li.dataset.type = entry.type;
  li.dataset.path = entry.path;
  if (entry.path === currentPlayingPath) li.classList.add('active');
  if (entry.path === selectedPath) {
    li.classList.add('selected');
    selectedItem = li;  // refresh DOM reference after re-render
  }

  const icon = document.createElement('span');
  icon.className = 'icon';
  icon.textContent = iconFor(entry.type);

  const label = document.createElement('span');
  label.className = 'col-name';
  label.textContent = entry.name;

  const dur = document.createElement('span');
  dur.className = 'col-dur';
  dur.textContent = (entry.type === 'audio' && entry.duration) ? entry.duration : '';

  const typeBadge = document.createElement('span');
  typeBadge.className = 'col-type';
  typeBadge.textContent = entry.type === 'audio' ? 'MP3'
    : entry.type === 'playlist' ? 'PLS'
    : entry.type === 'dir' ? 'DIR'
    : '';

  li.appendChild(icon);
  li.appendChild(label);
  li.appendChild(dur);
  li.appendChild(typeBadge);

  li.addEventListener('click', () => {
    if (entry.type === 'dir' || entry.type === 'parent') {
      browse(entry.path);
    } else {
      selectEntry(li, entry.path);
    }
  });

  li.addEventListener('dblclick', () => {
    if (entry.type !== 'dir' && entry.type !== 'parent') {
      selectEntry(li, entry.path);
      playTrack(entry.path);
    }
  });

  return li;
}

function iconFor(type) {
  switch (type) {
    case 'dir':      return '▶';   // ▶
    case 'playlist': return '≡';   // ≡
    case 'audio':    return '♪';   // ♪
    case 'parent':   return '↑';   // ↑
    default:         return '';
  }
}

function renderBreadcrumb(path) {
  const el = document.getElementById('breadcrumb');
  el.innerHTML = '';

  const root = makeSpan('Home', 'crumb');
  root.addEventListener('click', () => browse(''));
  el.appendChild(root);

  if (!path) return;

  const parts = path.split('/');
  parts.forEach((part, i) => {
    el.appendChild(makeSpan(' / ', 'crumb-sep'));
    if (i === parts.length - 1) {
      el.appendChild(makeSpan(part, 'crumb-current'));
    } else {
      const partial = parts.slice(0, i + 1).join('/');
      const span = makeSpan(part, 'crumb');
      span.addEventListener('click', () => browse(partial));
      el.appendChild(span);
    }
  });
}

function fmtTime(secs) {
  secs = Math.floor(secs || 0);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function makeSpan(text, cls) {
  const s = document.createElement('span');
  s.textContent = text;
  s.className = cls;
  return s;
}

// ---------------------------------------------------------------------------
// Player commands
// ---------------------------------------------------------------------------

function selectEntry(li, path) {
  if (selectedItem) selectedItem.classList.remove('selected');
  selectedItem = li;
  selectedPath = path;
  li.classList.add('selected');
  updatePlayButton();
}

function updatePlayButton() {
  const btn = document.getElementById('btn-playpause');
  btn.disabled = lastState !== 'stopped' ? false : !selectedPath;
}

function resetProgressBar() {
  document.getElementById('prog-fill').style.width = '0%';
  document.getElementById('prog-current').textContent = '0:00';
  document.getElementById('prog-total').textContent = '0:00';
}

function playTrack(path) {
  post('/api/play', { path }).then(r => {
    if (!r) return;
    r.json().then(renderStatus);
  });
}

function cmdPlayPause() {
  if (lastState === 'stopped') {
    if (!selectedPath) return;
    playTrack(selectedPath);
  } else {
    post('/api/pause');
  }
}

function cmdPrev()    { post('/api/previous'); }
function cmdNext()    { post('/api/next'); }
function cmdShuffle() { post('/api/shuffle'); }
function cmdStop() { post('/api/stop'); }

function post(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
  .then(r => { setTimeout(pollStatus, 250); return r; })
  .catch(() => {});
}

// ---------------------------------------------------------------------------
// Status polling
// ---------------------------------------------------------------------------

function pollStatus() {
  fetch('/api/status')
    .then(r => r.json())
    .then(renderStatus)
    .catch(() => {});
}

function renderStatus(s) {
  // Track name
  document.getElementById('track-name').textContent =
    s.track_name || 'Nothing playing';

  // Playlist position
  document.getElementById('track-pos').textContent =
    s.playlist_len > 1
      ? `Track ${s.playlist_pos} of ${s.playlist_len}`
      : '';

  // Backend signals a new track started — reset the progress bar
  if (s.track_changed) {
    resetProgressBar();
  }

  // Follow the currently playing file/stream; for streams use the source playlist path
  const newPath = s.current_path || s.source_path || null;
  const pathChanged = newPath !== currentPlayingPath;
  if (pathChanged) currentPlayingPath = newPath;

  if (!initialNavDone) {
    initialNavDone = true;
    // On fresh page load navigate to the folder containing what's playing, or root
    const folder = (newPath && s.state !== 'stopped')
      ? newPath.includes('/') ? newPath.split('/').slice(0, -1).join('/') : ''
      : '';
    browse(folder);
  } else if (pathChanged) {
    refreshFileList();
  }

  // State badge
  const badge = document.getElementById('state-badge');
  badge.textContent = s.state;
  badge.className = 'badge ' + s.state;

  lastState = s.state;
  const active = s.state !== 'stopped';
  const isStream = !s.current_path && !!s.track_name;

  // Single play/pause toggle button — pause is meaningless for live streams
  const btnPP = document.getElementById('btn-playpause');
  if (s.state === 'playing') {
    btnPP.innerHTML = '&#9646;&#9646; Pause';
    btnPP.title = isStream ? 'Use Stop to disconnect' : 'Pause';
    btnPP.disabled = isStream;
  } else if (s.state === 'paused') {
    btnPP.innerHTML = '&#9654; Resume';
    btnPP.title = 'Resume';
    btnPP.disabled = false;
  } else {
    btnPP.innerHTML = '&#9654; Play';
    btnPP.title = 'Play';
    btnPP.disabled = !selectedPath;
  }

  // Stop — only useful while something is loaded
  document.getElementById('btn-stop').disabled = !active;

  // Prev / Next / Shuffle
  document.getElementById('btn-prev').disabled = !s.has_prev;
  document.getElementById('btn-next').disabled = !s.has_next;
  document.getElementById('btn-shuffle').classList.toggle('is-active', !!s.shuffle);

  // Progress bar
  const dur = s.duration || 0;
  lastDuration = dur;
  const progBar = document.getElementById('progressbar');
  if (s.state !== 'stopped' && s.track_name) {
    document.getElementById('prog-current').textContent = fmtTime(dur ? Math.min(s.position, dur) : s.position);
    if (!isStream && dur > 0) {
      const pct = Math.min(100, ((s.position || 0) / dur) * 100).toFixed(1);
      document.getElementById('prog-fill').style.width = pct + '%';
      document.getElementById('prog-total').textContent = fmtTime(dur);
      progBar.classList.remove('is-stream', 'is-broken');
    } else {
      document.getElementById('prog-fill').style.width = '';
      document.getElementById('prog-total').textContent = 'LIVE';
      progBar.classList.add('is-stream');
      progBar.classList.remove('is-broken');
    }
    progBar.hidden = false;
  } else if (s.state === 'stopped' && isStream) {
    // stream broke — track_name still set but nothing playing
    document.getElementById('prog-current').textContent = fmtTime(s.position);
    document.getElementById('prog-total').textContent = 'OFFLINE';
    progBar.classList.add('is-stream', 'is-broken');
    progBar.hidden = false;
  } else {
    progBar.hidden = true;
    progBar.classList.remove('is-stream', 'is-broken');
  }
}

function refreshFileList() {
  // Re-render with same path to update active highlight
  browse(currentPath);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.getElementById('prog-hit').addEventListener('click', function (e) {
  if (lastState === 'stopped') return;
  const rect = this.getBoundingClientRect();
  const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  post('/api/seek', { fraction: frac });
});

setInterval(pollStatus, 1000);
pollStatus();
