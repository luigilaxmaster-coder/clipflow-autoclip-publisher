/* AutoClip Publisher V2 — Motor de Gráficos Interactivos SVG Local (Sin CDN) */
(function () {
  "use strict";

  const PALETTE = [
    "#6366f1", // Indigo
    "#8b5cf6", // Violet
    "#06b6d4", // Cyan
    "#10b981", // Emerald
    "#f59e0b", // Amber
    "#f43f5e", // Rose
    "#38bdf8", // Sky
  ];

  /**
   * Renderiza un gráfico de barras/área limpio con ejes y tooltips nativos SVG.
   */
  window.renderBarChart = function (containerId, labels, values, emptyText) {
    const el = document.getElementById(containerId);
    if (!el) return;

    if (!labels || labels.length === 0 || values.every((v) => !v)) {
      el.innerHTML = `
        <div class="empty-state" style="padding: 28px 16px;">
          <p class="text-muted">${emptyText || "Sin actividad registrada en el rango seleccionado."}</p>
        </div>`;
      return;
    }

    const width = Math.max(el.clientWidth || 520, 320);
    const height = 210;
    const padL = 36, padR = 16, padT = 16, padB = 34;
    const plotW = width - padL - padR;
    const plotH = height - padT - padB;
    const maxVal = Math.max(...values, 1);

    const barSlot = plotW / labels.length;
    const barW = Math.min(36, Math.max(10, barSlot * 0.56));

    let gridSvg = "";
    for (let i = 0; i <= 4; i++) {
      const y = padT + (plotH * i) / 4;
      const tickVal = Math.round(maxVal - (maxVal * i) / 4);
      gridSvg += `<line x1="${padL}" y1="${y}" x2="${width - padR}" y2="${y}" stroke="rgba(148,163,184,0.12)" stroke-dasharray="3 3" />`;
      gridSvg += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end" fill="#94a3b8" font-size="11">${tickVal}</text>`;
    }

    let barsSvg = "";
    labels.forEach((lbl, idx) => {
      const val = values[idx] || 0;
      const h = (val / maxVal) * plotH;
      const x = padL + idx * barSlot + (barSlot - barW) / 2;
      const y = padT + plotH - h;
      const shortLbl = lbl.length > 5 ? lbl.slice(5) : lbl;
      barsSvg += `
        <g>
          <rect x="${x}" y="${y}" width="${barW}" height="${Math.max(h, 2)}" rx="5" fill="url(#indigoBarGrad)">
            <title>${lbl}: ${val}</title>
          </rect>
          <text x="${x + barW / 2}" y="${height - 10}" text-anchor="middle" fill="#94a3b8" font-size="10.5">${shortLbl}</text>
        </g>`;
    });

    el.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="Gráfico temporal">
        <defs>
          <linearGradient id="indigoBarGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#818cf8"/>
            <stop offset="100%" stop-color="#6366f1"/>
          </linearGradient>
        </defs>
        ${gridSvg}
        ${barsSvg}
      </svg>`;
  };

  /**
   * Renderiza un gráfico Donut SVG con leyenda clara por plataforma.
   */
  window.renderDonutChart = function (containerId, entries, emptyText) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const clean = (entries || []).filter((e) => e.value > 0);
    const total = clean.reduce((acc, e) => acc + e.value, 0);

    if (total === 0) {
      el.innerHTML = `
        <div class="empty-state" style="padding: 28px 16px;">
          <p class="text-muted">${emptyText || "Sin datos por plataforma aún."}</p>
        </div>`;
      return;
    }

    const size = 160;
    const cx = 80, cy = 80, r = 56, strokeW = 18;
    const circ = 2 * Math.PI * r;
    let offset = 0;

    let slices = "";
    let legend = "";

    clean.forEach((item, idx) => {
      const color = PALETTE[idx % PALETTE.length];
      const frac = item.value / total;
      const dash = frac * circ;
      slices += `
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="${strokeW}"
          stroke-dasharray="${dash} ${circ - dash}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})">
          <title>${item.label}: ${item.value} (${Math.round(frac * 100)}%)</title>
        </circle>`;
      offset += dash;

      legend += `
        <div class="flex items-center justify-between text-sm" style="gap: 10px;">
          <span class="flex items-center gap-2">
            <span style="width:10px;height:10px;border-radius:3px;background:${color};display:inline-block;"></span>
            <span style="text-transform:capitalize;color:#e2e8f0;">${item.label}</span>
          </span>
          <span class="font-mono text-muted">${item.value} (${Math.round(frac * 100)}%)</span>
        </div>`;
    });

    el.innerHTML = `
      <div class="flex items-center flex-wrap gap-4" style="justify-content:center;padding:8px 0;">
        <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="Distribución por plataforma">
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(148,163,184,0.12)" stroke-width="${strokeW}" />
          ${slices}
          <text x="${cx}" y="${cy - 2}" text-anchor="middle" fill="#f8fafc" font-size="22" font-weight="800">${total}</text>
          <text x="${cx}" y="${cy + 15}" text-anchor="middle" fill="#94a3b8" font-size="11">Total</text>
        </svg>
        <div class="flex-col gap-2" style="min-width:160px;flex:1;">
          ${legend}
        </div>
      </div>`;
  };
})();
