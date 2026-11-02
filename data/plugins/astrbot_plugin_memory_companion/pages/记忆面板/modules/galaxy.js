
// galaxy: one page runtime owns shared state and DOM references.
export function createGalaxy(app) {
function satelliteColor(memory) {
  const source = app.common.compact(memory.source_plugin);
  if (source && source !== "self" && source !== "astrbot_plugin_memory_companion") return "#7dd3fc";
  return app.values.TYPE_COLORS[app.common.compact(memory.memory_type)] || "#a99bff";
}

function typeLabel(memory) {
  const type = app.common.compact(memory.memory_type);
  return app.values.TYPE_LABEL[type] || type || "记忆";
}

function memoryTimestamp(memory) {
  const value = app.common.compact(memory.occurred_at) || app.common.compact(memory.created_at) || app.common.compact(memory.updated_at);
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function relationScore(center, other) {
  if (!center || !other || center.id === other.id) return 0;
  let score = 0;
  const centerTags = new Set((Array.isArray(center.tags) ? center.tags : []).map((t) => app.common.compact(t)).filter(Boolean));
  const otherTags = Array.isArray(other.tags) ? other.tags : [];
  const sharedTags = otherTags.filter((tag) => centerTags.has(app.common.compact(tag)));
  score += Math.min(3, sharedTags.length) * 0.22;

  const centerSubject = app.common.compact(center.subject && center.subject.id);
  const otherSubject = app.common.compact(other.subject && other.subject.id);
  if (centerSubject && centerSubject === otherSubject) score += 0.3;

  const centerSession = app.common.compact(center.session_id);
  if (centerSession && centerSession === app.common.compact(other.session_id)) score += 0.2;
  const centerGroup = app.common.compact(center.group_id);
  if (centerGroup && centerGroup === app.common.compact(other.group_id)) score += 0.16;

  if (app.common.compact(center.scope) === app.common.compact(other.scope)) score += 0.1;
  if (app.common.compact(center.memory_type) === app.common.compact(other.memory_type)) score += 0.08;

  const batch = app.common.compact(center.import_batch_id);
  if (batch && batch === app.common.compact(other.import_batch_id)) score += 0.18;

  const ct = memoryTimestamp(center);
  const ot = memoryTimestamp(other);
  if (ct && ot) {
    const days = Math.abs(ct - ot) / 86400000;
    if (days <= 1) score += 0.16;
    else if (days <= 7) score += 0.1;
    else if (days <= 30) score += 0.05;
  }
  return app.common.clamp(score, 0, 1);
}

function buildGalaxy(memories, centerId) {
  const pool = (memories || []).filter((m) => app.common.compact(m.id));
  if (!pool.length) return null;
  let center = centerId ? pool.find((m) => m.id === centerId) : null;
  if (!center) {
    center = pool.slice().sort((a, b) => {
      const diff = Number(b.importance || 0) - Number(a.importance || 0);
      if (Math.abs(diff) > 0.0001) return diff;
      return memoryTimestamp(b) - memoryTimestamp(a);
    })[0];
  }
  const scored = pool
    .filter((m) => m.id !== center.id)
    .map((memory) => ({ memory, score: relationScore(center, memory) }))
    .filter((item) => item.score > 0.08)
    .sort((a, b) => b.score - a.score || Number(b.memory.importance || 0) - Number(a.memory.importance || 0))
    .slice(0, 12);

  const nodes = scored.map((item, index) => {
    const tier = item.score >= 0.62 ? 0 : item.score >= 0.36 ? 1 : 2;
    const count = scored.filter((s, i) => (s.score >= 0.62 ? 0 : s.score >= 0.36 ? 1 : 2) === tier).length;
    return {
      id: item.memory.id,
      memory: item.memory,
      score: item.score,
      tier,
      color: satelliteColor(item.memory),
      angle: (index / Math.max(1, scored.length)) * Math.PI * 2 + tier * 0.7,
      size: 4.4 + Number(item.memory.importance || 0) * 5.2 + item.score * 2.4,
      slotIndex: index,
      tierCount: count,
    };
  });
  return { center, nodes, pool };
}

function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

class GalaxyView {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.w = 0;
    this.h = 0;
    this.galaxy = null;
    this.pending = null;
    this.transition = null;
    this.hoverId = "";
    this.paused = false;
    this.destroyed = false;
    this.starfield = [];
    this.lastFrame = 0;
    this.tip = null;
    this.onSelect = null;
    this.onHover = null;
    this.resize();
    this.bindEvents();
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  destroy() {
    this.destroyed = true;
    window.removeEventListener("resize", this.resizeHandler);
    this.canvas.removeEventListener("mousemove", this.moveHandler);
    this.canvas.removeEventListener("mouseleave", this.leaveHandler);
    this.canvas.removeEventListener("click", this.clickHandler);
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    this.w = Math.max(320, Math.round(rect.width));
    this.h = Math.max(320, Math.round(rect.height || 620));
    this.canvas.width = Math.round(this.w * this.dpr);
    this.canvas.height = Math.round(this.h * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.buildStarfield();
  }

  buildStarfield() {
    const count = Math.round((this.w * this.h) / 5200);
    const stars = [];
    let seed = 20260829;
    const rand = () => {
      seed = (seed * 1664525 + 1013904223) % 4294967296;
      return seed / 4294967296;
    };
    for (let i = 0; i < count; i += 1) {
      stars.push({
        x: rand() * this.w,
        y: rand() * this.h,
        r: 0.35 + rand() * 0.95,
        a: 0.12 + rand() * 0.42,
        tw: rand() * Math.PI * 2,
      });
    }
    this.starfield = stars;
  }

  bindEvents() {
    this.resizeHandler = () => this.resize();
    window.addEventListener("resize", this.resizeHandler);

    this.moveHandler = (event) => {
      const rect = this.canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const found = this.hitTest(mx, my);
      const id = found ? found.id : "";
      if (id !== this.hoverId) {
        this.hoverId = id;
        this.canvas.style.cursor = id ? "pointer" : "default";
        if (this.onHover) this.onHover(found ? found.node : null, mx, my);
      } else if (id && this.onHover) {
        this.onHover(found.node, mx, my);
      }
    };
    this.leaveHandler = () => {
      this.hoverId = "";
      this.canvas.style.cursor = "default";
      if (this.onHover) this.onHover(null, 0, 0);
    };
    this.clickHandler = (event) => {
      const rect = this.canvas.getBoundingClientRect();
      const found = this.hitTest(event.clientX - rect.left, event.clientY - rect.top);
      if (found && this.onSelect) this.onSelect(found.node);
    };
    this.canvas.addEventListener("mousemove", this.moveHandler);
    this.canvas.addEventListener("mouseleave", this.leaveHandler);
    this.canvas.addEventListener("click", this.clickHandler);
  }

  setGalaxy(galaxy) {
    if (!galaxy) return;
    if (!this.galaxy || !this.galaxy.center || galaxy.center.id === this.galaxy.center.id) {
      const keepAngles = {};
      if (this.galaxy) this.galaxy.nodes.forEach((node) => (keepAngles[node.id] = node.angle));
      galaxy.nodes.forEach((node) => {
        if (keepAngles[node.id] !== undefined) node.angle = keepAngles[node.id];
        node.born = 1;
      });
      this.galaxy = galaxy;
      return;
    }
    const outgoing = this.galaxy;
    const incoming = galaxy;
    const clicked = outgoing.nodes.find((node) => node.id === galaxy.center.id);
    incoming.nodes.forEach((node) => {
      node.born = 0;
    });
    this.transition = {
      t: 0,
      outgoing,
      incoming,
      clicked: clicked || null,
      startX: clicked ? this.nodeX(clicked) : this.w / 2,
      startY: clicked ? this.nodeY(clicked) : this.h / 2,
      startSize: clicked ? clicked.size : 6,
      startColor: clicked ? clicked.color : "#a99bff",
    };
  }

  cx() {
    return this.w / 2;
  }
  cy() {
    return this.h / 2 - 6;
  }
  orbitRadius(tier) {
    const base = Math.min(this.w, this.h) * 0.5;
    return base * app.values.ORBIT_TIERS[tier].ratio;
  }
  nodeX(node) {
    const r = this.orbitRadius(node.tier);
    return this.cx() + Math.cos(node.angle) * r;
  }
  nodeY(node) {
    const r = this.orbitRadius(node.tier);
    return this.cy() + Math.sin(node.angle) * r * 0.7;
  }
  depthOf(angle) {
    return (Math.sin(angle) + 1) / 2;
  }

  hitTest(mx, my) {
    if (!this.galaxy) return null;
    const nodes = this.galaxy.nodes;
    for (let i = nodes.length - 1; i >= 0; i -= 1) {
      const node = nodes[i];
      if (node.born < 0.4) continue;
      const x = this.nodeX(node);
      const y = this.nodeY(node);
      const r = Math.max(11, node.size + 7);
      if ((mx - x) ** 2 + (my - y) ** 2 <= r * r) return { id: node.id, node };
    }
    const cx = this.cx();
    const cy = this.cy();
    if ((mx - cx) ** 2 + (my - cy) ** 2 <= 26 * 26) return null;
    return null;
  }

  loop(now) {
    if (this.destroyed) return;
    const dt = this.lastFrame ? Math.min(64, now - this.lastFrame) : 16;
    this.lastFrame = now;
    this.step(dt);
    this.draw(now);
    requestAnimationFrame(this.loop);
  }

  step(dt) {
    if (this.transition) {
      this.transition.t = Math.min(1, this.transition.t + dt / 1050);
      const e = easeInOutCubic(this.transition.t);
      this.transition.incoming.nodes.forEach((node, index) => {
        const delay = app.common.clamp((index / Math.max(1, this.transition.incoming.nodes.length)) * 0.5, 0, 0.5);
        node.born = app.common.clamp((e - delay) / (1 - delay), 0, 1);
      });
      if (this.transition.t >= 1) {
        this.transition.incoming.nodes.forEach((node) => (node.born = 1));
        this.galaxy = this.transition.incoming;
        this.transition = null;
        this.hoverId = "";
      }
      return;
    }
    if (this.paused || this.userPaused || !this.galaxy) return;
    const seconds = dt / 1000;
    this.galaxy.nodes.forEach((node) => {
      const dir = node.tier % 2 === 0 ? 1 : -1;
      node.angle += ((app.values.ORBIT_TIERS[node.tier].speed * Math.PI) / 180) * seconds * dir;
      if (node.angle > Math.PI * 4) node.angle -= Math.PI * 4;
      if (node.angle < -Math.PI * 4) node.angle += Math.PI * 4;
    });
  }

  draw(now) {
    const ctx = this.ctx;
    const isDark = document.documentElement.dataset.theme !== "light";
    ctx.clearRect(0, 0, this.w, this.h);

    const bg = ctx.createLinearGradient(0, 0, 0, this.h);
    if (isDark) {
      bg.addColorStop(0, "#05070d");
      bg.addColorStop(0.55, "#080b14");
      bg.addColorStop(1, "#05070d");
    } else {
      bg.addColorStop(0, "#eef2f9");
      bg.addColorStop(0.55, "#e6ecf7");
      bg.addColorStop(1, "#eef2f9");
    }
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, this.w, this.h);

    this.drawStarfield(now, isDark);
    this.drawDisk(isDark);
    this.drawOrbits(isDark);
    this.drawTransition(now, isDark);
    if (!this.transition) this.drawSystem(this.galaxy, now, isDark);
  }

  drawStarfield(now, isDark) {
    const ctx = this.ctx;
    const time = now / 1000;
    this.starfield.forEach((star) => {
      const tw = 0.65 + 0.35 * Math.sin(time * 0.7 + star.tw);
      ctx.globalAlpha = star.a * tw;
      ctx.fillStyle = isDark ? "#dfe6f5" : "#7d8aa5";
      ctx.beginPath();
      ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.globalAlpha = 1;
  }

  drawDisk(isDark) {
    const ctx = this.ctx;
    const r = this.orbitRadius(2) * 1.16;
    const gradient = ctx.createRadialGradient(this.cx(), this.cy(), r * 0.06, this.cx(), this.cy(), r);
    if (isDark) {
      gradient.addColorStop(0, "rgba(124,108,240,0.16)");
      gradient.addColorStop(0.42, "rgba(90,110,200,0.07)");
      gradient.addColorStop(1, "rgba(10,14,26,0)");
    } else {
      gradient.addColorStop(0, "rgba(124,108,240,0.14)");
      gradient.addColorStop(0.45, "rgba(120,140,210,0.07)");
      gradient.addColorStop(1, "rgba(230,236,247,0)");
    }
    ctx.save();
    ctx.translate(this.cx(), this.cy());
    ctx.scale(1, 0.7);
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.fillStyle = gradient;
    ctx.fill();
    ctx.restore();
  }

  drawOrbits(isDark) {
    const ctx = this.ctx;
    app.values.ORBIT_TIERS.forEach((tier, index) => {
      const rx = this.orbitRadius(index);
      const ry = rx * 0.7;
      ctx.save();
      ctx.translate(this.cx(), this.cy());
      ctx.beginPath();
      ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2);
      ctx.strokeStyle = isDark ? "rgba(255,255,255,0.055)" : "rgba(16,24,40,0.07)";
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.beginPath();
      ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI);
      ctx.strokeStyle = isDark ? "rgba(255,255,255,0.13)" : "rgba(16,24,40,0.14)";
      ctx.lineWidth = 1.1;
      ctx.stroke();
      ctx.restore();
    });
  }

  drawSystem(galaxy, now, isDark) {
    if (!galaxy) return;
    this.drawCenter(galaxy.center, this.cx(), this.cy(), 1, 1, isDark, now);
    const sorted = galaxy.nodes.slice().sort((a, b) => Math.sin(a.angle) - Math.sin(b.angle));
    sorted.forEach((node) => {
      const born = node.born === undefined ? 1 : node.born;
      if (born <= 0.01) return;
      this.drawSatellite(node, born, isDark, now);
    });
  }

  drawTransition(now, isDark) {
    const tr = this.transition;
    if (!tr) return;
    const e = easeInOutCubic(tr.t);

    tr.outgoing.nodes.forEach((node) => {
      if (tr.clicked && node.id === tr.clicked.id) return;
      const fade = 1 - app.common.clamp(tr.t / 0.55, 0, 1);
      const born = app.common.clamp(1 - fade * 0.92, 0.08, 1);
      this.drawSatellite(node, born, isDark, now, 1 - fade * 0.9);
    });

    const oldCenterFade = 1 - app.common.clamp(tr.t / 0.5, 0, 1);
    if (oldCenterFade > 0.01) {
      this.drawCenter(tr.outgoing.center, this.cx(), this.cy(), 1 - e * 0.45, oldCenterFade, isDark, now);
    }

    if (tr.clicked) {
      const x = tr.startX + (this.cx() - tr.startX) * e;
      const y = tr.startY + (this.cy() - tr.startY) * e;
      const scale = 1 + e * 1.5;
      ctx_save(this.ctx);
      this.drawMoving(x, y, tr.startSize * scale, tr.startColor, isDark);
      ctx_restore(this.ctx);
    }

    if (e > 0.45) {
      const appear = app.common.clamp((e - 0.45) / 0.55, 0, 1);
      this.drawCenter(tr.incoming.center, this.cx(), this.cy(), 0.6 + appear * 0.4, appear, isDark, now);
    }

    tr.incoming.nodes.forEach((node) => {
      if (node.born <= 0.01) return;
      this.drawSatellite(node, node.born, isDark, now);
    });
  }

  drawMoving(x, y, size, color, isDark) {
    const ctx = this.ctx;
    const glow = ctx.createRadialGradient(x, y, 0, x, y, size * 4.2);
    glow.addColorStop(0, hexAlpha(color, 0.5));
    glow.addColorStop(0.45, hexAlpha(color, 0.16));
    glow.addColorStop(1, hexAlpha(color, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(x, y, size * 4.2, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, size, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = isDark ? "rgba(255,255,255,0.9)" : "rgba(255,255,255,0.85)";
    ctx.beginPath();
    ctx.arc(x - size * 0.26, y - size * 0.3, size * 0.3, 0, Math.PI * 2);
    ctx.fill();
  }

  drawCenter(memory, x, y, scale, alpha, isDark, now) {
    const ctx = this.ctx;
    if (!memory) return;
    const pulse = 1 + Math.sin(now / 1400) * 0.045;
    const base = 13 * scale * pulse;
    const color = satelliteColor(memory);

    const halo = ctx.createRadialGradient(x, y, 0, x, y, base * 6);
    halo.addColorStop(0, hexAlpha(color, 0.34 * alpha));
    halo.addColorStop(0.3, hexAlpha(color, 0.13 * alpha));
    halo.addColorStop(1, hexAlpha(color, 0));
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(x, y, base * 6, 0, Math.PI * 2);
    ctx.fill();

    const core = ctx.createRadialGradient(x - base * 0.3, y - base * 0.34, base * 0.1, x, y, base);
    core.addColorStop(0, hexAlpha("#ffffff", 0.98 * alpha));
    core.addColorStop(0.42, hexAlpha(color, 0.95 * alpha));
    core.addColorStop(1, hexAlpha(color, 0.6 * alpha));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(x, y, base, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = hexAlpha(color, 0.5 * alpha);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(x, y, base * 1.72, 0, Math.PI * 2);
    ctx.stroke();

    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.fillStyle = isDark ? "rgba(242,245,250,0.9)" : "rgba(16,19,25,0.86)";
    ctx.font = '600 11px "Sarasa Gothic SC", "PingFang SC", system-ui, sans-serif';
    ctx.textAlign = "center";
    ctx.fillText(app.common.clip(app.records.memoryTitle(memory), 20), x, y + base * 1.72 + 16);
    ctx.font = '400 10px "JetBrains Mono", monospace';
    ctx.fillStyle = isDark ? "rgba(152,163,184,0.72)" : "rgba(85,96,122,0.78)";
    ctx.fillText(
      (app.values.TYPE_LABEL[app.common.compact(memory.memory_type)] || app.common.compact(memory.memory_type) || "记忆") +
        " · 重要 " + app.common.num(memory.importance),
      x,
      y + base * 1.72 + 30
    );
    ctx.restore();
  }

  drawSatellite(node, born, isDark, now, alphaOverride) {
    const ctx = this.ctx;
    const rx = this.orbitRadius(node.tier);
    const ry = rx * 0.7;
    const x = this.cx() + Math.cos(node.angle) * rx * (0.35 + 0.65 * born);
    const y = this.cy() + Math.sin(node.angle) * ry * (0.35 + 0.65 * born);
    const depth = this.depthOf(node.angle);
    const isHover = this.hoverId === node.id;
    const sizeScale = (0.74 + depth * 0.4) * (0.5 + 0.5 * born) * (isHover ? 1.42 : 1);
    const size = Math.max(2, node.size * sizeScale);
    const alpha = app.common.clamp((alphaOverride === undefined ? 0.48 + depth * 0.52 : alphaOverride) * born, 0, 1);

    const dir = node.tier % 2 === 0 ? 1 : -1;
    const rEff = (rx + ry) / 2;
    const step = (24 / Math.max(60, rEff)) * dir;
    const trailScale = [0.5, 0.37, 0.26, 0.17];
    const trailAlpha = [0.44, 0.3, 0.18, 0.09];
    for (let i = 0; i < 4; i += 1) {
      const ta = node.angle - step * (i + 1);
      const tx = this.cx() + Math.cos(ta) * rx * (0.35 + 0.65 * born);
      const ty = this.cy() + Math.sin(ta) * ry * (0.35 + 0.65 * born);
      const tDepth = this.depthOf(ta);
      ctx.globalAlpha = trailAlpha[i] * born * (0.55 + tDepth * 0.45);
      ctx.fillStyle = node.color;
      ctx.beginPath();
      ctx.arc(tx, ty, Math.max(0.8, size * trailScale[i]), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    const glowR = size * (isHover ? 5.4 : 3.6);
    const glow = ctx.createRadialGradient(x, y, 0, x, y, glowR);
    glow.addColorStop(0, hexAlpha(node.color, (isHover ? 0.55 : 0.34) * alpha));
    glow.addColorStop(1, hexAlpha(node.color, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(x, y, glowR, 0, Math.PI * 2);
    ctx.fill();

    ctx.globalAlpha = alpha;
    ctx.fillStyle = node.color;
    ctx.beginPath();
    ctx.arc(x, y, size, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = hexAlpha("#ffffff", 0.55 * alpha);
    ctx.beginPath();
    ctx.arc(x - size * 0.28, y - size * 0.32, size * 0.32, 0, Math.PI * 2);
    ctx.fill();

    if (isHover || born > 0.85) {
      ctx.globalAlpha = alpha * (isHover ? 1 : 0.62);
      ctx.strokeStyle = hexAlpha(node.color, isHover ? 0.9 : 0.4);
      ctx.lineWidth = isHover ? 1.4 : 1;
      ctx.beginPath();
      ctx.arc(x, y, size + (isHover ? 7 : 3.4), 0, Math.PI * 2);
      ctx.stroke();
    }

    if (isHover || born > 0.9) {
      ctx.globalAlpha = alpha;
      ctx.fillStyle = isDark ? "rgba(242,245,250,0.94)" : "rgba(16,19,25,0.9)";
      ctx.font = '500 10.5px "Sarasa Gothic SC", "PingFang SC", system-ui, sans-serif';
      ctx.textAlign = "center";
      ctx.fillText(app.common.clip(app.records.memoryTitle(node.memory), 16), x, y + size + 15);
    }
    ctx.globalAlpha = 1;
  }
}

function ctx_save(ctx) { ctx.save(); }

function ctx_restore(ctx) { ctx.restore(); }

function hexAlpha(hex, alpha) {
  const value = app.common.compact(hex).replace("#", "");
  const full = value.length === 3 ? value.split("").map((c) => c + c).join("") : value;
  const r = parseInt(full.slice(0, 2), 16) || 0;
  const g = parseInt(full.slice(2, 4), 16) || 0;
  const b = parseInt(full.slice(4, 6), 16) || 0;
  return "rgba(" + r + "," + g + "," + b + "," + app.common.clamp(alpha, 0, 1).toFixed(3) + ")";
}

async function initialize() {
app.values.ORBIT_TIERS = [
  { key: "inner", label: "内轨 · 强关联", ratio: 0.34, speed: 4.0, color: "#a99bff" },
  { key: "mid", label: "中轨 · 相关", ratio: 0.56, speed: 2.6, color: "#7c6cf0" },
  { key: "outer", label: "外轨 · 弱相关", ratio: 0.79, speed: 1.7, color: "#4c5a7a" },
];
app.values.TYPE_COLORS = {
  profile: "#f472b6",
  preference: "#f472b6",
  relationship: "#f472b6",
  fact: "#60a5fa",
  state: "#60a5fa",
  event: "#2dd4bf",
  action: "#2dd4bf",
  image_action: "#fb923c",
  schedule: "#fb923c",
  promise: "#fcd34d",
  emotion: "#f87171",
  thought: "#a99bff",
};
app.values.galaxyInstance = null;
app.navigation.defineView("starmap", {
  title: "知识星图",
  navLabel: "知识星图",
  eyebrow: "Insight · Star Map",
  hint: "选中的记忆是中央恒星，关联记忆按强度落在内 / 中 / 外三条星轨上；点击卫星即可升为新的恒星",
  async load(options) {
    const opts = options || {};
    const pool = await app.pool.loadPool();
    const centerId = opts.center || (app.values.state.starmap && app.values.state.starmap.centerId) || "";
    const galaxy = buildGalaxy(pool, centerId);
    return { galaxy, poolSize: pool.length };
  },
  render(data) {
    const galaxy = data.galaxy;
    if (!galaxy) {
      return app.widgets.emptyState("记忆库还是空的", "先产生一些记忆，星图才有可以围绕的恒星。");
    }
    const legend = app.values.ORBIT_TIERS.map(
      (tier) =>
        '<span class="legend-item" style="color:' + tier.color + '"><i></i><span style="color:var(--text-3)">' + app.common.esc(tier.label) + "</span></span>"
    ).join("");

    return (
      '<div class="starmap-wrap">' +
        '<div class="starmap-card" id="starmapCard">' +
          '<div class="starmap-hud"><span class="hud-title">知识星图</span>' +
            '<span class="hud-sub">' + app.common.fmtInt(data.poolSize) + " 条记忆参与关联计算 · 悬停暂停公转</span></div>" +
          '<div class="starmap-tools">' +
            '<button class="pill" type="button" id="starPauseBtn">暂停公转</button>' +
            '<button class="pill" type="button" id="starReseedBtn">换一颗恒星</button>' +
          "</div>" +
          '<canvas class="starmap-canvas" id="starmapCanvas"></canvas>' +
          '<div class="starmap-tip" id="starmapTip"></div>' +
          '<div class="star-legend">' + legend + "</div>" +
        "</div>" +
        '<div class="star-panel" id="starPanel">' + app.galaxyPanel.starPanelHtml(galaxy) + "</div>" +
      "</div>"
    );
  },
  mount(node, data) {
    const canvas = app.values.$("#starmapCanvas", node);
    if (!canvas) return;
    if (app.values.galaxyInstance) app.values.galaxyInstance.destroy();
    app.values.galaxyInstance = new GalaxyView(canvas);
    app.values.galaxyInstance.setGalaxy(data.galaxy);
    app.values.state.starmap = { centerId: data.galaxy.center.id };

    const tip = app.values.$("#starmapTip", node);

    app.values.galaxyInstance.onHover = (hoverNode, mx, my) => {
      app.values.galaxyInstance.paused = Boolean(hoverNode);
      if (!hoverNode) {
        tip.classList.remove("is-on");
        return;
      }
      const memory = hoverNode.memory;
      const weights = memory.persona_weights && typeof memory.persona_weights === "object" ? memory.persona_weights : {};
      const weightKeys = Object.keys(weights).slice(0, 4);
      tip.innerHTML =
        '<div class="tip-head">' +
          app.widgets.badge(typeLabel(memory), app.galaxyPanel.toneOfColor(hoverNode.color)) +
          app.widgets.badge("关联 " + app.common.num(hoverNode.score), "accent") +
        "</div>" +
        '<div class="tip-body">' + app.common.esc(app.common.clip(app.records.memoryTitle(memory), 62)) + "</div>" +
        (weightKeys.length
          ? '<div class="tip-weights">' + weightKeys.map((key) => app.widgets.badge((app.values.WEIGHT_LABEL[key] || key) + " " + app.common.num(weights[key]), "accent")).join("") + "</div>"
          : "") +
        '<div class="tip-hint">悬停暂停公转 · 点击升为恒星</div>';
      tip.style.left = app.common.clamp(mx, 130, app.values.galaxyInstance.w - 130) + "px";
      tip.style.top = my + "px";
      tip.classList.add("is-on");
    };

    app.values.galaxyInstance.onSelect = (selectedNode) => {
      if (!selectedNode) return;
      tip.classList.remove("is-on");
      app.navigation.go("starmap", { center: selectedNode.id });
    };

    const pauseBtn = app.values.$("#starPauseBtn", node);
    if (pauseBtn) {
      pauseBtn.addEventListener("click", () => {
        app.values.galaxyInstance.userPaused = !app.values.galaxyInstance.userPaused;
        if (app.values.galaxyInstance.userPaused) app.values.galaxyInstance.paused = true;
        pauseBtn.classList.toggle("is-active", app.values.galaxyInstance.userPaused);
        pauseBtn.textContent = app.values.galaxyInstance.userPaused ? "继续公转" : "暂停公转";
      });
    }
    const reseedBtn = app.values.$("#starReseedBtn", node);
    if (reseedBtn) {
      reseedBtn.addEventListener("click", () => {
        const pool = app.values.state.pool || [];
        if (!pool.length) return;
        const current = app.values.state.starmap ? app.values.state.starmap.centerId : "";
        const ranked = pool.slice().sort((a, b) => Number(b.importance || 0) - Number(a.importance || 0));
        const next = ranked.find((m) => m.id !== current && m.id !== (data.galaxy.center && data.galaxy.center.id));
        if (next) app.navigation.go("starmap", { center: next.id });
      });
    }

    app.values.$$("[data-star-open]", node).forEach((button) => {
      button.addEventListener("click", () => app.viewer.openMemory(button.dataset.starOpen));
    });
  },
});
}
return { satelliteColor, typeLabel, memoryTimestamp, relationScore, buildGalaxy, easeInOutCubic, GalaxyView, ctx_save, ctx_restore, hexAlpha, initialize };
}
