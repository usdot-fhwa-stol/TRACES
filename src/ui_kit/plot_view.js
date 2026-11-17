/*
 * plot_view.js -- interactive canvas plots for HTML files saved by
 * plot/helpers/plot_export.py (build_plot_html). Self-contained, no libraries.
 *
 * Conventions follow pyqtgraph and the app's KinematicStackedPlotWidget:
 *   wheel over the plot area  zooms both axes about the mouse
 *   wheel over an axis        zooms only that axis
 *   drag                      pans (over an axis: only that axis)
 *   double-click              resets the view (the "A" button)
 *   legend entry click        shows/hides that series (linked series on
 *                             every plot that shares the link key)
 *   hover                     vertical cursor on every plot sharing the
 *                             time axis; near a curve, each of those plots
 *                             shows a label with its series' values;
 *                             over a box or outlier, its statistics
 *
 * Data comes from one <script type="application/json" id="plot-data">
 * block (see plot_export.py for the schema):
 *
 *   { title, columns, cellHeight,
 *     plots: [ { title, xLabel, yLabel, xUnit, yUnit, xName, showXAxis,
 *                grid, xLink, yFit, xRange, yRange, legend, cursor,
 *                items: [ curve | vline | region | box ] }
 *            | { spacer: true } ] }
 *
 *   curve  { type, name, x[], y[], pen, shadow, z, legend, visible, link }
 *   vline  { type, x, pen, z }
 *   region { type, x0, x1, brush, z }
 *   box    { type, x, q1, median, q3, lo, hi, outliers[{y, label}],
 *            boxWidth, whiskerWidth, pen, brush, z }
 *   pen    { color, width, dash: [px, px] | null }
 *
 * null in x/y arrays breaks the line, like connect="finite".
 */
(function () {
  "use strict";

  // ── Style (mirrors plot/plot_style.py) ──────────────────────────────

  const S = {
    background: "#ffffff",
    axisColor: "#000000",
    textColor: "#000000",
    font: "12px sans-serif",
    titleFont: "13px sans-serif",
    leftAxis: 72,            // fixed, like the widget's left_axis_width
    bottomAxis: 44,
    bottomAxisHidden: 12,
    titleHeight: 24,
    topPad: 10,
    rightPad: 16,
    tickLen: 5,
    xTickSpacing: 90,        // target pixels between ticks
    yTickSpacing: 42,
    pad: 0.03,               // auto-range padding (fraction of span)
    hoverPx: 12,             // cursor-to-curve distance that shows values
    outlierPx: 6,
    wheelBase: 1.0015,       // zoom factor per wheel delta unit
    cursorPen: { color: "#888888", width: 1, dash: [4, 4] },
    boxPen: { color: "#000000", width: 1, dash: null },
    boxBrush: "rgba(100,100,255,0.59)",
    medianPen: { color: "#ffffb4", width: 2, dash: null },
    whiskerPen: { color: "#000000", width: 1, dash: null },
  };

  // ── Helpers ─────────────────────────────────────────────────────────

  function toFloatArray(arr) {
    const out = new Float64Array(arr ? arr.length : 0);
    for (let i = 0; i < out.length; i++) {
      const v = arr[i];
      out[i] = (v === null || v === undefined) ? NaN : v;
    }
    return out;
  }

  function isSorted(x) {
    let prev = -Infinity;
    for (let i = 0; i < x.length; i++) {
      const v = x[i];
      if (Number.isNaN(v)) continue;
      if (v < prev) return false;
      prev = v;
    }
    return true;
  }

  // First index with x[i] >= v (x sorted, NaNs skipped conservatively).
  function lowerBound(x, v) {
    let lo = 0, hi = x.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      const xm = x[mid];
      if (Number.isNaN(xm) || xm < v) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  function niceTicks(lo, hi, pixels, spacing) {
    const span = hi - lo;
    if (!(span > 0) || !Number.isFinite(span)) return { values: [], decimals: 0 };
    const target = Math.max(2, Math.floor(pixels / spacing));
    const raw = span / target;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / mag;
    const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
    const first = Math.ceil(lo / step - 1e-9);
    const values = [];
    for (let k = first; k * step <= hi + step * 1e-9; k++) {
      const v = k * step;
      values.push(Math.abs(v) < step * 1e-9 ? 0 : v);
      if (values.length > 200) break;
    }
    const decimals = Math.min(10, Math.max(0, -Math.floor(Math.log10(step) + 1e-9)));
    return { values, decimals };
  }

  function formatTick(v, decimals) {
    const a = Math.abs(v);
    if (a !== 0 && (a >= 1e7 || a < 1e-6)) return v.toExponential(2);
    return v.toFixed(decimals);
  }

  function applyPen(ctx, pen) {
    ctx.strokeStyle = pen.color || "#000000";
    ctx.lineWidth = pen.width || 1;
    ctx.setLineDash(pen.dash || []);
    ctx.lineCap = pen.dash ? "butt" : "round";
    ctx.lineJoin = "round";
  }

  function padRange(lo, hi) {
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
    if (lo === hi) {
      const d = Math.abs(lo) > 0 ? Math.abs(lo) * 0.1 : 1;
      return [lo - d, hi + d];
    }
    const p = (hi - lo) * S.pad;
    return [lo - p, hi + p];
  }

  // ── One plot (a pyqtgraph PlotItem) ─────────────────────────────────

  class PlotView {
    constructor(spec, cell, page) {
      this.spec = spec;
      this.page = page;
      this.cell = cell;
      this.title = spec.title || "";
      this.showXAxis = spec.showXAxis !== false;
      this.yFit = spec.yFit === "all" ? "all" : "visible";

      // Spec order is kept for the legend and hover labels (like
      // pyqtgraph's legend: the order series were added); drawing uses
      // z order, with ties in spec order.
      const inOrder = (spec.items || []).map((it, i) => Object.assign({ _order: i }, it));
      this.items = inOrder.slice().sort((a, b) => ((a.z || 0) - (b.z || 0)) || (a._order - b._order));
      for (const it of inOrder) {
        if (it.type === "curve") {
          it.x = toFloatArray(it.x);
          it.y = toFloatArray(it.y);
          it.visible = it.visible !== false;
          it.legend = it.legend !== false;
          it.sorted = isSorted(it.x);
        }
      }
      this.curves = inOrder.filter((it) => it.type === "curve");
      this.boxes = inOrder.filter((it) => it.type === "box");
      this.cursorEnabled = spec.cursor !== false && this.curves.length > 0;
      this.cursorX = null;

      // DOM: canvas, legend, hover label
      this.canvas = document.createElement("canvas");
      this.ctx = this.canvas.getContext("2d");
      cell.appendChild(this.canvas);
      this.legendDiv = document.createElement("div");
      this.legendDiv.className = "pv-legend";
      this.legendDiv.title = "Click an entry to show or hide that series.";
      cell.appendChild(this.legendDiv);
      this.tip = document.createElement("div");
      this.tip.className = "pv-tip";
      cell.appendChild(this.tip);

      this.home = this.dataBounds();
      this.xr = spec.xRange ? spec.xRange.slice() : this.home.slice();
      this.autoY = !spec.yRange;
      this.yr = spec.yRange ? spec.yRange.slice() : this.fitY();
      this.homeY = spec.yRange ? spec.yRange.slice() : null;
      this.drag = null;

      this.buildLegend();
      this.bindEvents();
      new ResizeObserver(() => this.resize()).observe(cell);
    }

    // ── Ranges ────────────────────────────────────────────────────────

    dataBounds() {
      let lo = Infinity, hi = -Infinity;
      for (const c of this.curves) {
        for (let i = 0; i < c.x.length; i++) {
          if (Number.isNaN(c.y[i])) continue;
          const v = c.x[i];
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
      for (const b of this.boxes) {
        const h = Math.max(b.boxWidth || 0.5, b.whiskerWidth || 0.3) / 2;
        lo = Math.min(lo, b.x - h);
        hi = Math.max(hi, b.x + h);
      }
      return padRange(lo, hi);
    }

    fitY() {
      let lo = Infinity, hi = -Infinity;
      const [x0, x1] = this.xr;
      const visibleOnly = this.yFit === "visible";
      for (const c of this.curves) {
        if (!c.visible) continue;          // like pyqtgraph: hidden items don't count
        let i0 = 0, i1 = c.x.length;
        if (visibleOnly && c.sorted) {
          i0 = lowerBound(c.x, x0);
          i1 = lowerBound(c.x, x1 + 1e-12 * Math.abs(x1));
          i1 = Math.min(c.x.length, i1 + 1);
        }
        for (let i = i0; i < i1; i++) {
          const x = c.x[i], y = c.y[i];
          if (Number.isNaN(y) || Number.isNaN(x)) continue;
          if (visibleOnly && (x < x0 || x > x1)) continue;
          if (y < lo) lo = y;
          if (y > hi) hi = y;
        }
      }
      for (const b of this.boxes) {
        lo = Math.min(lo, b.lo, b.q1, b.q3);
        hi = Math.max(hi, b.hi, b.q1, b.q3);
        for (const o of b.outliers || []) {
          lo = Math.min(lo, o.y);
          hi = Math.max(hi, o.y);
        }
      }
      if (lo === Infinity) return this.yr ? this.yr.slice() : [0, 1];
      return padRange(lo, hi);
    }

    // ── Geometry ──────────────────────────────────────────────────────

    rect() {
      const w = this.cell.clientWidth, h = this.cell.clientHeight;
      const top = (this.title ? S.titleHeight : 0) + S.topPad;
      const bottom = h - (this.showXAxis ? S.bottomAxis : S.bottomAxisHidden);
      return { left: S.leftAxis, right: Math.max(S.leftAxis + 10, w - S.rightPad),
               top: top, bottom: Math.max(top + 10, bottom), w: w, h: h };
    }

    px(x, r) { return r.left + (x - this.xr[0]) / (this.xr[1] - this.xr[0]) * (r.right - r.left); }
    py(y, r) { return r.bottom - (y - this.yr[0]) / (this.yr[1] - this.yr[0]) * (r.bottom - r.top); }
    dx(px, r) { return this.xr[0] + (px - r.left) / (r.right - r.left) * (this.xr[1] - this.xr[0]); }
    dy(py, r) { return this.yr[0] + (r.bottom - py) / (r.bottom - r.top) * (this.yr[1] - this.yr[0]); }

    zone(mx, my, r) {
      if (mx >= r.left && mx <= r.right && my >= r.top && my <= r.bottom) return "plot";
      if (mx < r.left && my >= r.top && my <= r.bottom) return "y";
      if (this.showXAxis && my > r.bottom && mx >= r.left && mx <= r.right) return "x";
      return null;
    }

    resize() {
      const dpr = window.devicePixelRatio || 1;
      const w = this.cell.clientWidth, h = this.cell.clientHeight;
      this.canvas.width = Math.max(1, Math.round(w * dpr));
      this.canvas.height = Math.max(1, Math.round(h * dpr));
      this.canvas.style.width = w + "px";
      this.canvas.style.height = h + "px";
      this.draw();
    }

    // ── Drawing ───────────────────────────────────────────────────────

    draw() {
      const ctx = this.ctx;
      const dpr = window.devicePixelRatio || 1;
      const r = this.rect();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = S.background;
      ctx.fillRect(0, 0, r.w, r.h);

      const xt = niceTicks(this.xr[0], this.xr[1], r.right - r.left, S.xTickSpacing);
      const yt = niceTicks(this.yr[0], this.yr[1], r.bottom - r.top, S.yTickSpacing);

      // Title
      if (this.title) {
        ctx.fillStyle = S.textColor;
        ctx.font = S.titleFont;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(this.title, (r.left + r.right) / 2, 6, r.right - r.left);
      }

      // Grid
      const alpha = this.spec.grid;
      if (alpha) {
        ctx.save();
        ctx.strokeStyle = "rgba(0,0,0," + alpha + ")";
        ctx.lineWidth = 1;
        ctx.setLineDash([]);
        ctx.beginPath();
        if (this.showXAxis) {
          for (const v of xt.values) {
            const X = Math.round(this.px(v, r)) + 0.5;
            ctx.moveTo(X, r.top); ctx.lineTo(X, r.bottom);
          }
        }
        for (const v of yt.values) {
          const Y = Math.round(this.py(v, r)) + 0.5;
          ctx.moveTo(r.left, Y); ctx.lineTo(r.right, Y);
        }
        ctx.stroke();
        ctx.restore();
      }

      // Items, clipped to the data area, in z order
      ctx.save();
      ctx.beginPath();
      ctx.rect(r.left, r.top, r.right - r.left, r.bottom - r.top);
      ctx.clip();
      for (const it of this.items) {
        if (it.type === "curve") this.drawCurve(it, r);
        else if (it.type === "vline") this.drawVLine(it, r);
        else if (it.type === "region") this.drawRegion(it, r);
        else if (it.type === "box") this.drawBox(it, r);
      }
      if (this.cursorX !== null) {
        const X = Math.round(this.px(this.cursorX, r)) + 0.5;
        applyPen(ctx, S.cursorPen);
        ctx.beginPath(); ctx.moveTo(X, r.top); ctx.lineTo(X, r.bottom); ctx.stroke();
      }
      ctx.restore();

      // Axes
      ctx.save();
      applyPen(ctx, { color: S.axisColor, width: 1, dash: null });
      ctx.lineCap = "butt";
      ctx.fillStyle = S.textColor;
      ctx.font = S.font;
      ctx.beginPath();
      const L = Math.round(r.left) - 0.5, B = Math.round(r.bottom) + 0.5;
      ctx.moveTo(L, r.top); ctx.lineTo(L, r.bottom);
      for (const v of yt.values) {
        const Y = Math.round(this.py(v, r)) + 0.5;
        ctx.moveTo(L - S.tickLen, Y); ctx.lineTo(L, Y);
      }
      if (this.showXAxis) {
        ctx.moveTo(r.left, B); ctx.lineTo(r.right, B);
        for (const v of xt.values) {
          const X = Math.round(this.px(v, r)) + 0.5;
          ctx.moveTo(X, B); ctx.lineTo(X, B + S.tickLen);
        }
      }
      ctx.stroke();

      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      for (const v of yt.values) {
        ctx.fillText(formatTick(v, yt.decimals), L - S.tickLen - 3, this.py(v, r));
      }
      if (this.showXAxis) {
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        for (const v of xt.values) {
          ctx.fillText(formatTick(v, xt.decimals), this.px(v, r), B + S.tickLen + 2);
        }
        if (this.spec.xLabel) {
          ctx.fillText(this.spec.xLabel, (r.left + r.right) / 2, B + S.tickLen + 18);
        }
      }
      if (this.spec.yLabel) {
        ctx.translate(13, (r.top + r.bottom) / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(this.spec.yLabel, 0, 0);
      }
      ctx.restore();

      this.placeLegend(r);
    }

    curvePath(c, r) {
      const path = new Path2D();
      let i0 = 0, i1 = c.x.length;
      if (c.sorted && i1 > 0) {          // only the points near the view
        i0 = Math.max(0, lowerBound(c.x, this.xr[0]) - 1);
        i1 = Math.min(c.x.length, lowerBound(c.x, this.xr[1]) + 1);
      }
      let penDown = false;
      for (let i = i0; i < i1; i++) {
        const x = c.x[i], y = c.y[i];
        if (Number.isNaN(x) || Number.isNaN(y)) { penDown = false; continue; }
        const X = this.px(x, r), Y = this.py(y, r);
        if (penDown) path.lineTo(X, Y); else { path.moveTo(X, Y); penDown = true; }
      }
      return path;
    }

    drawCurve(c, r) {
      if (!c.visible) return;
      const ctx = this.ctx;
      const path = this.curvePath(c, r);
      if (c.shadow) { applyPen(ctx, c.shadow); ctx.stroke(path); }
      applyPen(ctx, c.pen || {});
      ctx.stroke(path);
    }

    drawVLine(it, r) {
      const X = this.px(it.x, r);
      applyPen(this.ctx, it.pen || {});
      this.ctx.beginPath();
      this.ctx.moveTo(X, r.top); this.ctx.lineTo(X, r.bottom);
      this.ctx.stroke();
    }

    drawRegion(it, r) {
      const a = this.px(it.x0, r), b = this.px(it.x1, r);
      this.ctx.fillStyle = it.brush || "rgba(200,200,200,0.3)";
      this.ctx.fillRect(Math.min(a, b), r.top, Math.abs(b - a), r.bottom - r.top);
    }

    drawBox(b, r) {
      const ctx = this.ctx;
      const hb = (b.boxWidth || 0.5) / 2, hw = (b.whiskerWidth || 0.3) / 2;
      const line = (x0, y0, x1, y1) => {
        ctx.beginPath();
        ctx.moveTo(this.px(x0, r), this.py(y0, r));
        ctx.lineTo(this.px(x1, r), this.py(y1, r));
        ctx.stroke();
      };
      if (b.q1 === b.q3 && b.q3 === b.median && b.median === b.lo && b.lo === b.hi) {
        applyPen(ctx, { color: "#000000", width: 2, dash: null });
        line(b.x - hb, b.median, b.x + hb, b.median);
      } else {
        const top = this.py(Math.max(b.q1, b.q3), r), bot = this.py(Math.min(b.q1, b.q3), r);
        const left = this.px(b.x - hb, r), right = this.px(b.x + hb, r);
        ctx.fillStyle = b.brush || S.boxBrush;
        ctx.fillRect(left, top, right - left, bot - top);
        applyPen(ctx, b.pen || S.boxPen);
        ctx.strokeRect(left, top, right - left, bot - top);
        applyPen(ctx, S.medianPen);
        line(b.x - hb, b.median, b.x + hb, b.median);
        applyPen(ctx, S.whiskerPen);
        line(b.x, b.q3, b.x, b.hi);
        line(b.x, b.q1, b.x, b.lo);
        line(b.x - hw, b.hi, b.x + hw, b.hi);
        line(b.x - hw, b.lo, b.x + hw, b.lo);
      }
      applyPen(ctx, { color: "#000000", width: 1, dash: null });
      for (const o of b.outliers || []) {
        ctx.beginPath();
        ctx.arc(this.px(b.x, r), this.py(o.y, r), 3, 0, 2 * Math.PI);
        ctx.stroke();
      }
    }

    // ── Legend ────────────────────────────────────────────────────────

    legendEntries() { return this.curves.filter((c) => c.legend); }

    buildLegend() {
      const entries = this.legendEntries();
      const show = this.spec.legend === true || (this.spec.legend !== false && entries.length >= 2);
      this.legendDiv.style.display = show ? "" : "none";
      this.legendDiv.innerHTML = "";
      if (!show) return;
      this.legendRows = [];
      for (const c of entries) {
        const row = document.createElement("div");
        row.className = "pv-entry";
        const sw = document.createElement("canvas");
        const dpr = window.devicePixelRatio || 1;
        sw.width = 30 * dpr; sw.height = 14 * dpr;
        sw.style.width = "30px"; sw.style.height = "14px";
        const g = sw.getContext("2d");
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (c.shadow) { applyPen(g, c.shadow); g.beginPath(); g.moveTo(2, 7); g.lineTo(28, 7); g.stroke(); }
        applyPen(g, c.pen || {});
        g.beginPath(); g.moveTo(2, 7); g.lineTo(28, 7); g.stroke();
        const label = document.createElement("span");
        label.textContent = c.name || "";
        row.appendChild(sw);
        row.appendChild(label);
        row.addEventListener("click", (e) => { e.stopPropagation(); this.page.toggle(this, c); });
        row.addEventListener("dblclick", (e) => e.stopPropagation());
        row.addEventListener("mousedown", (e) => e.stopPropagation());
        this.legendDiv.appendChild(row);
        this.legendRows.push({ curve: c, row: row });
      }
      this.refreshLegend();
    }

    refreshLegend() {
      for (const { curve, row } of this.legendRows || []) {
        row.classList.toggle("pv-hidden", !curve.visible);
      }
    }

    placeLegend(r) {
      this.legendDiv.style.left = (r.left + 10) + "px";
      this.legendDiv.style.top = (r.top + 10) + "px";
    }

    // ── Hover ─────────────────────────────────────────────────────────

    hoverCurves() { return this.curves.filter((c) => c.visible && c.legend); }

    // Time of the visible legend curve point nearest the mouse, if within
    // hoverPx screen pixels.
    nearestCurveX(mx, my, r) {
      let best = S.hoverPx, bestX = null;
      const xTol = S.hoverPx * (this.xr[1] - this.xr[0]) / (r.right - r.left);
      const m = this.dx(mx, r);
      for (const c of this.hoverCurves()) {
        let i0 = 0, i1 = c.x.length;
        if (c.sorted) {
          i0 = lowerBound(c.x, m - xTol);
          i1 = Math.min(c.x.length, lowerBound(c.x, m + xTol) + 1);
        }
        for (let i = i0; i < i1; i++) {
          const x = c.x[i], y = c.y[i];
          if (Number.isNaN(x) || Number.isNaN(y)) continue;
          const d = Math.hypot(this.px(x, r) - mx, this.py(y, r) - my);
          if (d <= best) { best = d; bestX = x; }
        }
      }
      return bestX;
    }

    // Label with this plot's visible series values at hitX.
    showValues(hitX, xTol) {
      const r = this.rect();
      const lines = [(this.spec.xName || "x") + " = " + hitX.toFixed(3) +
                     (this.spec.xUnit ? " " + this.spec.xUnit : "")];
      const ys = [];
      for (const c of this.hoverCurves()) {
        let i0 = 0, i1 = c.x.length;
        if (c.sorted) {
          i0 = lowerBound(c.x, hitX - xTol);
          i1 = Math.min(c.x.length, lowerBound(c.x, hitX + xTol) + 1);
        }
        let bi = -1, bd = Infinity;
        for (let i = i0; i < i1; i++) {
          if (Number.isNaN(c.y[i])) continue;
          const d = Math.abs(c.x[i] - hitX);
          if (d < bd) { bd = d; bi = i; }
        }
        if (bi >= 0 && bd <= xTol) {
          lines.push((c.name || "") + ": " + c.y[bi].toFixed(4) +
                     (this.spec.yUnit ? " " + this.spec.yUnit : ""));
          ys.push(c.y[bi]);
        }
      }
      if (!ys.length) { this.hideTip(); return; }
      const meanY = ys.reduce((a, b) => a + b, 0) / ys.length;
      this.showTip(lines.join("\n"), this.px(hitX, r), this.py(meanY, r));
    }

    showTip(text, X, Y) {
      this.tip.textContent = text;
      this.tip.style.display = "block";
      const w = this.tip.offsetWidth, h = this.tip.offsetHeight;
      const cw = this.cell.clientWidth, ch = this.cell.clientHeight;
      const left = Math.max(0, Math.min(X + 10, cw - w));
      const top = Math.max(0, Math.min(Y - h - 8, ch - h));
      this.tip.style.left = left + "px";
      this.tip.style.top = top + "px";
    }

    hideTip() { this.tip.style.display = "none"; }

    boxHover(mx, my, r) {
      for (const b of this.boxes) {
        const bx = this.px(b.x, r);
        const outs = (b.outliers || []).filter(
          (o) => Math.hypot(bx - mx, this.py(o.y, r) - my) <= S.outlierPx);
        if (outs.length) {
          this.showTip(outs.map((o) => (o.label !== undefined && o.label !== null ? o.label + "  " : "") +
                                     "value: " + Number(o.y).toPrecision(4)).join("\n"), mx, my);
          return true;
        }
        const h = Math.max(b.boxWidth || 0.5, b.whiskerWidth || 0.3) / 2;
        const left = this.px(b.x - h, r), right = this.px(b.x + h, r);
        const top = this.py(b.hi, r), bot = this.py(b.lo, r);
        if (mx >= left && mx <= right && my >= Math.min(top, bot) - 3 && my <= Math.max(top, bot) + 3) {
          const f = (v) => Number(v).toPrecision(4);
          this.showTip("Upper: " + f(b.hi) + "\nQ3: " + f(b.q3) + "\nMedian: " + f(b.median) +
                       "\nQ1: " + f(b.q1) + "\nLower: " + f(b.lo), mx, my);
          return true;
        }
      }
      return false;
    }

    // ── Events ────────────────────────────────────────────────────────

    bindEvents() {
      const c = this.canvas;
      const pos = (e) => {
        const b = c.getBoundingClientRect();
        return [e.clientX - b.left, e.clientY - b.top];
      };

      c.addEventListener("wheel", (e) => {
        const [mx, my] = pos(e);
        const r = this.rect();
        const z = this.zone(mx, my, r);
        if (!z) return;
        e.preventDefault();
        const f = Math.pow(S.wheelBase, Math.max(-300, Math.min(300, e.deltaY)));
        if (z === "plot" || z === "x") {
          const cx = this.dx(mx, r);
          this.page.setX(this, [cx - (cx - this.xr[0]) * f, cx + (this.xr[1] - cx) * f]);
        }
        if (z === "plot" || z === "y") {
          const cy = this.dy(my, r);
          this.autoY = false;
          this.yr = [cy - (cy - this.yr[0]) * f, cy + (this.yr[1] - cy) * f];
          this.draw();
        }
      }, { passive: false });

      c.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        const [mx, my] = pos(e);
        const r = this.rect();
        const z = this.zone(mx, my, r);
        if (!z) return;
        e.preventDefault();
        this.drag = { mx: mx, my: my, xr: this.xr.slice(), yr: this.yr.slice(), zone: z };
        this.page.clearCursor(this);
        const move = (ev) => {
          const [x, y] = pos(ev);
          const d = this.drag;
          if (d.zone !== "y") {
            const sx = (x - d.mx) * (d.xr[1] - d.xr[0]) / (r.right - r.left);
            this.page.setX(this, [d.xr[0] - sx, d.xr[1] - sx]);
          }
          if (d.zone !== "x") {
            const sy = (y - d.my) * (d.yr[1] - d.yr[0]) / (r.bottom - r.top);
            this.autoY = false;
            this.yr = [d.yr[0] + sy, d.yr[1] + sy];
            this.draw();
          }
        };
        const up = () => {
          window.removeEventListener("mousemove", move);
          window.removeEventListener("mouseup", up);
          this.drag = null;
        };
        window.addEventListener("mousemove", move);
        window.addEventListener("mouseup", up);
      });

      c.addEventListener("dblclick", (e) => { e.preventDefault(); this.page.reset(this); });

      c.addEventListener("mousemove", (e) => {
        if (this.drag) return;
        const [mx, my] = pos(e);
        const r = this.rect();
        if (this.zone(mx, my, r) !== "plot") { this.page.clearCursor(this); return; }
        if (this.boxes.length && this.boxHover(mx, my, r)) return;
        this.hideTip();
        if (!this.cursorEnabled) return;
        this.page.setCursor(this, this.dx(mx, r));
        const hitX = this.nearestCurveX(mx, my, r);
        const xTol = S.hoverPx * (this.xr[1] - this.xr[0]) / (r.right - r.left);
        this.page.showValues(this, hitX, xTol);
      });

      c.addEventListener("mouseleave", () => { if (!this.drag) this.page.clearCursor(this); });
    }
  }

  // ── Page: plots, linked time axes, linked series ─────────────────────

  class Page {
    constructor(data, root) {
      this.views = [];
      this.groups = new Map();       // xLink key -> [PlotView]
      root.style.gridTemplateColumns = "repeat(" + (data.columns || 1) + ", minmax(0, 1fr))";
      const height = (data.cellHeight || 320) + "px";
      for (const spec of data.plots || []) {
        const cell = document.createElement("div");
        cell.className = spec.spacer ? "pv-spacer" : "pv-cell";
        cell.style.height = height;
        root.appendChild(cell);
        if (spec.spacer) continue;
        const view = new PlotView(spec, cell, this);
        this.views.push(view);
        if (spec.xLink) {
          if (!this.groups.has(spec.xLink)) this.groups.set(spec.xLink, []);
          this.groups.get(spec.xLink).push(view);
        }
      }
      // A linked group starts on one shared time range: the first range
      // a plot asked for, otherwise the union of the group's data.
      for (const group of this.groups.values()) {
        const asked = group.find((v) => v.spec.xRange);
        const xr = asked ? asked.spec.xRange.slice() : this.unionHome(group);
        for (const v of group) {
          v.xr = xr.slice();
          if (v.autoY) v.yr = v.fitY();
        }
      }
    }

    group(view) {
      return view.spec.xLink ? this.groups.get(view.spec.xLink) : [view];
    }

    unionHome(group) {
      let lo = Infinity, hi = -Infinity;
      for (const v of group) { lo = Math.min(lo, v.home[0]); hi = Math.max(hi, v.home[1]); }
      return [lo, hi];
    }

    setX(view, xr) {
      if (!(xr[1] - xr[0] > 1e-12)) return;
      for (const v of this.group(view)) {
        v.xr = xr.slice();
        if (v.autoY) v.yr = v.fitY();
        v.draw();
      }
    }

    reset(view) {
      const group = this.group(view);
      const xr = this.unionHome(group);
      for (const v of group) {
        v.xr = xr.slice();
        v.autoY = !v.homeY;
        v.yr = v.homeY ? v.homeY.slice() : v.fitY();
        v.draw();
      }
    }

    setCursor(view, x) {
      for (const v of this.group(view)) {
        if (!v.cursorEnabled) continue;
        v.cursorX = x;
        v.draw();
      }
    }

    clearCursor(view) {
      for (const v of this.group(view)) {
        v.hideTip();
        if (v.cursorX !== null) { v.cursorX = null; v.draw(); }
      }
    }

    showValues(view, hitX, xTol) {
      for (const v of this.group(view)) {
        if (hitX === null) v.hideTip(); else v.showValues(hitX, xTol);
      }
    }

    // Legend click: a linked series toggles on every plot sharing its
    // link key; an unlinked one only on its own plot.
    toggle(view, curve) {
      const visible = !curve.visible;
      const touched = new Set([view]);
      curve.visible = visible;
      if (curve.link) {
        for (const v of this.views) {
          for (const c of v.curves) {
            if (c.link === curve.link) { c.visible = visible; touched.add(v); }
          }
        }
      }
      for (const v of touched) {
        v.refreshLegend();
        v.hideTip();
        if (v.autoY) v.yr = v.fitY();
        v.draw();
      }
    }
  }

  function start() {
    const dataEl = document.getElementById("plot-data");
    const root = document.getElementById("plots");
    if (!dataEl || !root) return;
    new Page(JSON.parse(dataEl.textContent), root);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
