"""「合并」不能再问用户要 ID。

原来的流程是 `prompt("把这条事实合并到哪个事实 ID？")` —— 可是事实 ID 从来不露在页面
上，除了翻数据库没人知道它是几。用户的原话：「用户是不知道ID的」。

改法是：弹出一张列表，让他看着事实本身（主题 + 正文）挑一条目标。后端的
`merge_fact` 接口一个字没动，它收的仍然是两个 ID，只是这两个 ID 由行本身带来，
不再由人敲进来。

这个文件跑的是真的 memory.js（node + 极小的 DOM 桩），因为要钉住的都是控制流：
弹的是哪个盒子、候选里有没有自己、关掉之后 `editing` 有没有松开。headless 浏览器
能证明的东西更少、要搭的东西更多。node 是硬依赖，没有就 skip 而不是 fail。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[2]
      / "knowme" / "ops" / "static" / "js")
MEMORY_JS = JS / "memory.js"
VIEWS_JS = JS / "views.js"

HARNESS = r"""
const fs = require("fs");
const memSrc = fs.readFileSync(process.argv[2], "utf8");
const viewsSrc = fs.readFileSync(process.argv[3], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

// ---- 弹窗挂在哪儿：两边的 id 必须一致 --------------------------------------
// 视图里那个空盒子是 memory.js 唯一找得到的落点，两边写不一样就是"点了没反应"。
(function testTheBoxExists() {
  const rootId = (viewsSrc.match(/id="(memory-modal-root)"/) || [])[1];
  assert(!!rootId, "语义记忆那一页提供了弹窗要挂的盒子");
  assert(memSrc.includes('"' + rootId + '"'),
         "memory.js 找的就是那个盒子（两边 id 一个字都不能差）");
  assert(!/\bprompt\(/.test(memSrc),
         "合并这条路上不再有 prompt —— 问 ID 的输入框就是被删掉的那件事");
})();

// ---- 挑选和真正动手：跑真的 memory.js --------------------------------------
const runPicker = (function testPicker() {
  let editing = false;
  function markEditing() { editing = true; }
  const els = {};
  const document = { getElementById: id => els[id] || null };
  let renders = 0, refreshes = 0, activeView = "memory";
  function render() { renders++; }
  function refresh() { refreshes++; }
  const posts = [], alerts = [];
  const answers = [];
  const postJSON = async (url, body) => {
    posts.push({ url, body });
    return answers.length ? answers.shift() : { ok: true };
  };
  let asked = null;
  function confirm(msg) { asked = msg; return true; }
  function alert(m) { alerts.push(m); }
  const esc = s => String(s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ACTIVE_AGENT = "default";
  const D = { facts: [
    { id: 3, subject: "奶奶", content: "住在杭州", source: "user" },
    { id: 7, subject: "奶奶的偏好", content: "喜欢桂花糕", source: "user" },
    { id: 9, subject: "项目", content: "KnowMe", source: "user" },
  ] };
  eval(memSrc);
  // 弹窗里那个盒子：只关心 innerHTML 被写了什么。
  els["memory-modal-root"] = { innerHTML: "", querySelector: () => null };
  const html = () => els["memory-modal-root"].innerHTML;
  const text = h => String(h).replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim();
  const rows = h => [...String(h).matchAll(
    /<button class="memtarget"[^>]*>([\s\S]*?)<\/button>/g)].map(m => m[1]);

  return (async () => {
    // 打开弹窗
    mergeFact(3);
    assert(editing === true,
           "弹窗一开就 markEditing()——不然 5 秒轮询会把这张列表当场刷掉");
    assert(html().includes("住在杭州"), "上面那条写明了要合并的是哪一条事实");
    assert(rows(html()).length === 2,
           "候选里是除自己以外的每一条事实");
    assert(text(rows(html())[0]) === "奶奶的偏好 喜欢桂花糕",
           "每一行写的就是主题和正文，没有一个 ID： " + text(rows(html())[0]));
    assert(text(rows(html())[1]) === "项目 KnowMe", "…每一条都是这样");
    assert(rows(html()).every(r => !/事实 ?ID|#\d/.test(text(r))),
           "候选行上看不到任何 ID 字样");
    assert(!rows(html()).some(r => text(r).includes("住在杭州")),
           "要合并的那条自己不在候选里（合并没有自己对自己做的意义）");
    assert(/mergeFactInto\(3, 7\)/.test(html()),
           "点哪一行，就是拿那一行的 ID 去合并");

    // 选一条：说清楚会发生什么，然后才发请求
    await mergeFactInto(3, 7);
    assert(asked && asked.includes("奶奶") && asked.includes("奶奶的偏好"),
           "确认框里写的是这两条事实： " + JSON.stringify(asked));
    assert(/删掉/.test(asked) && /无法撤销/.test(asked),
           "说清楚了这条会被删掉、而且撤不回来（merge 真的会 DELETE 掉来源那行）");
    assert(posts.length === 1 && posts[0].body.action === "merge_fact"
           && posts[0].body.id === 3 && posts[0].body.target_id === 7
           && posts[0].body.agent_id === "default",
           "发给后端的就是两个 ID： " + JSON.stringify(posts[0].body));
    assert(alerts.length === 0, "顺利的时候不弹任何提示");
    assert(editing === false,
           "合并完把 editing 松开——不松，5 秒轮询就被永久冻住了");
    assert(els["memory-modal-root"].innerHTML === "", "成功之后弹窗关掉");
    assert(refreshes === 1, "并且重新拉一次数据，列表上看到的是合并后的样子");

    // 失败：把列表留着，人可以重挑一条
    mergeFact(3);                                   // 重新打开（上一次成功时关掉了）
    answers.push({ error: "no such fact" });
    await mergeFactInto(3, 9);
    assert(alerts.length === 1 && alerts[0] === "no such fact",
           "后端说不行的原因是原话转给用户，不吞掉");
    assert(els["memory-modal-root"].innerHTML !== "", "失败就把列表留在那儿");
    assert(editing === true, "…于是轮询也仍然让它留在那儿");
    alerts.length = 0;

    // 关掉：三种关法都是同一个出口
    const before = renders;
    closeMemoryModal();
    assert(els["memory-modal-root"].innerHTML === "", "关闭清空盒子");
    assert(editing === false, "关闭也必须松开 editing");
    assert(renders > before, "而且在记忆页上要重画一次（不然表格是弹窗底下的旧样子）");

    mergeFact(3);
    memoryModalKeydown({ key: "a" });
    assert(editing === true, "别的按键不关弹窗");
    memoryModalKeydown({ key: "Escape" });
    assert(editing === false && els["memory-modal-root"].innerHTML === "",
           "Escape 关掉弹窗（并且松开 editing）");

    // 不在记忆页上时，关闭不需要重画
    activeView = "ops";
    mergeFact(3);
    const renders2 = renders;
    closeMemoryModal();
    assert(renders === renders2,
           "人已经不在记忆页上了就不再重画（render() 会照 hash 画别的页）");

    // 主题很长的时候，确认框里的名字要收尾，不能撑成一行
    assert(shortSubject("一二三四五六七八九十十一十二十三十四十五", 14).endsWith("…")
           && shortSubject("一二三四五六七八九十十一十二十三十四十五", 14).length === 15,
           "长主题在确认框里截断并收尾");
    assert(shortSubject("短", 14) === "短", "短主题原样不动");
  })();
})();

runPicker
  .then(() => process.exit(failures ? 1 : 0))
  .catch(err => { console.log("FAIL  the harness threw: " + (err && err.stack || err));
                  process.exit(1); });
"""


@pytest.fixture(scope="module")
def node():
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node is not installed — the frontend has no other test runner")
    return exe


def test_merging_a_fact_asks_for_the_fact_not_its_id(node, tmp_path):
    for path in (MEMORY_JS, VIEWS_JS):
        assert path.exists(), f"missing frontend source: {path}"
    harness = tmp_path / "merge_picker_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(MEMORY_JS), str(VIEWS_JS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)

    print(proc.stdout)
    assert proc.returncode == 0, f"frontend checks failed:\n{proc.stdout}\n{proc.stderr}"
