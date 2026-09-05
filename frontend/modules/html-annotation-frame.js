// Loaded only by the server's controlled HTML preview, inside its opaque sandbox.
(() => {
  if (window.parent === window) return;
  let enabled = false, channel = "", target = null, overlay = null;
  const send = (kind, data = {}) => parent.postMessage({__muselab: kind, channel, ...data}, "*");
  const draw = element => {
    if (!element || !element.isConnected) return;
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.setAttribute("data-muselab-annotation-overlay", "");
      overlay.style.cssText = "position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #3975ef;background:#3975ef18;border-radius:3px;box-sizing:border-box;";
      document.documentElement.append(overlay);
    }
    const r = element.getBoundingClientRect();
    Object.assign(overlay.style, {left:`${r.left}px`, top:`${r.top}px`, width:`${r.width}px`, height:`${r.height}px`});
  };
  const clear = () => { enabled = false; target = null; overlay?.remove(); overlay = null; };
  const selector = element => {
    const parts = [];
    for (let e = element; e && e !== document.documentElement && parts.length < 8; e = e.parentElement) {
      if (e.id && e.id.length <= 100) { parts.unshift(`#${CSS.escape(e.id)}`); break; }
      const siblings = e.parentElement ? [...e.parentElement.children].filter(n => n.tagName === e.tagName) : [e];
      parts.unshift(e.localName + (siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(e)+1})` : ""));
    }
    return parts.join(" > ").slice(0, 500);
  };
  addEventListener("message", event => {
    if (event.source !== parent || !event.data || typeof event.data.__muselab !== "string") return;
    const d = event.data;
    if (d.__muselab === "annotation-probe") {
      channel = String(d.channel || "").slice(0, 80);
      send("annotation-ready");
    } else if (d.__muselab === "annotation-enable" && d.channel === channel) {
      clear(); enabled = true;
    } else if (d.__muselab === "annotation-stop" && d.channel === channel) clear();
  });
  addEventListener("pointermove", event => {
    if (!enabled || !(event.target instanceof Element)) return;
    target = event.target; draw(target);
  }, true);
  addEventListener("click", event => {
    if (!enabled || !(event.target instanceof Element)) return;
    event.preventDefault(); event.stopImmediatePropagation();
    target = event.target; draw(target); enabled = false;
    const text = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ? "" : String(target.innerText || target.textContent || "").replace(/\s+/g, " ").trim().slice(0, 800);
    send("annotation-selected", {selector: selector(target), tag: target.localName,
      text, label: String(target.getAttribute("aria-label") || target.getAttribute("alt") || "").slice(0, 160)});
  }, true);
  addEventListener("scroll", () => { if (target) draw(target); }, {passive:true});
  addEventListener("resize", () => { if (target) draw(target); });
  addEventListener("keydown", event => { if (event.key === "Escape") { clear(); send("annotation-cancelled"); } });
})();
