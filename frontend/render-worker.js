// Pure parsing only. The page sanitizes Markdown output before inserting it.
self.onmessage = ({ data }) => {
  try {
    let html;
    if (data.kind === "markdown") {
      if (!self.marked) importScripts("vendor/marked.min.js" + self.location.search);
      html = self.marked.parse(data.text);
    } else if (data.kind === "highlight") {
      if (!self.hljs) importScripts("vendor/highlight.min.js" + self.location.search);
      const languages = data.languages.filter(lang => self.hljs.getLanguage(lang));
      html = data.language && self.hljs.getLanguage(data.language)
        ? self.hljs.highlight(data.text, { language: data.language, ignoreIllegals: true }).value
        : self.hljs.highlightAuto(data.text, languages).value;
    } else throw new Error("unsupported render operation");
    self.postMessage({ html });
  } catch (_) { self.postMessage({ failed: true }); }
};
