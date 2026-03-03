/* ── CRT Debug Controller ─────────────────────────────────────── */
/* Ctrl+D to toggle.                                               */
/* Map generation defined in index.html <head> for sync loading.  */

const crtDebug = {

  // ── Barrel k (curvature strength 0-1) ─────────────────────────
  // k=0.15 subtle, k=0.3 visible CRT, k=0.5 strong fisheye
  // corner out-of-bounds ≈ scale × k px — match border-radius to hide
  setK(k) {
    window._CRT_K = k;
    const uri = window.buildBarrelMap(k);
    window._CRT_MAP = uri;
    const el = document.getElementById('crt-map-img');
    if (el) {
      el.setAttribute('href', uri);
      el.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', uri);
    }
  },

  // ── Scale (pixel displacement magnitude) ──────────────────────
  setScale(px) {
    window._CRT_SCALE = px;
    const el = document.getElementById('crt-displace');
    if (el) el.setAttribute('scale', px);
  },

  toggleBarrel(on) {
    const el = document.querySelector('.crt-screen');
    if (el) el.style.filter = on ? 'url(#crt-barrel)' : 'none';
  },

  // ── Screen shape ───────────────────────────────────────────────
  setRadius(px) {
    document.documentElement.style.setProperty('--crt-radius', px + 'px');
  },

  // ── Scanlines ─────────────────────────────────────────────────
  setOverlayOpacity(val) {
    const el = document.querySelector('.crt-overlay');
    if (el) el.style.opacity = val;
  },

  setSweepSpeed(sec) {
    document.documentElement.style.setProperty('--sweep-speed', sec + 's');
  },

  toggleOverlay(on) {
    const el = document.querySelector('.crt-overlay');
    if (el) el.style.display = on ? '' : 'none';
  },

  toggleSweep(on) {
    const el = document.querySelector('.crt-scanline-sweep');
    if (el) el.style.display = on ? '' : 'none';
  },

  // ── Vignette ───────────────────────────────────────────────────
  setVignette(val) {
    document.documentElement.style.setProperty('--vignette-opacity', val);
  },
};

// ── Sync helpers ──────────────────────────────────────────────────
window.syncNum   = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
window.syncRange = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };

// ── Ctrl+D toggle ─────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'd') {
    e.preventDefault();
    document.getElementById('debug-panel')?.classList.toggle('visible');
  }
});

console.log('[CRT] Ready. Ctrl+D for controls.');
console.log(`[CRT] k=${window._CRT_K} scale=${window._CRT_SCALE}`);
console.log('[CRT] Barrel math: dx = nx*r²*k (outward = barrel). feFlood fills out-of-bounds with #0a0a0a.');
