/* ── CRT Debug Controller ──────────────────────────────────────── */
/* Toggle with Ctrl+D. Adjusts all CRT effect parameters live.     */

const crtDebug = {
  // DOM refs
  get screen()  { return document.querySelector('.crt-screen'); },
  get monitor() { return document.querySelector('.crt-monitor'); },
  get overlay() { return document.querySelector('.crt-overlay'); },
  get sweep()   { return document.querySelector('.crt-scanline-sweep'); },
  get glass()   { return document.querySelector('.crt-glass'); },
  get filter()  { return document.getElementById('crt-barrel'); },
  get displace(){ return document.getElementById('crt-displace'); },

  // ── Barrel Distortion ──────────────────────────────────────────

  setBarrelScale(val) {
    if (this.displace) {
      this.displace.setAttribute('scale', val);
    }
  },

  setFilterAttr(attr, val) {
    if (this.filter) {
      this.filter.setAttribute(attr, val);
    }
  },

  toggleBarrel(on) {
    if (this.screen) {
      this.screen.style.filter = on ? 'url(#crt-barrel)' : 'none';
    }
  },

  // ── Screen Shape ───────────────────────────────────────────────

  setRadius(px) {
    document.documentElement.style.setProperty('--crt-radius', px + 'px');
  },

  // ── Chromatic Aberration ───────────────────────────────────────

  setAberrationSpeed(sec) {
    document.documentElement.style.setProperty('--aberration-speed', sec + 's');
  },

  toggleAberration(on) {
    if (this.screen) {
      this.screen.style.animationName = on ? 'crt-aberration' : 'none';
    }
  },

  // ── Scanlines / Overlay ────────────────────────────────────────

  setOverlayOpacity(val) {
    if (this.overlay) {
      this.overlay.style.opacity = val;
    }
  },

  setSweepSpeed(sec) {
    document.documentElement.style.setProperty('--sweep-speed', sec + 's');
  },

  toggleOverlay(on) {
    if (this.overlay) {
      this.overlay.style.display = on ? 'block' : 'none';
    }
  },

  toggleSweep(on) {
    if (this.sweep) {
      this.sweep.style.display = on ? 'block' : 'none';
    }
  },

  // ── Vignette ───────────────────────────────────────────────────

  setVignette(val) {
    document.documentElement.style.setProperty('--vignette-opacity', val);
  },

  // ── Glass Reflection ───────────────────────────────────────────

  toggleGlass(on) {
    if (this.glass) {
      this.glass.style.display = on ? 'block' : 'none';
    }
  },
};

// ── Keyboard shortcut: Ctrl+D ────────────────────────────────────

document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key === 'd') {
    e.preventDefault();
    const panel = document.getElementById('debug-panel');
    if (panel) {
      panel.classList.toggle('visible');
    }
  }
});

// Log available controls
console.log('[CRT Debug] Press Ctrl+D to open CRT controls panel');
console.log('[CRT Debug] Parameters: barrel scale/filter, radius, aberration speed, overlay opacity, sweep speed, vignette, glass');
