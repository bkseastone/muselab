// Numeric, page-local diagnostics. No request URLs, text, file paths or session
// identifiers leave this closure or appear in the public snapshots.
export function createBrowserMetrics(app) {
  const started = performance.now();
  const active = new Map();
  let number = 0, longTasks = 0, longTaskMs = 0;
  const rows = [];
  const supported = typeof PerformanceObserver !== "undefined"
    && PerformanceObserver.supportedEntryTypes?.includes("longtask");
  const publish = () => {
    const fcp = performance.getEntriesByName("first-contentful-paint")[0];
    const ready = performance.getEntriesByName("muselab-app-ready").at(-1);
    app.browserMetrics = {fcpMs:fcp?.startTime ?? null, readyMs:ready?.startTime ?? null,
      monitorStartedMs:started, longTasksSupported:!!supported, longTasks, longTaskMs,
      turns:rows.map(t => ({number:t.number, firstMs:t.firstMs, finalMs:t.finalMs}))};
  };
  if (supported) {
    const observer = new PerformanceObserver(list => {
      for (const entry of list.getEntries()) {
        if (entry.startTime < started) continue;
        longTasks++; longTaskMs += entry.duration;
      }
      publish();
    });
    observer.observe({type:"longtask"});
  }
  const api = {
    publish,
    startTurn(sid) {
      const turn = {sid, number:++number, start:performance.now(), firstMs:null, finalMs:null, pending:new Set()};
      active.set(sid,turn); rows.unshift(turn);
      if (rows.length > 12) {
        const dropped = rows.pop();
        if (active.get(dropped.sid) === dropped) active.delete(dropped.sid);
      }
      publish(); return turn;
    },
    currentTurn(sid) { return active.get(sid) || null; },
    paint(turn, phase, key) {
      if (!turn || key == null || !["first", "final"].includes(phase)
          || turn[`${phase}Ms`] != null || turn.pending.has(phase)) return;
      turn.pending.add(phase);
      // Two frames separate reactive publication from a completed browser paint.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        turn.pending.delete(phase);
        if (active.get(turn.sid) !== turn || app.currentId !== turn.sid
            || document.visibilityState !== "visible") return;
        const pane = document.querySelector(`.msg-pane[data-tid="${CSS.escape(turn.sid)}"]`);
        const node = pane?.querySelector(`[data-message-key="${CSS.escape(String(key))}"]`);
        const viewport = document.querySelector('.chat-body');
        if (!node || !viewport || !node.getClientRects().length || !viewport.getClientRects().length) return;
        const r = node.getBoundingClientRect(), v = viewport.getBoundingClientRect();
        if (r.bottom <= v.top || r.top >= v.bottom || r.right <= v.left || r.left >= v.right) return;
        turn[`${phase}Ms`] = performance.now() - turn.start;
        publish();
      }));
    },
  };
  publish(); return api;
}
