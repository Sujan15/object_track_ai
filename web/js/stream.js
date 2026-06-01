// web/js/stream.js
// MODIFIED: streams are OFF by default (no auto-start on page load).
// All other features (toggle, overlay, reconnect, stats) are preserved.

(function () {
  const lineIds = [1, 2, 3, 4];
  let currentOverlayIndex = 0;
  const wsConnections = {};
  const reconnectTimers = {};
  const RECONNECT_BASE_MS  = 2000;
  const RECONNECT_MAX_MS   = 16000;
  const reconnectAttempts  = {};

  function createCameraCards() {
    const grid = document.getElementById('camera-grid');
    if (!grid) return;
    grid.innerHTML = '';
    lineIds.forEach(id => {
      const card = document.createElement('div');
      card.className = 'camera-card';
      card.dataset.lineId = id;
      card.innerHTML = `
        <div class="camera-header">
          <span><i class="fas fa-video"></i> Line ${id}</span>
          <span class="stream-status" id="status-${id}" style="font-size:11px;opacity:.6">Paused</span>
          <button class="stream-toggle" data-line="${id}" data-streaming="false">
            <i class="fas fa-play-circle"></i> Paused
          </button>
        </div>
        <div class="camera-feed">
          <img id="video-${id}" class="stream-frame" alt="Live feed from line ${id}">
        </div>
        <div class="camera-stats">
          <div class="stat-item">
            <span class="stat-value" id="total-${id}">0</span>
            <span class="stat-label">Total</span>
          </div>
          <div class="stat-item">
            <span class="stat-value" id="defects-${id}">0</span>
            <span class="stat-label">Defects</span>
          </div>
          <div class="stat-item">
            <span class="stat-value" id="rate-${id}">0%</span>
            <span class="stat-label">Defect%</span>
          </div>
        </div>
      `;
      grid.appendChild(card);
    });
  }

  function _attachFrame(img, data) {
    const prevUrl = img._blobUrl || null;
    const blob    = new Blob([data], { type: 'image/jpeg' });
    const url     = URL.createObjectURL(blob);
    img.onload = () => {
      if (prevUrl) URL.revokeObjectURL(prevUrl);
    };
    img._blobUrl = url;
    img.src      = url;
  }

  function startStream(lineId) {
    if (wsConnections[lineId]) return;
    clearTimeout(reconnectTimers[lineId]);

    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws       = new WebSocket(`${protocol}://${location.host}/ws/stream/${lineId}`);
    ws.binaryType  = 'arraybuffer';

    ws.onopen = () => {
      reconnectAttempts[lineId] = 0;
      _setStatus(lineId, 'Live', 'green');
    };

    ws.onmessage = (event) => {
      const img = document.getElementById(`video-${lineId}`);
      if (img) _attachFrame(img, event.data);
    };

    ws.onerror = () => {
      _setStatus(lineId, 'Error', 'red');
    };

    ws.onclose = () => {
      delete wsConnections[lineId];
      const btn = document.querySelector(`.stream-toggle[data-line="${lineId}"]`);
      if (btn && btn.dataset.streaming === 'true') {
        _setStatus(lineId, 'Reconnecting…', 'orange');
        const attempt = (reconnectAttempts[lineId] || 0) + 1;
        reconnectAttempts[lineId] = attempt;
        const delay = Math.min(RECONNECT_BASE_MS * Math.pow(1.5, attempt - 1), RECONNECT_MAX_MS);
        reconnectTimers[lineId] = setTimeout(() => startStream(lineId), delay);
      } else {
        _setStatus(lineId, 'Paused', '');
      }
    };

    wsConnections[lineId] = ws;
  }

  function stopStream(lineId) {
    clearTimeout(reconnectTimers[lineId]);
    const ws = wsConnections[lineId];
    if (ws) {
      ws.onclose = null;
      ws.close();
      delete wsConnections[lineId];
    }
    const img = document.getElementById(`video-${lineId}`);
    if (img) {
      if (img._blobUrl) { URL.revokeObjectURL(img._blobUrl); img._blobUrl = null; }
      img.src = '';
    }
  }

  function toggleStream(lineId) {
    const btn = document.querySelector(`.stream-toggle[data-line="${lineId}"]`);
    if (wsConnections[lineId]) {
      btn.dataset.streaming = 'false';
      btn.innerHTML = '<i class="fas fa-play-circle"></i> Paused';
      stopStream(lineId);
      _setStatus(lineId, 'Paused', '');
    } else {
      btn.dataset.streaming = 'true';
      btn.innerHTML = '<i class="fas fa-pause-circle"></i> Streaming';
      startStream(lineId);
    }
  }

  function _setStatus(lineId, text, color) {
    const el = document.getElementById(`status-${lineId}`);
    if (el) {
      el.textContent = text;
      el.style.color = color || '';
    }
  }

  function updateStats({ allLines }) {
    for (const [lineId, data] of Object.entries(allLines || {})) {
      const stats   = data.stats || {};
      const total   = stats.total   || 0;
      const defects = stats.defects || 0;
      const rate    = total > 0 ? ((defects / total) * 100).toFixed(1) : '0';
      _setText(`total-${lineId}`,   total);
      _setText(`defects-${lineId}`, defects);
      _setText(`rate-${lineId}`,    `${rate}%`);
    }
  }

  function _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  // Overlay functions (unchanged)
  function openOverlay(index) {
    const lineId = lineIds[index];
    if (!lineId) return;
    currentOverlayIndex = index;

    const overlay = document.getElementById('single-view-overlay');
    const img     = document.getElementById('overlay-video');
    document.getElementById('overlay-title').textContent = `Line ${lineId}`;

    _closeOverlayWs();

    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws       = new WebSocket(`${protocol}://${location.host}/ws/stream/${lineId}`);
    ws.binaryType  = 'arraybuffer';
    ws.onmessage   = (e) => { if (img) _attachFrame(img, e.data); };
    ws.onerror     = () => {};
    window._overlayWs = ws;

    overlay.classList.add('open');

    const s   = window.App?.state?.lastStats?.[lineId] || {};
    _setText('overlay-total',   s.total   || 0);
    _setText('overlay-defects', s.broken  || 0);
    const rate = s.total > 0 ? ((s.broken / s.total) * 100).toFixed(1) : '0';
    _setText('overlay-rate', rate);
  }

  function closeOverlay() {
    document.getElementById('single-view-overlay')?.classList.remove('open');
    _closeOverlayWs();
  }

  function _closeOverlayWs() {
    if (window._overlayWs) {
      window._overlayWs.onclose = null;
      window._overlayWs.close();
      delete window._overlayWs;
    }
    const img = document.getElementById('overlay-video');
    if (img && img._blobUrl) {
      URL.revokeObjectURL(img._blobUrl);
      img._blobUrl = null;
      img.src = '';
    }
  }

  function navigateOverlay(delta) {
    let idx = currentOverlayIndex + delta;
    if (idx < 0) idx = lineIds.length - 1;
    if (idx >= lineIds.length) idx = 0;
    openOverlay(idx);
  }

  document.addEventListener('DOMContentLoaded', () => {
    createCameraCards();

    if (window.App) window.App.registerStatsHandler(updateStats);

    // ⚠️ REMOVED auto-start loop – streams are now OFF by default.
    // Users must click the toggle button to start each stream manually.

    // Toggle button events
    document.getElementById('camera-grid')?.addEventListener('click', (e) => {
      const btn = e.target.closest('.stream-toggle');
      if (btn) {
        e.stopPropagation();
        toggleStream(parseInt(btn.dataset.line));
      }
    });

    // Overlay controls
    document.getElementById('overlay-close')?.addEventListener('click', closeOverlay);
    document.getElementById('overlay-prev')?.addEventListener('click', () => navigateOverlay(-1));
    document.getElementById('overlay-next')?.addEventListener('click', () => navigateOverlay(1));

    // Double-click card → overlay
    document.querySelectorAll('.camera-card').forEach((card, idx) => {
      card.addEventListener('dblclick', () => openOverlay(idx));
    });

    // View mode buttons
    document.querySelectorAll('.view-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        if (btn.dataset.view === 'single') openOverlay(0);
        else closeOverlay();
      });
    });
  });
})();
