// Native ES module entrypoint for the Web client.
//
// Feature files are evaluated together inside one Function scope. They can
// share their application state (the old frontend contract), but that state is
// no longer installed as dozens of browser globals. Only functions referenced
// by generated inline handlers are exported explicitly at the end.
const FEATURES = [
  "util.js",
  "memory.js",
  "models.js",
  "render.js",
  "trace.js",
  "diagram.js",
  "graph.js",
  "views.js",
  "chat.js",
  "reader.js",
  "knowledge.js",
  "main.js",
];

async function loadFeatureSource(name) {
  const response = await fetch(`/static/js/${name}`);
  if (!response.ok) throw new Error(`无法加载 Web 模块：${name}`);
  return response.text();
}

const sources = await Promise.all(FEATURES.map(loadFeatureSource));
const source = sources.join("\n\n");
const names = [...new Set([
  ...source.matchAll(/^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/gm),
  ...source.matchAll(/^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=/gm),
].map(match => match[1]))];
const exports = names.length
  ? `\nreturn { ${names.map(name => `${name}: ${name}`).join(", ")} };`
  : "\nreturn {};";
const featureApp = new Function(`${source}${exports}`);
const publicHandlers = featureApp();
Object.assign(window, publicHandlers);
