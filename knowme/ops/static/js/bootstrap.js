// Native ES module entrypoint for the Web client.
//
// Feature files are evaluated together inside one Function scope. They can
// share their application state, while the browser receives only the names
// referenced by generated event handlers.
const FEATURES = [
  "util.js",
  "memory.js",
  "models.js",
  "render.js",
  "trace.js",
  "diagram.js",
  "graph.js",
  "views.js",
  "coding.js",
  "deepresearch.js",
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
const declaredNames = [...new Set([
  ...source.matchAll(/^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/gm),
  ...source.matchAll(/^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=/gm),
].map(match => match[1]))];
const eventExpressions = [...source.matchAll(/on(?:click|input|change|focus|keydown)=["']([^"']*)["']/g)]
  .map(match => match[1]);
const eventNames = new Set(eventExpressions.flatMap(expression =>
  [...expression.matchAll(/[A-Za-z_$][\w$]*/g)].map(match => match[0])));
// These handlers belong to the static HTML shell, so they are not visible in
// the feature source's generated inline attributes.
const shellHandlers = new Set(["openAgent"]);
const names = declaredNames.filter(name => eventNames.has(name) || shellHandlers.has(name));
const exports = names.length
  ? `\nreturn { ${names.map(name => `${name}: ${name}`).join(", ")} };`
  : "\nreturn {};";
const featureApp = new Function(`${source}${exports}`);
const publicHandlers = featureApp();
Object.assign(window, publicHandlers);

// The static shell uses data attributes instead of inline handlers. Dynamic
// views still use the existing handler boundary until they are migrated.
document.addEventListener("click", event => {
  const agent = event.target.closest("[data-agent]");
  if (agent) publicHandlers.openAgent(agent.dataset.agent);
  if (event.target.closest("[data-route='models']")) location.hash = "#models";
});
