/* ── CRT Debug Controller ─────────────────────────────────────── */
/* Ctrl+D to toggle. All map generation done synchronously in      */
/* index.html <head> — this file only handles live tuning.         */

const crtDebug = {

  // ── Barrel: scale (intensity) ──────────────────────────────────
  setScale(val) {
    window._CRT_SCALE = val;
    const el = document.getElementById('crt-displace');
    if (el) el.setAttribute('scale', val);
  },

  // ── Barrel: curvature (shape of the bowl) ─────────────────────
  // Higher = stronger bubble in the middle, gentler fall-off
  // Lower  = effect mostly at the very edge, flat center
  setCurvature(val) {
    window._CRT_CURVE = val;
    const uri = window.buildBarrelMap(val);
    window._CRT_MAP = uri;
    const el = document.getElementById('crt-map-img');
    if (el) {
      el.setAttribute('href', uri);
      el.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', uri);
    }
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

// ── Ctrl+D toggle ─────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'd') {
    e.preventDefault();
    document.getElementById('debug-panel')?.classList.toggle('visible');
  }
});

console.log('[CRT] Barrel map loaded synchronously. Ctrl+D for controls.');
console.log('[CRT] Scale:', window._CRT_SCALE, '| Curvature:', window._CRT_CURVE);
