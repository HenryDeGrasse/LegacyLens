/* ── CRT Debug Controller ──────────────────────────────────────── */
/* Toggle with Ctrl+D. Adjusts all CRT effect parameters live.     */
/* Barrel distortion map generated procedurally via Canvas.         */

// ── Barrel Map Generator ─────────────────────────────────────────

/**
 * Generate a displacement map for barrel distortion.
 * 
 * How it works:
 *   - Each pixel's color encodes displacement: R = X offset, G = Y offset
 *   - 128 = no displacement (neutral)
 *   - <128 = push toward that axis's negative direction
 *   - >128 = push toward that axis's positive direction
 *   - For barrel distortion, pixels push OUTWARD from center
 *     (left edge pushes left, right edge pushes right, etc.)
 *   - The `power` param controls curvature (1=linear, 1.5=moderate, 2+=strong bulge)
 *
 * @param {number} w - Map width (128 is plenty for smooth distortion)
 * @param {number} h - Map height
 * @param {number} power - Curvature exponent (1=linear, 1.5=barrel, 2=strong)
 * @returns {string} Data URI of the displacement map PNG
 */
function generateBarrelMap(w, h, power) {
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  const imageData = ctx.createImageData(w, h);
  const d = imageData.data;

  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      // Normalized coordinates: -1 to +1
      const nx = (x / (w - 1)) * 2 - 1;
      const ny = (y / (h - 1)) * 2 - 1;

      // Distance from center (0 to ~1.414 at corners)
      const dist = Math.sqrt(nx * nx + ny * ny);

      // Barrel curve: outward push increases with distance^power
      // Clamp dist to 1.0 so corners don't over-saturate
      const strength = Math.pow(Math.min(dist, 1.0), power);

      // Direction: unit vector from center to this pixel
      // At center (0,0) this is 0 — no displacement
      const angle = Math.atan2(ny, nx);
      const dx = Math.cos(angle) * strength;
      const dy = Math.sin(angle) * strength;

      const i = (y * w + x) * 4;
      // NEGATED: feDisplacementMap samples source at (x + offset, y + offset).
      // To make center BALLOON OUT (barrel/bubble), edge pixels must sample
      // from INWARD — so we push displacement toward center (negate outward vector).
      d[i + 0] = Math.round(128 - dx * 127);   // R = X displacement (inverted)
      d[i + 1] = Math.round(128 - dy * 127);   // G = Y displacement (inverted)
      d[i + 2] = 128;                            // B = unused
      d[i + 3] = 255;                            // A = opaque
    }
  }

  ctx.putImageData(imageData, 0, 0);
  return canvas.toDataURL('image/png');
}

// ── Init barrel map ──────────────────────────────────────────────

let currentCurvature = 2.0;

function applyBarrelMap(power) {
  currentCurvature = power;
  const dataURI = generateBarrelMap(128, 128, power);
  const feImage = document.getElementById('crt-sphere-map');
  if (feImage) {
    feImage.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', dataURI);
  }
}

// Generate on page load
window.addEventListener('DOMContentLoaded', () => {
  applyBarrelMap(currentCurvature);
  console.log('[CRT] Barrel map generated (128x128, curvature=' + currentCurvature + ', INVERTED for bubble effect)');
});

// ── Debug Controls ───────────────────────────────────────────────

const crtDebug = {
  // DOM refs
  get screen()   { return document.querySelector('.crt-screen'); },
  get monitor()  { return document.querySelector('.crt-monitor'); },
  get overlay()  { return document.querySelector('.crt-overlay'); },
  get sweep()    { return document.querySelector('.crt-scanline-sweep'); },
  get glass()    { return document.querySelector('.crt-glass'); },
  get filter()   { return document.getElementById('crt-barrel'); },
  get displace() { return document.getElementById('crt-displace'); },

  // ── Barrel Distortion ──────────────────────────────────────────

  setBarrelScale(val) {
    if (this.displace) {
      this.displace.setAttribute('scale', val);
    }
  },

  regenerateMap(power) {
    applyBarrelMap(parseFloat(power));
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

console.log('[CRT Debug] Press Ctrl+D to open CRT controls panel');
