// Settings drafts stay in this page only: credentials never enter browser storage.
const clone = value => JSON.parse(JSON.stringify(value));
const clean = value => {
  if (Array.isArray(value)) return value.map(clean);
  if (value && typeof value === "object") return Object.fromEntries(
    Object.keys(value).sort().filter(key => !["open", "show"].includes(key))
      .map(key => [key, clean(value[key])]));
  return value;
};
const signature = (value, name) => JSON.stringify(clean(name === "keys"
  ? Object.fromEntries(Object.entries(value || {}).filter(([, item]) => String(item || "").trim()))
  : value));

export function createSettingsDraftGuard(app) {
  const baseline = new Map();
  const values = () => ({
    defaults: app.settings.draftDefaults,
    keys: app.settings.draftKeys,
    newProvider: app.settings.providerNew,
    mcp: app.settings.mcpDraft,
    hook: app.settings.hooks.draft,
    memory: app.settings.memory.config,
    ...Object.fromEntries(Object.entries(app.settings.providerDrafts).map(
      ([id, draft]) => [`provider:${id}`, draft])),
  });
  const guard = {
    capture(names = Object.keys(values())) {
      const current = values();
      for (const name of names) if (name in current) baseline.set(name, clone(current[name]));
      app._syncBeforeUnloadGuard();
    },
    accept(name, value) { baseline.set(name, clone(value)); app._syncBeforeUnloadGuard(); },
    dirty(name) {
      const current = values();
      const names = name ? [name] : Object.keys(current);
      return names.some(key => baseline.has(key)
        && signature(current[key], key) !== signature(baseline.get(key), key));
    },
    discard() {
      const simple = {defaults: "draftDefaults", keys: "draftKeys", newProvider: "providerNew", mcp: "mcpDraft"};
      for (const [key, value] of baseline) {
        if (simple[key]) app.settings[simple[key]] = clone(value);
        else if (key === "memory") app.settings.memory.config = clone(value);
        else if (key === "hook") app.settings.hooks.draft = clone(value);
        else if (key.startsWith("provider:")) app.settings.providerDrafts[key.slice(9)] = clone(value);
      }
      app._syncBeforeUnloadGuard();
    },
  };
  for (const path of ["draftDefaults", "draftKeys", "providerDrafts", "providerNew", "mcpDraft", "hooks.draft", "memory.config"])
    app.$watch(`settings.${path}`, () => app._syncBeforeUnloadGuard());
  return guard;
}
