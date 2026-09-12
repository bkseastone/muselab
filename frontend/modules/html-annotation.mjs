// The parent owns file/workspace identity. Frame messages are untrusted descriptions,
// never commands, markup to render, selectors to execute in the parent, or paths.
export function createHtmlAnnotation(app) {
  let frame = null, channel = "", ownerPath = "", ownerWorkspace = "", timer;
  const state = app.htmlAnnotation;
  const post = kind => frame?.contentWindow?.postMessage({__muselab:kind, channel}, "*");
  const valid = () => frame?.isConnected && ownerPath === app.selected
    && ownerWorkspace === app.fileWorkspacePath() && app.previewMode === "html";
  const stop = () => {
    clearTimeout(timer); post("annotation-stop");
    frame?.removeEventListener("load", stop);
    frame = null; state.active = false; state.loading = false;
    state.selection = null; state.comment = "";
  };
  const api = {
    stop,
    start() {
      stop(); state.error = "";
      frame = app._previewFrameEl("html");
      const src = frame && new URL(frame.getAttribute("src") || "", location.href);
      if (!frame || !src || src.origin !== location.origin || src.pathname !== "/api/files/raw"
          || src.searchParams.get("preview") !== "1") {
        state.error = app.lang === "zh" ? "仅支持当前工作目录中的 HTML 文件预览。" : "Open an HTML file from the current workspace first.";
        return;
      }
      ownerPath = app.selected; ownerWorkspace = app.fileWorkspacePath();
      channel = app._uuid(); state.loading = true;
      frame.addEventListener("load", stop, {once:true});
      timer = setTimeout(() => {
        stop(); state.error = app.lang === "zh" ? "此页面无法标注，请重新打开文件后再试。" : "Annotation is unavailable here. Reopen the file and try again.";
      }, 2000);
      post("annotation-probe");
    },
    onMessage(event) {
      const d = event.data;
      if (!valid() || event.source !== frame.contentWindow || event.origin !== "null"
          || !d || d.channel !== channel) return;
      if (d.__muselab === "annotation-ready" && state.loading) {
        clearTimeout(timer); state.loading = false; state.active = true; post("annotation-enable");
      } else if (d.__muselab === "annotation-selected" && state.active && typeof d.selector === "string") {
        state.active = false;
        state.selection = {selector:d.selector.slice(0,500), tag:String(d.tag || "").slice(0,30),
          text:String(d.text || "").slice(0,800), label:String(d.label || "").slice(0,160)};
        app.$nextTick(() => document.querySelector('.html-annotation-comment')?.focus());
      } else if (d.__muselab === "annotation-cancelled") stop();
    },
    add() {
      if (!valid() || !state.selection) { stop(); return false; }
      if (!app.currentId) { state.error = app.lang === "zh" ? "请先打开一个会话。" : "Open a conversation first."; return false; }
      app._captureComposerState(app.currentId);
      const draft = app._ensureTabState(app.currentId).draft;
      if (draft.pendingQuotes.length >= 4) { state.error = app.lang === "zh" ? "一次最多引用 4 段内容。" : "Up to 4 quotes per message."; return false; }
      const s = state.selection;
      const text = [`HTML element: ${s.selector}`, s.label, s.text,
        state.comment.trim() ? `${app.lang === "zh" ? "标注" : "Comment"}: ${state.comment.trim()}` : ""].filter(Boolean).join("\n");
      draft.pendingQuotes.push(app._selectionQuoteSnapshot({source:"preview", path:ownerPath,
        workspace:ownerWorkspace, text}));
      app.pendingQuotes = draft.pendingQuotes;
      stop();
      if (app._isMobileLayout()) app.setMobileTab("chat");
      app.$nextTick(() => app.$refs.chatInput?.focus());
      return true;
    },
  };
  window.addEventListener("message", event => api.onMessage(event));
  for (const path of ["selected", "activeWorkspace", "previewSurface"]) app.$watch(path, stop);
  return api;
}
