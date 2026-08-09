// ==========================================================================
// frost-gate.mjs - 冰雪擦除人机验证画布
//
// 登录前置的 proof-of-human 交互层：全屏冰霜 canvas，用户用指针擦除冰霜，
// 覆盖率达到 42% 即视为通过；停手后冰霜以 5%/s 自然回霜，回落到 36% 以下
// 重新上锁（自然回霜策略）。最终把 {coverage, durationMs, moves} 连同服务端
// 下发的 {id, nonce} 组成 proof 交给 /api/login 校验。
//
// 逻辑端口自 ref/ECS-Terminal-Web/public/login.js，保持擦除笔触、残雪、回霜
// 的视觉与阈值一致；改造为可创建/销毁的工厂（登录模板每次渲染新建、登录成功
// 或失败时销毁），并用 --frost-tint 适配 muse 的 dark/light/eyecare 三主题。
//
// 画布像素是命令式的（不进 Alpine 响应式），只通过 onUpdate 把 {coverage,
// verified} 回吐给 Alpine 更新进度条 / 按钮禁用态；is-passive 类由本模块直接
// 管，因为它依赖 verified && !drawing（验证后仍允许继续擦以对抗回霜）。
// ==========================================================================

// 阈值常量 - 与后端 backend/auth_challenge.py 的校验边界一一对应：
//   VERIFY_THRESHOLD(0.42) = REQUIRED_COVERAGE
//   REFROST_RELOCK_THRESHOLD(0.36) = 回霜重新上锁线（仅前端视觉用）
//   coverage/duration/moves 的下限必须满足后端 MIN_DURATION_MS=300 / MIN_MOVES=6
const VERIFY_THRESHOLD = 0.42;
const REFROST_RELOCK_THRESHOLD = 0.36;
const REFROST_INTERVAL_MS = 180;
const REFROST_COVERAGE_PER_SECOND = 0.05;
const REFROST_BLEND_PER_SECOND = 0.085;
const REFROST_HOLD_MS = 2400;
const INITIAL_FROST_ALPHA = 0.9;

function assetVersion() {
  const v = String(
    document.querySelector('meta[name="muselab-asset-version"]')?.content || ""
  );
  return v && !v.startsWith("__") ? v : "";
}

const FROST_SURFACE_SRC = "/static/assets/frost-surface.webp"
  + (assetVersion() ? `?v=${encodeURIComponent(assetVersion())}` : "");

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {{ onUpdate?: (s: {coverage: number, verified: boolean}) => void }} opts
 */
export function createFrostGate(canvas, opts = {}) {
  const onUpdate = opts.onUpdate || (() => {});
  const context = canvas.getContext("2d");

  // 冰霜贴图（webp）。加载完成前用渐变兜底；贴图就绪后若画布已就位则铺霜。
  const frostSurfaceImage = new Image();
  frostSurfaceImage.decoding = "async";
  let frostSurfaceLoaded = false;

  const state = {
    coverage: 0,
    drawing: false,
    erasedDistance: 0,
    lastPoint: null,
    maxCoverage: 0,
    moves: 0,
    pointerId: null,
    pointerMode: "",
    startedAt: 0,
    verified: false,
  };

  let frostTimer = 0;
  let lastRefrostAt = 0;
  let thawHoldUntil = 0;
  let tint = "transparent";
  // 上一帧上报值，仅当显示的百分比或 verified 翻转时才回调，避免高频刷新。
  let lastReported = { pct: -1, verified: null };
  // 已绑定的监听器，destroy 时逐个移除。
  const listeners = [];

  function on(el, type, handler, options) {
    el.addEventListener(type, handler, options);
    listeners.push({ el, type, handler, options });
  }

  function readTint() {
    // --frost-tint 在 :root / html[data-theme=...] 上按主题定义；dark 透明
    // （贴图本身即高对比白霜），light/eyecare 给一层冷蓝让白霜在浅底上可见。
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue("--frost-tint").trim();
    return v || "transparent";
  }

  function coverageTargetDistance() {
    return Math.max(
      1800,
      Math.min(canvas.clientWidth, canvas.clientHeight) * 4.2,
    );
  }

  function resizeFrostCanvas() {
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width));
    canvas.height = Math.max(1, Math.round(rect.height));
    context.setTransform(1, 0, 0, 1, 0, 0);
    tint = readTint();
    resetFrost();
  }

  function resetFrost() {
    cancelRefrostTimer();
    state.coverage = 0;
    state.drawing = false;
    state.erasedDistance = 0;
    state.lastPoint = null;
    state.maxCoverage = 0;
    state.moves = 0;
    state.pointerId = null;
    state.pointerMode = "";
    state.startedAt = 0;
    state.verified = false;
    lastRefrostAt = 0;
    thawHoldUntil = 0;
    drawFrost({ full: true });
    updateVerification();
  }

  function drawFrost({ full = false, alpha } = {}) {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;

    context.save();
    context.globalCompositeOperation = "source-over";
    if (full) context.clearRect(0, 0, width, height);

    if (frostSurfaceLoaded) {
      context.globalAlpha = alpha ?? (full ? INITIAL_FROST_ALPHA : 1);
      drawFrostSurface(context, width, height);
    } else if (full) {
      drawFrostFallback(width, height);
    }

    // 仅在整屏铺霜时叠加主题色调（source-atop 只作用于已有霜像素，擦除区
    // 仍透明）。回霜混合重绘不重复叠色，避免半透明 tint 多帧累积到不透明。
    if (full && tint && tint !== "transparent") {
      context.globalAlpha = 1;
      context.globalCompositeOperation = "source-atop";
      context.fillStyle = tint;
      context.fillRect(0, 0, width, height);
    }
    context.restore();
  }

  function drawFrostSurface(target, width, height) {
    const imageWidth = frostSurfaceImage.naturalWidth;
    const imageHeight = frostSurfaceImage.naturalHeight;
    const scale = Math.max(width / imageWidth, height / imageHeight);
    const sourceWidth = width / scale;
    const sourceHeight = height / scale;
    const sourceX = (imageWidth - sourceWidth) / 2;
    const sourceY = (imageHeight - sourceHeight) / 2;
    target.drawImage(
      frostSurfaceImage, sourceX, sourceY, sourceWidth, sourceHeight,
      0, 0, width, height,
    );
  }

  function drawFrostFallback(width, height) {
    const fallback = context.createRadialGradient(
      width * 0.5, height * 0.45, 0,
      width * 0.5, height * 0.45, Math.max(width, height) * 0.78,
    );
    fallback.addColorStop(0, "#9ebce8");
    fallback.addColorStop(0.5, "#c4dbf7");
    fallback.addColorStop(1, "#d9eaff");
    context.fillStyle = fallback;
    context.fillRect(0, 0, width, height);
  }

  function drawIrregularBlob(target, cx, cy, rx, ry, rotation) {
    const points = 13;
    const gradient = target.createRadialGradient(
      cx, cy, Math.min(rx, ry) * 0.08,
      cx, cy, Math.max(rx, ry),
    );
    gradient.addColorStop(0, "rgba(255, 255, 255, 0.82)");
    gradient.addColorStop(0.5, "rgba(233, 247, 255, 0.5)");
    gradient.addColorStop(1, "rgba(164, 207, 246, 0)");
    target.fillStyle = gradient;
    target.beginPath();
    for (let i = 0; i < points; i += 1) {
      const angle = rotation + (i / points) * Math.PI * 2;
      const roughness = randomBetween(0.62, 1.15);
      const x = cx + Math.cos(angle) * rx * roughness;
      const y = cy + Math.sin(angle) * ry * roughness;
      if (i === 0) target.moveTo(x, y);
      else target.lineTo(x, y);
    }
    target.closePath();
    target.fill();
  }

  function refrostStep() {
    const now = Date.now();
    const elapsedMs = lastRefrostAt ? Math.max(0, now - lastRefrostAt) : 0;
    lastRefrostAt = now;
    // 停手宽限期内 / 正在擦 / 无耗时 都不回霜：让用户有连贯的擦除体验，
    // 验证通过后也先冻住 REFROST_HOLD_MS 再开始回霜。
    if (now <= thawHoldUntil || state.drawing || !elapsedMs) return;
    if (state.coverage <= 0 && state.maxCoverage <= 0) return;

    const elapsedSeconds = elapsedMs / 1000;
    const previousCoverage = state.coverage;
    state.coverage = Math.max(
      0,
      previousCoverage - REFROST_COVERAGE_PER_SECOND * elapsedSeconds,
    );
    state.erasedDistance = state.coverage * coverageTargetDistance();

    if (state.coverage > 0) {
      drawFrost({ alpha: 1 - Math.exp(-REFROST_BLEND_PER_SECOND * elapsedSeconds) });
    } else {
      state.maxCoverage = 0;
      drawFrost({ full: true });
    }

    // 已验证后用更低的回锁线（0.36）：给用户一点余量，轻微回霜不立刻掉锁。
    state.verified = state.verified
      ? state.coverage >= REFROST_RELOCK_THRESHOLD
      : state.coverage >= VERIFY_THRESHOLD;
    updateVerification();
  }

  function scheduleRefrost() {
    if (frostTimer || (state.coverage <= 0 && state.maxCoverage <= 0)) return;
    frostTimer = window.setTimeout(() => {
      frostTimer = 0;
      refrostStep();
      scheduleRefrost();
    }, REFROST_INTERVAL_MS);
  }

  function cancelRefrostTimer() {
    if (!frostTimer) return;
    window.clearTimeout(frostTimer);
    frostTimer = 0;
  }

  function pointFromEvent(event) {
    const source = event.touches?.[0] || event;
    const rect = canvas.getBoundingClientRect();
    return { x: source.clientX - rect.left, y: source.clientY - rect.top };
  }

  function beginErase(event) {
    if (event.isPrimary === false || (event.button !== undefined && event.button !== 0)) return;
    event.preventDefault();
    if (event.type.startsWith("mouse") && state.pointerMode === "pointer") return;

    state.pointerMode = event.type.startsWith("pointer")
      ? "pointer"
      : event.type.startsWith("touch")
        ? "touch"
        : "mouse";
    if (event.pointerId !== undefined && canvas.setPointerCapture) {
      canvas.setPointerCapture(event.pointerId);
      state.pointerId = event.pointerId;
    }
    state.drawing = true;
    state.lastPoint = pointFromEvent(event);
    state.startedAt = state.startedAt || performance.now();
    thawHoldUntil = Date.now() + 1200;
    eraseAt(state.lastPoint, state.lastPoint);
  }

  function moveErase(event) {
    if (!state.drawing) return;
    if (event.type.startsWith("mouse") && state.pointerMode === "pointer") return;

    event.preventDefault();
    const point = pointFromEvent(event);
    const previousPoint = state.lastPoint || point;
    state.erasedDistance += Math.hypot(
      point.x - previousPoint.x,
      point.y - previousPoint.y,
    );
    eraseAt(previousPoint, point);
    state.lastPoint = point;
    state.moves += 1;
    thawHoldUntil = Date.now() + 1500;
    if (state.moves % 3 === 0) updateCoverage();
  }

  function endErase() {
    if (!state.drawing) return;
    state.drawing = false;
    state.lastPoint = null;
    state.pointerId = null;
    state.pointerMode = "";
    updateCoverage();
    thawHoldUntil = Date.now() + 1800;
    scheduleRefrost();
  }

  function eraseAt(from, to) {
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const distance = Math.max(1, Math.hypot(dx, dy));
    const angle = Math.atan2(dy, dx);
    const steps = Math.min(5, Math.max(1, Math.ceil(distance / 34)));
    const baseWidth = Math.max(
      76,
      Math.min(canvas.clientWidth, canvas.clientHeight) * 0.14,
    );

    context.save();
    context.globalCompositeOperation = "destination-out";
    context.lineCap = "round";
    context.lineJoin = "round";
    context.globalAlpha = 0.68;
    context.lineWidth = baseWidth * 0.54;
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.lineTo(to.x, to.y);
    context.stroke();

    for (let i = 0; i <= steps; i += 1) {
      const progress = i / steps;
      const x = from.x + dx * progress;
      const y = from.y + dy * progress;
      const stampCount = 4 + Math.floor(Math.random() * 3);
      for (let j = 0; j < stampCount; j += 1) {
        const scatterAngle = angle + randomBetween(-1.4, 1.4);
        const spread = baseWidth * randomBetween(0.03, 0.3);
        context.globalAlpha = randomBetween(0.45, 0.82);
        context.beginPath();
        context.ellipse(
          x + Math.cos(scatterAngle) * spread + randomBetween(-9, 9),
          y + Math.sin(scatterAngle) * spread + randomBetween(-7, 7),
          baseWidth * randomBetween(0.05, 0.17),
          baseWidth * randomBetween(0.025, 0.1),
          angle + randomBetween(-0.6, 0.6),
          0, Math.PI * 2,
        );
        context.fill();
      }
    }

    context.globalAlpha = 1;
    context.lineWidth = baseWidth * 0.2;
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.lineTo(to.x, to.y);
    context.stroke();
    context.restore();

    drawEraseResidue(from, to, angle, baseWidth, steps);
  }

  function drawEraseResidue(from, to, angle, baseWidth, steps) {
    const dx = to.x - from.x;
    const dy = to.y - from.y;

    context.save();
    context.globalCompositeOperation = "source-over";
    context.lineCap = "round";

    if (Math.hypot(dx, dy) > 1) {
      for (const side of [-1, 1]) {
        const offsetX = -Math.sin(angle) * baseWidth * 0.36 * side;
        const offsetY = Math.cos(angle) * baseWidth * 0.36 * side;
        context.globalAlpha = 0.17;
        context.strokeStyle = "rgba(249, 254, 255, 0.82)";
        context.lineWidth = Math.max(4, baseWidth * 0.045);
        context.beginPath();
        context.moveTo(from.x + offsetX, from.y + offsetY);
        context.lineTo(to.x + offsetX, to.y + offsetY);
        context.stroke();
      }
    }

    for (let i = 0; i <= steps; i += 1) {
      const progress = i / steps;
      const x = from.x + dx * progress;
      const y = from.y + dy * progress;

      for (let j = 0; j < 6; j += 1) {
        const dustAngle = Math.random() * Math.PI * 2;
        const spread = baseWidth * randomBetween(0.08, 0.44);
        const radius = randomBetween(0.35, 1.6);
        context.globalAlpha = randomBetween(0.24, 0.58);
        context.fillStyle = Math.random() > 0.2
          ? "rgba(251, 255, 255, 0.86)"
          : "rgba(70, 128, 196, 0.28)";
        context.beginPath();
        context.arc(
          x + Math.cos(dustAngle) * spread,
          y + Math.sin(dustAngle) * spread,
          radius, 0, Math.PI * 2,
        );
        context.fill();
      }

      for (const side of [-1, 1]) {
        const ridgeAngle = angle + side * Math.PI / 2 + randomBetween(-0.35, 0.35);
        const ridgeDistance = baseWidth * randomBetween(0.32, 0.56);
        const cx = x + Math.cos(ridgeAngle) * ridgeDistance + randomBetween(-8, 8);
        const cy = y + Math.sin(ridgeAngle) * ridgeDistance + randomBetween(-7, 7);

        context.globalAlpha = randomBetween(0.34, 0.62);
        drawIrregularBlob(
          context, cx, cy,
          randomBetween(10, 28), randomBetween(4, 12), ridgeAngle,
        );

        for (let j = 0; j < 8; j += 1) {
          const particleAngle = ridgeAngle + randomBetween(-0.9, 0.9);
          const particleSpread = randomBetween(0, baseWidth * 0.24);
          const radius = randomBetween(0.35, 1.45);
          context.globalAlpha = randomBetween(0.35, 0.78);
          context.fillStyle = "rgba(252, 255, 255, 0.84)";
          context.beginPath();
          context.arc(
            cx + Math.cos(particleAngle) * particleSpread,
            cy + Math.sin(particleAngle) * particleSpread,
            radius, 0, Math.PI * 2,
          );
          context.fill();
        }
      }
    }
    context.restore();
  }

  function updateCoverage() {
    state.coverage = Math.min(0.95, state.erasedDistance / coverageTargetDistance());
    state.maxCoverage = Math.max(state.maxCoverage, state.coverage);
    state.verified = state.coverage >= VERIFY_THRESHOLD;
    updateVerification();
  }

  function updateVerification() {
    // is-passive 由本模块直管：验证后且未在擦除时放开指针事件，让下层的
    // token 输入框可点；正在擦除时即使已验证也收回事件，保证回霜对抗可继续。
    canvas.classList.toggle("is-passive", state.verified && !state.drawing);

    const pct = Math.max(0, Math.min(100, Math.round(state.coverage * 100)));
    if (pct !== lastReported.pct || state.verified !== lastReported.verified) {
      lastReported = { pct, verified: state.verified };
      onUpdate({ coverage: state.coverage, verified: state.verified });
    }
  }

  // ---- 公共 API ----------------------------------------------------------

  function proof(challenge) {
    return {
      coverage: Number(Math.max(state.coverage, state.maxCoverage).toFixed(3)),
      durationMs: Math.round(performance.now() - state.startedAt),
      id: challenge?.id || "",
      moves: state.moves,
      nonce: challenge?.nonce || "",
    };
  }

  function destroy() {
    cancelRefrostTimer();
    for (const { el, type, handler, options } of listeners) {
      el.removeEventListener(type, handler, options);
    }
    listeners.length = 0;
  }

  // ---- 事件绑定 + 启动 ----------------------------------------------------

  if ("PointerEvent" in window) {
    on(canvas, "pointerdown", beginErase);
    on(canvas, "pointermove", moveErase);
    on(window, "pointerup", endErase);
    on(window, "pointercancel", endErase);
  } else {
    on(canvas, "mousedown", beginErase);
    on(canvas, "mousemove", moveErase);
    on(window, "mouseup", endErase);
    on(canvas, "touchstart", beginErase, { passive: false });
    on(canvas, "touchmove", moveErase, { passive: false });
    on(window, "touchend", endErase);
    on(window, "touchcancel", endErase);
  }
  on(window, "resize", resizeFrostCanvas);

  frostSurfaceImage.addEventListener("load", () => {
    frostSurfaceLoaded = true;
    if (canvas.width > 0 && !state.startedAt) resetFrost();
  });
  frostSurfaceImage.src = FROST_SURFACE_SRC;

  resizeFrostCanvas();
  window.addEventListener("beforeunload", cancelRefrostTimer);

  return {
    get verified() { return state.verified; },
    get coverage() { return state.coverage; },
    proof,
    reset: resetFrost,
    destroy,
  };
}
