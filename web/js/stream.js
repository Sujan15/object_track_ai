// (function() {
//     const lineIds = [1, 2, 3, 4];
//     let currentOverlayIndex = 0;
//     const wsConnections = {};

//     function createCameraCards() {
//         const grid = document.getElementById('camera-grid');
//         if (!grid) return;
//         grid.innerHTML = '';
//         lineIds.forEach(id => {
//             const card = document.createElement('div');
//             card.className = 'camera-card';
//             card.dataset.lineId = id;
//             card.innerHTML = `
//                 <div class="camera-header">
//                     <span><i class="fas fa-video"></i> Line ${id}</span>
//                     <button class="stream-toggle" data-line="${id}"><i class="fas fa-play-circle"></i> Paused</button>
//                 </div>
//                 <div class="camera-feed">
//                     <img id="video-${id}" class="stream-frame" alt="Live feed from line ${id}">
//                 </div>
//                 <div class="camera-stats">
//                     <div class="stat-item"><span class="stat-value" id="total-${id}">0</span><span class="stat-label">Total</span></div>
//                     <div class="stat-item"><span class="stat-value" id="defects-${id}">0</span><span class="stat-label">Defects</span></div>
//                     <div class="stat-item"><span class="stat-value" id="rate-${id}">0%</span><span class="stat-label">Defect%</span></div>
//                 </div>
//             `;
//             grid.appendChild(card);
//         });
//     }

//     function startStream(lineId) {
//         if (wsConnections[lineId]) return;
//         const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
//         const ws = new WebSocket(`${protocol}://${location.host}/ws/stream/${lineId}`);
//         ws.binaryType = 'arraybuffer';
//         ws.onmessage = (event) => {
//             const blob = new Blob([event.data], {type: 'image/jpeg'});
//             const url = URL.createObjectURL(blob);
//             const img = document.getElementById(`video-${lineId}`);
//             if (img) {
//                 // Revoke previous blob URL to avoid memory leaks
//                 if (img.src && img.src.startsWith('blob:')) {
//                     URL.revokeObjectURL(img.src);
//                 }
//                 img.src = url;
//             } else {
//                 URL.revokeObjectURL(url);
//             }
//         };
//         ws.onclose = () => {
//             delete wsConnections[lineId];
//             setTimeout(() => {
//                 if (document.getElementById(`video-${lineId}`)) startStream(lineId);
//             }, 3000);
//         };
//         wsConnections[lineId] = ws;
//     }

//     function stopStream(lineId) {
//         if (wsConnections[lineId]) {
//             wsConnections[lineId].close();
//             delete wsConnections[lineId];
//         }
//         const img = document.getElementById(`video-${lineId}`);
//         if (img && img.src) {
//             if (img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
//             img.src = '';
//         }
//     }

//     function toggleStream(lineId) {
//         const btn = document.querySelector(`.stream-toggle[data-line="${lineId}"]`);
//         if (wsConnections[lineId]) {
//             stopStream(lineId);
//             if (btn) btn.innerHTML = '<i class="fas fa-play-circle"></i> Paused';
//         } else {
//             startStream(lineId);
//             if (btn) btn.innerHTML = '<i class="fas fa-pause-circle"></i> Streaming';
//         }
//     }

//     function updateStats({ allLines }) {
//         for (const [lineId, data] of Object.entries(allLines || {})) {
//             const stats = data.stats || {};
//             const total = stats.total || 0;
//             const defects = stats.defects || 0;
//             const rate = total > 0 ? ((defects / total) * 100).toFixed(1) : '0';
//             const totalEl = document.getElementById(`total-${lineId}`);
//             if (totalEl) totalEl.innerText = total;
//             const defectsEl = document.getElementById(`defects-${lineId}`);
//             if (defectsEl) defectsEl.innerText = defects;
//             const rateEl = document.getElementById(`rate-${lineId}`);
//             if (rateEl) rateEl.innerText = `${rate}%`;
//         }
//     }

//     // Single view overlay uses an <img> as well
//     function openOverlay(index) {
//         const lineId = lineIds[index];
//         if (!lineId) return;
//         currentOverlayIndex = index;
//         const overlay = document.getElementById('single-view-overlay');
//         const img = document.getElementById('overlay-video');
//         document.getElementById('overlay-title').innerText = `Line ${lineId}`;
//         if (window._overlayWs) {
//             window._overlayWs.close();
//             if (window._overlayUrl) {
//                 URL.revokeObjectURL(window._overlayUrl);
//                 window._overlayUrl = null;
//             }
//         }
//         const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
//         const ws = new WebSocket(`${protocol}://${location.host}/ws/stream/${lineId}`);
//         ws.binaryType = 'arraybuffer';
//         ws.onmessage = (e) => {
//             const blob = new Blob([e.data], {type: 'image/jpeg'});
//             const url = URL.createObjectURL(blob);
//             if (window._overlayUrl) URL.revokeObjectURL(window._overlayUrl);
//             window._overlayUrl = url;
//             img.src = url;
//         };
//         window._overlayWs = ws;
//         overlay.classList.add('open');
//         const allStats = window.App?.state?.lastStats || {};
//         const lineStats = allStats[lineId] || { total: 0, broken: 0 };
//         document.getElementById('overlay-total').innerText = lineStats.total || 0;
//         document.getElementById('overlay-defects').innerText = lineStats.broken || 0;
//         const rate = lineStats.total > 0 ? ((lineStats.broken / lineStats.total) * 100).toFixed(1) : '0';
//         document.getElementById('overlay-rate').innerText = rate;
//     }

//     function closeOverlay() {
//         const overlay = document.getElementById('single-view-overlay');
//         overlay.classList.remove('open');
//         if (window._overlayWs) {
//             window._overlayWs.close();
//             delete window._overlayWs;
//         }
//         if (window._overlayUrl) {
//             URL.revokeObjectURL(window._overlayUrl);
//             window._overlayUrl = null;
//         }
//     }

//     function navigateOverlay(delta) {
//         let newIndex = currentOverlayIndex + delta;
//         if (newIndex < 0) newIndex = lineIds.length - 1;
//         if (newIndex >= lineIds.length) newIndex = 0;
//         openOverlay(newIndex);
//     }

//     document.addEventListener('DOMContentLoaded', () => {
//         createCameraCards();
//         if (window.App) window.App.registerStatsHandler(updateStats);

//         // Attach toggle events after cards are created (event delegation recommended)
//         document.getElementById('camera-grid')?.addEventListener('click', (e) => {
//             const btn = e.target.closest('.stream-toggle');
//             if (btn) {
//                 e.stopPropagation();
//                 const lineId = parseInt(btn.dataset.line);
//                 toggleStream(lineId);
//             }
//         });

//         const overlay = document.getElementById('single-view-overlay');
//         document.getElementById('overlay-close')?.addEventListener('click', closeOverlay);
//         document.getElementById('overlay-prev')?.addEventListener('click', () => navigateOverlay(-1));
//         document.getElementById('overlay-next')?.addEventListener('click', () => navigateOverlay(1));

//         // Double-click on camera card opens overlay
//         document.querySelectorAll('.camera-card').forEach((card, idx) => {
//             card.addEventListener('dblclick', () => openOverlay(idx));
//         });

//         // View mode buttons
//         document.querySelectorAll('.view-btn').forEach(btn => {
//             btn.addEventListener('click', () => {
//                 document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
//                 btn.classList.add('active');
//                 if (btn.dataset.view === 'single') openOverlay(0);
//                 else closeOverlay();
//             });
//         });
//     });
// })();


// web/js/stream.js
// FIXES applied:
//   1. Blob URL leak fixed — revoke previous URL in img.onload (after browser consumed it)
//   2. Overlay WebSocket shares same revoke pattern
//   3. Auto-start streams on page load (removed "Paused" default)
//   4. Reconnect with exponential backoff (was fixed 3s)
//   5. Memory-safe stats update (no innerHTML thrash)

(function () {
  const lineIds = [1, 2, 3, 4];
  let currentOverlayIndex = 0;
  const wsConnections = {};          // lineId → WebSocket
  const reconnectTimers = {};        // lineId → setTimeout handle
  const RECONNECT_BASE_MS  = 2000;
  const RECONNECT_MAX_MS   = 16000;
  const reconnectAttempts  = {};     // lineId → attempt count

  // ── Card creation ──────────────────────────────────────────────────────────

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
          <span class="stream-status" id="status-${id}" style="font-size:11px;opacity:.6">Connecting…</span>
          <button class="stream-toggle" data-line="${id}">
            <i class="fas fa-pause-circle"></i> Streaming
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

  // ── WebSocket stream ───────────────────────────────────────────────────────

  /**
   * Attach a frame blob to an <img> element.
   * Revoke the previous blob URL AFTER the browser has decoded the new one
   * (inside onload) to prevent memory accumulation on 4-camera grids.
   */
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
    if (wsConnections[lineId]) return;   // already running
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
      // Only reconnect if the button is still in "Streaming" state
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
      ws.onclose = null;   // prevent auto-reconnect on manual stop
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
      btn.innerHTML = '<i class="fas fa-play-circle"></i> Start';
      stopStream(lineId);
    } else {
      btn.dataset.streaming = 'true';
      btn.innerHTML = '<i class="fas fa-pause-circle"></i> Streaming';
      startStream(lineId);
    }
  }

  function _setStatus(lineId, text, color) {
    const el = document.getElementById(`status-${lineId}`);
    if (el) {
      el.textContent     = text;
      el.style.color     = color || '';
    }
  }

  // ── Stats update ───────────────────────────────────────────────────────────

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

  // ── Single-view overlay ────────────────────────────────────────────────────

  function openOverlay(index) {
    const lineId = lineIds[index];
    if (!lineId) return;
    currentOverlayIndex = index;

    const overlay = document.getElementById('single-view-overlay');
    const img     = document.getElementById('overlay-video');
    document.getElementById('overlay-title').textContent = `Line ${lineId}`;

    // Close any existing overlay WebSocket
    _closeOverlayWs();

    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws       = new WebSocket(`${protocol}://${location.host}/ws/stream/${lineId}`);
    ws.binaryType  = 'arraybuffer';
    ws.onmessage   = (e) => { if (img) _attachFrame(img, e.data); };
    ws.onerror     = () => {};
    window._overlayWs = ws;

    overlay.classList.add('open');

    // Show last known stats for this line
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

  // ── Initialise ─────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    createCameraCards();

    if (window.App) window.App.registerStatsHandler(updateStats);

    // Auto-start all streams
    lineIds.forEach(id => {
      const btn = document.querySelector(`.stream-toggle[data-line="${id}"]`);
      if (btn) btn.dataset.streaming = 'true';
      startStream(id);
    });

    // Toggle button clicks (event delegation on grid)
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