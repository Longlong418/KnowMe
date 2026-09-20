# 上下文管理优化 + 上下文压缩 —— 技术调研与方案

> 状态：**方案（未落地）**。本文只做调研和设计，不含代码改动。
> 目标读者：准备对 KnowMe 做二次开发的自己。
> 实测环境：`KNOWME_PROVIDER=deepseek`，Python 3.11.15，2026-09-19。

---

## 0. 一句话结论

KnowMe 的上下文拼装是**按"重要性"排的，不是按"稳定性"排的** —— 而 prompt cache 只认前缀字节。
把拼装顺序改成按稳定性单调递减，就能拿到大部分收益，**且不需要任何新子系统**。
压缩则是在此之上补的能力，用来解决 `history_turns` 硬截断导致的上下文静默丢失。

**但必须先做埋点**：现状连缓存命中和门控开销都观测不到，不改这个，后面所有优化都无法证伪。

---

## 1. 现状实测

### 1.1 提示词的真实构成

`.knowme/usage.jsonl` 现有 12 次调用，全部 `deepseek-v4-pro`：

| 指标 | 值 |
|---|---|
| 总 input | 24,728 tokens |
| 总 output | 1,016 tokens |
| input p50 / p90 / max | 2,041 / 2,210 / 2,248 |

拆解这 ~2,041 tokens（实测各部件字符数 ÷ 3.6）：

| 部件 | 来源 | ≈tokens | 稳定性 |
|---|---|---|---|
| 工具 schema（5 个常开） | `tools/registry.py:44` | 817 | **配置级**（永不变） |
| 工具 schema（+3 记忆工具） | `tools/__init__.py:37` | ~+400 | 配置级 |
| SOUL.md | `.knowme/SOUL.md`（1,569 字节） | ~450 | **会话级** |
| 当前时间戳 | `runtime/session.py:70` | ~30 | **每轮变** |
| 模型身份 | `runtime/session.py:73` | ~25 | 会话级 |
| 门控检索到的记忆 | `runtime/session.py:81` | 变动 | **每轮变** |
| 匹配到的技能 | `runtime/session.py:84` | 变动 | **每轮变** |
| 历史 + 本轮提问 | `app.py:124` | 变动 | **每轮变** |

### 1.2 决定性的结构事实

`loop/agent.py:63-110` 的循环：

```python
for iteration in range(1, max_iterations + 1):
    response = client.messages.create(model=model, system=system, messages=messages, ...)
    ...
    messages.append({"role": "assistant", "content": response.content})
    ...
    messages.append({"role": "user", "content": tool_results})   # ← messages 原地增长
```

`messages` 是**原地追加**的，每次迭代都重发整个数组。
这正是前缀缓存的理想形态：第 N 次迭代和第 N-1 次共享几乎整个前缀，只差尾部追加的一小段。

而两种 wire format 的渲染顺序都是 **`tools → system → messages`**（Anthropic 文档明确；OpenAI 兼容层把 system 塞进 `messages[0]`，见 `loop/models.py:308-311`，同样在历史之前）。

**所以：时间戳一变 → system 变 → system 之后的一切（= 整段对话历史）在每一轮、每一次迭代上都全价重算。**

### 1.3 浪费的规模

| 场景 | 现状 | 说明 |
|---|---|---|
| 单轮短对话（现在） | ~2K input，其中尾巴 ~400 tok 不可缓存 | 绝对值小 |
| World Cup 那种 `iter 8` 回合 | 8 × 全量 prompt，**全部全价** | 设 prompt 5K → 40K 全价 input |
| 长驻会话（Telegram） | `history_turns=12` → 最多 24 条消息，每轮全价 | 随会话线性膨胀 |

> **诚实结论**：以当前 2K 的提示词规模，绝对省钱有限。
> 价值出现在**多迭代回合**和**长历史会话**上 —— 恰好是仓库自己拿来当卖点的两个场景。

---

## 2. 问题清单

### P1 · 动态信息插在 system 中间（核心）
`runtime/session.py:63-88` 的拼装顺序是 SOUL → **时间戳** → 模型身份 → **记忆** → **技能**。
稳定性排序是「稳定 → 每轮变 → 会话级 → 每轮变 → 每轮变」，完全乱序。
时间戳之后的一切都被永久作废。

### P2 · 观测不到，因此无法验证
- `loop/models.py:394-401`（非流式）和 `:466-468`（流式）只取 `prompt_tokens` / `completion_tokens`。
  - DeepSeek 返回的 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` **被丢弃**
  - Anthropic 的 `cache_creation_input_tokens` / `cache_read_input_tokens` **被丢弃**
- `ops/tracing.py:103 _record_usage` 只写 `in` / `out`。
- 参考文档对缓存失效的描述是：**「请求继续成功，只是账单更高，没有任何错误提示」**。没有埋点就必然踩这个坑。

### P3 · 门控与归纳的开销完全没记账
`memory/retrieval_gate.py` 和 `memory/consolidation.py` 都直接调模型，但只 `notify("gate", {decision})` / `notify("consolidation", {new_facts})` —— **不带 usage**。
`_record_usage` 只被 `loop/agent.py:87` 的 `llm` 事件触发。
→ **门控每轮都跑，这笔固定开销在账本里是隐形的。**

### P4 · `_record_usage` 记错了模型
`ops/tracing.py:110-112` 用的是 `self.settings.model`，不是真正应答的模型。
Graph 的 `quick_reply` 路径由 `settings.small_model` 作答（`app.py:102` 的 meta 也诚实标注了），但账本按主模型计价。**图路径的账是错的。**

### P5 · 工具输出全文进历史（压缩的第一目标）
`runtime/session.py:100-104`：

```python
summary = "; ".join(f"{c['tool']}({c['args']}) -> {c['output']}" for c in tool_calls)
record = f"{reply}\n[tools used: {summary}]"
```

`output` 是**完整**工具输出。`search_web` 的一次网页结果可能上千 token，会被**永久写进历史**，并在之后整个窗口期内每轮重发。
World Cup 那个 `search_web × N` 的演示，等于往历史里塞了 N 份网页全文。

### P6 · 没有压缩，只有硬截断
`app.py:123`：`window = self.settings.history_turns * 2`，然后 `self.session.history[-window:]`。
超出窗口的轮次**从提示词里静默消失**。它们还在 `state.db`，理论上能被门控检索回来，但**对话本身没了**。

注意区分：`memory/consolidation.py` 把 chat_log 蒸馏成 facts/episodes，那是**长期记忆**，不是上下文压缩 —— 它不向工作记忆回灌任何摘要。所以这里是个真实缺口。

### P7 · 工具集可能在会话中变化（风险）
工具渲染在**位置 0**，一变则全盘作废。
- 5 个常开工具是硬编码顺序 → **稳定 ✓**
- `tools/mcp_client.py:43-49`：MCP 工具按 `listed.items()` 顺序追加，顺序由**服务端** `list_tools()` 决定；且某个 server 连接失败会改变工具集合 → **不稳定 ✗**
- `settings.experimental` / `apple_tools` / `gh_tool` 开关改变工具集 → 每个组合是一个独立前缀（可接受，属启动期配置）

### P8 · 最小可缓存长度是陷阱
Anthropic 的最小可缓存前缀**因模型而异**，且**非单调**：

| 模型 | 最小前缀 |
|---|---:|
| Opus 5 / Fable 5 / Mythos 5 | 512 tokens |
| Opus 4.8 / Sonnet 5 / Sonnet 4.6 | 1024 tokens |
| Opus 4.7 | 2048 tokens |
| **Opus 4.6 / Opus 4.5 / Haiku 4.5** | **4096 tokens** |

当前 ~2K 的提示词在 **Haiku 4.5 或 Opus 4.6 上根本不会缓存** —— 不报错，只是 `cache_creation_input_tokens: 0`。
而 `PROVIDERS["anthropic"].small_model` 正是 `claude-haiku-4-5-20251001`（`loop/models.py:83`）。

### P9 · `KNOWME_MODEL` 被静默丢弃 ✅ **已修复 2026-09-19**

**现象**：`.env` 设了 `KNOWME_MODEL=deepseek-flash`，实际请求用的是 `deepseek-v4-pro`。

```
env KNOWME_MODEL    = 'deepseek-flash'
before get_client   = 'deepseek-flash'
after  get_client   = 'deepseek-v4-pro'   <-- 实际请求用的模型
```

**根因** —— `loop/models.py:234` 的家族归属表：

```python
owner = {f: name for name, p in PROVIDERS.items() if "/" not in (p.model or "x")
         for f in _families(p)}.get(family)
```

字典推导**后者覆盖前者**。`opencode_zen` / `opencode_go` 的默认模型是
`deepseek-v4-flash-free` / `deepseek-v4-flash`，家族名同样是 `deepseek`，而它们在
`PROVIDERS`（`loop/models.py:81`）里排在 `deepseek` **之后** → family `"deepseek"`
的归属被改写成 `opencode_go`。

于是 `_belongs_elsewhere("deepseek-flash", "deepseek")` 返回 `True`，`get_client`
（`:273-277`）把这个「继承自 env、却判定属于别人家」的模型名清空，回退到
`provider.model` = `deepseek-v4-pro`。

`:262-267` 那段注释的**本意是对的**（防止 `claude-haiku` 泄漏给 xAI），
但 `opencode_*` 的加入造成了一次跨 provider 家族碰撞，把 `deepseek` 自己误伤了。
**这是 `opencode_zen` / `opencode_go` 引入时带进来的回归。**

**连带后果**：`deepseek-v4-pro` 不在 `MODEL_PRICING`（`ops/pricing.py:55`）里
（那里只有 `deepseek-v4-flash` 和 `deepseek-v4-flash-free`），所以计价落到
`PRICING["deepseek"] = (0.435, 0.87)` 这个粗估值 —— **报表对实际在跑的模型是不准的**。

**与本方案的关系**：这条决定了要不要做 D6（anthropic wire 的断点）。
必须先修，否则你无法确定自己在优化哪个模型的缓存。

**修复（已完成）** —— `loop/models.py:_belongs_elsewhere` 加一段短路：provider **自己**
就在这个家族里出货时，这个家族对它就不算「别人家的」。

```python
current = PROVIDERS.get(provider_name)
if current is not None and family in _families(current):
    return False
```

之所以不是「把聚合商从 owner 表里摘掉」：那样会反过来把 `opencode_go` 自己的默认模型
`deepseek-v4-flash` 也丢掉。短路写法两条都保住。

行为矩阵（实测）：

| model | provider | 修复前 | 修复后 |
|---|---|---|---|
| `deepseek-flash` | deepseek | **True（错，被丢弃）** | False ✓ |
| `deepseek-v4-flash` | opencode_go | False | False ✓ |
| `claude-haiku-4-5-20251001` | opencode_go | True | True ✓ |
| `claude-haiku-4-5-20251001` | xai | True | True ✓ |
| `moonshot-v1-8k` | kimi | False | False ✓ |

回归测试：`evals/deterministic/test_providers.py` 新增 3 条
（`test_the_env_still_wins_for_its_own_deepseek_provider` 等）。
端到端已验证 `get_client` 后 `settings.model == 'deepseek-flash'`。
`test_providers.py` 47 passed；全量确定性测试 478 passed / 4 failed，
**那 4 个失败改动前就存在**（缺 `.env.example`、`skills/` 只有 `TEMPLATE.md`），与本修复无关。

**遗留（归入 P0/D5）**：`deepseek-flash` **不在 `MODEL_PRICING`**（`ops/pricing.py:55`
里只有 `deepseek-v4-flash` / `deepseek-v4-flash-free`），所以现在计价落到
`PRICING["deepseek"] = (0.435, 0.87)` 这个粗估值 —— 模型名修对了，**但价格仍是估的，
且不建模缓存折扣**。

---

### P10 · 两种 wire format 的缓存机制不同

| | `kind == "openai"` | `kind == "anthropic"` |
|---|---|---|
| 覆盖 provider | deepseek, openai, gemini, openrouter, xai, opencode_* | anthropic, kimi, glm, minimax |
| 机制 | **自动**硬盘前缀缓存 | **显式** `cache_control` 断点 |
| 需要的改动 | **只需调整顺序** | 需要在正确的块上打断点 |
| 最小单位 | 64-token 块（< 64 不缓存） | 见 P8，512–4096 |
| 缓存读价格 | ~1/10 输入价 | ~0.1× |
| 缓存写价格 | 无额外费用 | 5min TTL = 1.25×，1h TTL = 2× |
| 断点上限 | — | 每请求 4 个 |

**方案必须区分这两条路径。**

---

## 3. 方案

### 总原则

> 提示词的物理顺序 = 稳定性单调递减。稳定的在前，易变的在后，**每轮变的一律不放 system**。

前缀缓存是**前缀匹配**：前缀里任何一个字节变了，它之后的一切全部作废。
所以顺序本身就是机制，`cache_control` 标记只是补充。

---

### D1 · 重排拼装顺序（核心，零新子系统）

**新的分层：**

```
[静态前缀 —— 配置不变则永不改变]
  1. tools              排序后固定（见 D7）
  2. system[0]          SOUL.md

[会话级 —— 切换模型/provider 才变]
  3. system[1]          "你的模型是 X（provider Y）" + 时区（不是时间戳！）
                        → "时区：Asia/Shanghai (UTC+0800)"

[每轮变化 —— 全部移出 system]
  4. 当前时间戳    ┐
  5. 检索到的记忆  ├→ 放进【本轮 user 消息】的开头，带明确分隔
  6. 匹配到的技能  ┘
```

**改动点：**

| 文件 | 改什么 |
|---|---|
| `runtime/session.py:63-88` | `build_system()` 只产出静态 + 会话级两块；返回结构化块列表而非字符串 |
| `runtime/session.py` | 新增 `build_turn_context()` → 时间戳 + 记忆 + 技能，供 `app.py` 拼进 user 消息 |
| `app.py:118-124` | 把 `build_turn_context()` 的结果 prepend 到本轮 user 内容 |

**关键收益**：system 在整个会话内**字节完全一致** → 一次写入，之后每轮、每次循环迭代都命中。messages 变成纯追加链。

**需要处理的副作用 —— 时间戳的地位变化：**
现在 SOUL.md 里写着「the current date and time are given below — trust them, never ask」。移到 user 消息后位置变了，必须同步改 SOUL 措辞，否则模型可能仍然追问时间。`runtime/session.py:16-41` 的 `DEFAULT_SOUL` 和 `.knowme/SOUL.md` 都要改（后者是已存在的用户文件，需要考虑是否覆盖）。

**可选增强 —— 中间 system 消息：**
Anthropic 4.6+ 支持在 `messages[]` 里追加 `{"role": "system", ...}`，**既保留缓存前缀，又保留 system 的"操作者权威"语义**，比塞进 user 消息更好。
但：仅部分模型支持（Sonnet 5 文档自相矛盾），OpenAI wire 完全不支持，且 `Max 4 breakpoints` 之外还要求「必须跟在 user 消息之后」。

→ **建议**：先统一用 user 消息块（到处都能跑），把中间 system 消息作为后续可选优化，用 `Provider` 上的能力开关控制。

---

### D2 · 消息前缀：压缩而非滑窗

`app.py:123` 的 `history[-window:]` 换成「**固定头 + 追加尾**」：

```
messages = [摘要轮（固定位置，压缩时才变）]
         + [最近 N 轮的逐字原文（追加）]
         + [本轮 user 消息]
```

- 超出预算时，把**最老的一段**压进摘要，并丢弃那些轮次
- **绝不修改中间**。`add_exchange`（`session.py:103-104`）已经是纯追加 ✓
- 每次压缩 = **一次** 缓存失效，之后若干轮全部命中原前缀

这一条同时解决两件事：
1. P6 的静默丢上下文 —— 丢掉的内容进了摘要，而非消失
2. 滑动窗口导致的**前缀起点漂移** —— 现在窗口一动，整个 messages 前缀就变了

---

### D3 · 工具输出压缩（性价比最高）

分两层，**这是最关键的一点**：

| 层 | 存什么 | 为什么 |
|---|---|---|
| **实时**（`loop/agent.py:108`） | **完整**工具结果 | 模型需要它来推理当前这一轮 |
| **落历史**（`session.py:100-104`） | **截断摘要** + `…(省略 N 字符)` | 它会永久留在历史里并被反复重发 |

**做法**：给 `Tool` 加一个 `history_digest` 或 `max_history_chars` 字段（`tools/registry.py:16`），默认宽松，对 `search_web` / `github_read` / MCP 工具收紧。

```
search_web  → 保留 top-K 片段 + 每段首行（其余省略）
create_event → 完整保留（本来就短）
```

需要改的地方只有 `session.py:100-104` 那一行 `summary` 的构造 —— **没有其他调用方依赖这段文本的完整性**（`_status()` 在 `app.py:84-87` 只看开头是否含 "failed"/"timed out"，截断不影响）。

---

### D4 · 按 token 预算，而非按条数

`history_turns=12` 是**条数**上限，但花钱的是 **token**。

- 新增 `KNOWME_CONTEXT_BUDGET`（建议初值 8000）
- 用估算器（字符数 / 3.6，混合中英文）—— **不要**每轮调 `count_tokens`：那是 Anthropic 独有端点，OpenAI wire 没有，会造成 provider 分裂
- 阈值触发（如预算的 70%），`history_turns` 降级为兜底上限
- 估算器只需**单调、可比**，不需要精确。它的用途是判断「是否越过阈值」，不是报价

---

### D5 · 埋点（**必须最先做**）

不做这一步，后面每一步都无法证伪。参考文档明确建议：*每次改动后都要验证，而不只是初次搭建时*。

1. **捕获缓存字段** —— `loop/models.py`
   - `_create`（`:394-401`）与 `_stream.get_final_message`（`:463-468`）：从同一个 `usage` 对象上取
     - OpenAI wire：`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`
     - Anthropic：`cache_creation_input_tokens` / `cache_read_input_tokens`
   - 流式路径已有 `stream_options={"include_usage": True}`（`:434`）✓，字段在同一 usage 对象上
2. **写入账本** —— `ops/tracing.py:103 _record_usage`：加 `cache_read` / `cache_write` 字段，**并改用实际应答的模型**（修 P4）
3. **补齐缺失的调用** —— 门控与归纳（修 P3）：让 `retrieval_gate` / `consolidation` 也上报 usage，或统一走一个 `_metered_call()` 包装
4. **计价** —— `ops/pricing.py`：`price_for` 需要区分缓存读/写价。注意 `:53` 的注释明写「cache/batch discounts not modelled」，要改。保持「账本只存 token，价格读取时推算」的设计（`:5-8`）—— 这正是让历史行能被重新定价的优点，加字段后依然成立
5. **展示** —— `ops/dashboard.py`：Overview 加缓存命中率磁贴，Loop 页每轮显示命中情况

#### D5 实现细节：命中率怎么算（两个陷阱）

**命中率不是我们算的，是 provider 报的** —— 我们只做一次除法。但有三处要手写：

**① 取值（字段名两条路径不同）**

| wire format | 命中 | 写入 |
|---|---|---|
| Anthropic | `usage.cache_read_input_tokens` | `usage.cache_creation_input_tokens` |
| OpenAI 兼容 | `usage.prompt_cache_hit_tokens` | `prompt_cache_miss_tokens` |

已核实（2026-09-19，本地）：
- `anthropic 0.116.0` 的 `types/usage.py:18-22` **有类型定义** ✓
- `openai 2.45.0` **没有**定义 DeepSeek 的私有字段，但 `_models.py:128` 是
  `ConfigDict(extra="allow")`，未知字段落在 `__pydantic_extra__`，**属性访问可用** ✓

稳健写法 —— 两个形状都试，因为不同 OpenAI 兼容厂商字段名不统一：

```python
hit = (getattr(usage, "prompt_cache_hit_tokens", None)
       or getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None))
```

> ⚠️ **未验证**：SDK 能否承载字段 ≠ DeepSeek 实际返回哪个字段名。
> 后者需要一次真实调用确认（P0 落地时打一行日志，或发一次最小请求）。

**② 分母 —— 两条路径定义相反，最容易写错**

- **Anthropic**：`input_tokens` **只是未缓存的部分**。
  总 prompt = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`。
  用 `cache_read / input_tokens` 会算出 >100% 的荒谬值。
- **OpenAI 兼容**：`prompt_tokens` **本身就是总数**（= hit + miss），`hit / prompt_tokens` 才对。

**必须按 provider 的 wire format 分开算，不能共用一段逻辑。**

**③ 聚合 —— token 加权，不是比率平均**

```
100 token 命中 90%  +  10000 token 命中 0%
正确: 90 / 10100 = 0.9%
错误: (90% + 0%) / 2 = 45%      ← 小请求淹掉大请求
```

`usage.jsonl` 逐 call 追加，聚合时用 `sum(hit) / sum(total)`。

#### 两种"命中率"别混（这是诊断的关键）

| | 谁算 | 含义 |
|---|---|---|
| 报出来的命中率 | provider | 这次请求有多少按缓存价计费 |
| **结构上可缓存的比例** | **手写** | 提示词里稳定部分的占比 |

**诊断价值在两者的差值。**
命中率报 0、但结构上有 80% 是静态的 → **存在静默失效源**。
只知道前者，无法区分"本来就没得缓存"和"被破坏了"。
所以 P1 之后要**同时看这两个数**。


> **预期管理**：埋点做完后，**报表上的成本会先变高**。因为门控开销、以及之前被算作命中的部分现在被如实计。这是账目变准，不是变差。

---

### D6 · 缓存断点放置（只对 anthropic wire）

`kind == "openai"`（**包括你现在用的 deepseek**）→ **不需要改 API 调用**，D1 的顺序调整就是全部。

`kind == "anthropic"` → 加显式断点：

| 断点 | 位置 | 作用 |
|---|---|---|
| BP1 | 最后一个 tool 定义 / 最后一个静态 system 块 | tools + system 一起缓存 |
| BP2 | 上一轮最后一个内容块 | 消息增量缓存 |

- 上限 4 个
- TTL 用默认 5 分钟：agent loop 的迭代间隔远小于 5 分钟，**每次请求都会刷新计时器，5 分钟严格更便宜**（1h TTL 要付 2× 写入价）
- **不要用顶层自动缓存**：它会落在易变的尾部之后，等于对永远不会被读回的字节付写入费

**实现位置的选择（重要）：**
`run_loop` 目前把 `system` 当字符串传（`loop/agent.py:70-86`）。要打断点就得传块列表。
两个选项：

- ❌ 改 `run_loop` 签名 —— 违背仓库「loop 不动」的设计立场
- ✅ **在客户端边界包一层**（`loop/models.py`）：`build_system()` 返回结构化块，anthropic 路径注入 `cache_control`，`_to_openai` 把块列表拍平成字符串
  - `_to_openai:310-311` 需要 3 行改动处理 list 形式的 system
  - **`loop/agent.py` 一行不改**

---

### D7 · 工具集在会话内必须稳定

- `tools/mcp_client.py:43-49`：注册前**按名字排序**，把工具列表在会话开始时**钉死**
- 文档化：会话中途切换 experimental/apple/gh 开关会作废全部缓存（可接受）
- 已验证**不是**问题的地方：`procedural/loader.py:71` 用 `sorted(d.rglob())` 扫描，`:90` 的 `sort` 是稳定排序 → **技能块顺序确定 ✓**

---

### D8 · 压缩机制：复用记忆层已有的骨架

这个仓库已经有了正确的骨架，不要另起炉灶：

| 已有 | 作用 |
|---|---|
| `consolidation.py` | chat_log → facts/episodes（长期记忆），标记 `consolidated=1` |
| `retrieval_gate.py` | 读侧准入：这一轮**要不要**检索 |

**新增一块拼图：**

```
compaction.py   chat_log → 滚动摘要（工作记忆），
                存 summaries 表（按 session_id），
                在 chat_log 上记 compacted_upto 水位线
```

**设计要点：**
- 触发点：`app.py:107-109` 的 `maybe_consolidate` 旁边，同一形状、同一个 notify 模式
- **在回合边界跑，绝不在循环中途跑** —— 中途压缩会作废当前回合的缓存并污染 trace
- 用 `small_model` 跑
- **绝不删除原始 chat_log 行**（那是日志），只加水位线 —— 与 consolidation 保持同一纪律
- 摘要放在**固定位置**（messages 的第一条，或 system 的稳定块）

**和门控的关系可以这样理解：**

> `retrieval_gate` = 读侧准入（该不该取）
> `compaction` = 写侧预算（装不下时怎么压）

两者是对称的一对，worth 在架构图上并排画。

**两者都涉及 token 预算，应该共用一个估算器。**

---

### D9 · 顺带发现的其他优化

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 1 | `memory/retrieval_gate.py` | 每轮一次小模型调用，是固定成本 | 加便宜的预筛（代词/日期/"我的"/"记住"等正则），命不中就跳过模型调用；保持 fail-open |
| 2 | `memory/__init__.py:165 export_markdown` | 每轮重写 MEMORY.md，且要重读全部 facts+episodes | 加脏标记 |
| 3 | `runtime/session.py:44 load_soul` | 每轮从磁盘读 | 加 mtime 检查（`SkillLoader` 已经是这个模式，照抄） |
| 4 | `app.py:150-156` quick_reply | 同 P3，不计 usage | 统一走 `_metered_call()` |
| 5 | `runtime/session.py:100-104` | `[tools used: ...]` 无长度上限（见 P5） | 见 D3 |

---

## 4. 落地顺序

| 阶段 | 内容 | 状态 |
|---|---|---|
| ~~P0~~ | ~~D5 埋点~~ | ❌ **已取消** —— 见下方「为什么取消 P0」 |
| **P1** | **D1 提示词重排** | ✅ **已完成 2026-09-19** |
| P2 | D6 断点（anthropic wire） | 暂不需要（deepseek 是自动前缀缓存） |
| P3 | D3 工具输出压缩 + D4 token 预算 + D2 定长头 | 待做 |
| P4 | Dashboard 可见性 | 待做 |
| P5 | D8 compaction 接入 summaries 表 + D9 杂项 | 待做 |

### 为什么取消 P0（原方案里的排序是错的）

我原先主张「必须先埋点，否则无法证伪」。**这个理由是站不住的。**

D1 重排的正确性来自**机制**，不来自测量：prompt cache 是前缀匹配，所以把每轮变化的
时间戳插在 system 中间就是错的 —— 这一点不需要任何数据就能判定。**修它的依据是原理。**

验证也有现成的、零新代码的信号：

| 信号 | 在哪 |
|---|---|
| 延迟 | `app.py` 已经在记 `latency_ms` |
| DeepSeek 控制台的缓存命中统计 | provider 侧 |
| 一行临时日志 | 只在真要查具体数字时打 |

只有在这两个信号都不清不楚时，才值得加那 5 行字段。**不早于此。**

同时砍掉的过度设计：命中率磁贴、Loop 页每轮命中显示、计价区分缓存读写价、
「两种命中率」诊断框架。**门控/归纳的记账（P3）是另一件事** —— 它关乎成本账目准不准，
和缓存无关，想做时单独做。


---

## 5. 验证方式

### 5.1 确定性测试（符合本仓库纪律 —— 0/1，不调模型）

按 `evals/deterministic/test_working_memory.py` 的既有风格（其中已有 `test_system_prompt_includes_current_time` 这类断言）：

| 测试 | 断言 |
|---|---|
| **`test_system_prompt_is_stable_across_turns`** | 连续两轮不同提问，`build_system()` 输出**字节完全相同** ← **这是时间戳 bug 的回归锁，最重要的一条** |
| `test_turn_context_carries_the_timestamp` | 时间戳确实出现在本轮 user 消息里（防止上一行改成「干脆删掉时间」） |
| `test_tool_schemas_order_is_deterministic` | 两次 build registry，`schemas()` 顺序一致 |
| `test_long_tool_output_is_digested_in_history` | 超长工具输出进历史时被截断，且 `chat_log` 里的原始行未被改写 |
| `test_compaction_drops_no_raw_rows` | 触发压缩后，`chat_log` 行数只增不减 |
| `test_compaction_triggers_at_budget` | 越过 token 阈值时摘要被写入，水位线前移 |

注意：现有 `test_working_memory.py::test_system_prompt_includes_current_time` 在 P1 **必然需要改写** —— 它断言的正是我们要移除的行为。这是预期的，不是回归。

### 5.2 真实缓存的验证（需要 key）

参考文档明确建议「**要有一个常驻检查，而不是看一次**」：

```python
# 连续两次相同前缀的请求 → 断言 cache_read > 0
```

放 `evals/judge/`（有 key 才跑），或加一个 `make cache-check`。
失败特征很明确：**`cache_read` 恒为 0** → 有静默失效源。

### 5.3 端到端

```bash
make eval          # 确定性全绿（含新增用例）
make gate          # 发布门禁
uv run knowme dashboard   # 肉眼确认缓存磁贴 + Loop 页命中率
```

**回归验证的具体手法**（参考文档给的做法）：记录连续几次请求的完整 JSON body，剥掉 `cache_control` 标记后逐对 diff。
增长中的会话在**尾部**合法地不同；必须是「上一次的 prompt 原样重现为这一次的前缀」。第一个出现在重叠区内的差异点就是失效点。

---

## 6. 风险与取舍

| 风险 | 说明 | 应对 |
|---|---|---|
| **时间戳移出 system 可能改变行为** | 部分模型对 system 权重更高 | 同步改 `DEFAULT_SOUL` 措辞；加确定性用例锁住「时间戳仍在提示词里」 |
| **检索记忆移出 system 可能降低利用率** | SOUL 里「trust it」的语义依赖位置 | 用明确分隔块；跑 `evals/judge` 的 `MemoryUse` 指标对比前后 |
| **压缩是有损的** | 摘要必然丢细节 | 原始行不删（水位线方案）；摘要只压缩**最老**的段落 |
| **`evals/` 不在 git 里** | `.gitignore` 排除了 `evals/*`、`docs/*`、`sql`、`scripts` | 新增测试和本文档都不会进版本库 —— **提交前确认这是你要的** |
| **成本报表短期变难看** | 见 D5 预期管理 | 明确告知：是账目变准 |
| **压缩阈值调不好会频繁失效** | 阈值太低 → 每几轮就压缩一次 | 用预算的 70% 做迟滞；观察 trace 里的压缩事件频率 |

---

## 7. 决策记录

### 已决定

| # | 结论 |
|---|---|
| **`.env` 引号** | 无问题。`python-dotenv` 会剥掉引号，实测 `Settings.model == 'deepseek-flash'`（干净字符串） |
| **主模型** | **P9 已修复**（2026-09-19）。`KNOWME_MODEL` 现在生效。→ **D6（anthropic wire 断点）暂不需要**，deepseek 走自动前缀缓存路径 |
| **P9 vs P1 顺序** | 先修 P9 ✅ 已完成。**P0（埋点）取消**，P1 ✅ 已完成 —— 见 §4 |
| **P0 埋点** | **取消**。用户明确只需重组上下文，不需计算缓存命中率 |
| **`docs/`、`evals/` 的 gitignore** | **有意为之**，不改。意味着本文档和新增测试不进版本库 —— 仅本地有效 |
| **SOUL.md 里的时间说明** | **直接删掉那句**，不提时间这件事。见下方「P1 的行为变更」 |
| **摘要粒度** | 先做**滚动摘要**（单块，便宜），用 judge 评测决定要不要升级到分段摘要 |

### P1 落地记录 ✅ 已完成 2026-09-19

**改了什么（4 个文件）**

| 文件 | 改动 |
|---|---|
| `knowme/runtime/session.py` | `build_system()` **去掉 `user_message` 参数**，只返回 SOUL + 模型身份；新增 `build_turn_context(user_message, notify)` 返回时钟 + 记忆 + 技能 |
| `knowme/app.py` | `_run_full_turn` 把 `build_turn_context()` 的结果 prepend 到本轮 user 内容 |
| `knowme/runtime/session.py` | `DEFAULT_SOUL` 删掉「时间在下方」那条；记忆那条「below」→「with the message」 |
| `.knowme/SOUL.md` | 同上两处（本地文件，与旧 `DEFAULT_SOUL` 原本逐字节相同） |
| `evals/deterministic/test_working_memory.py` | 改指向 + 新增 3 条 |

新增的 3 条锁：
- `test_system_prompt_carries_no_clock` —— 结构不变量：system 里不许有时钟
- `test_per_turn_memory_does_not_leak_into_the_system_prompt` —— 用一个每次答案都不同的 memory 桩，
  泄漏会表现为字符串变化而非 flaky
- `test_the_turn_context_is_not_persisted` —— context 块不许落进 `chat_log` / `history`
  （**已用注入回归验证过它抓得住**，不是只跑通就算）

**实测结果**

```
every model call, in order:
  GATE/small model=deepseek-flash   system=no
  LOOP       model=deepseek-flash   system=yes(1586B)
  ...（每轮如此）

LOOP calls = 3
  system identical across all LOOP calls = True
  system bytes = 1586
  history growth (messages count per LOOP call): [1, 3, 5]
```

- system **逐字节一致**（1586 字节），历史纯追加 `[1, 3, 5]`
- 确定性测试 `480 passed`（+2 条新增），4 个失败**改动前就存在**
- 顺带验证了 P9 的修复：模型名确实是 `deepseek-flash`

**遗留风险（重要）**

删掉 SOUL 里那条时间说明后，`test_the_turn_carries_the_current_time` 只能断言
**时钟仍在提示词里**，断言不了**模型确实会用它**。
「agent 反问用户现在几点」那个线上 bug 如果复发，确定性测试抓不到 ——
**只有真实跑一次 / judge 测试能发现**。建议 P1 之后对着真模型跑一次
「30 分钟后提醒我」验证行为没退化。

### P1 的行为变更（因上一条决策而明确）

`runtime/session.py:16-41` 的 `DEFAULT_SOUL` 里这句**删除**：

> ~~"- When the user wants to schedule something, use create_event. Resolve relative
> dates and times ("next Tuesday", "in 30 minutes") to ISO timestamps yourself;
> the current date and time are given below — trust them, never ask the user
> what time it is."~~

改成不提时间的位置，只说「自己把相对时间解析成 ISO 时间戳」。
`.knowme/SOUL.md`（已存在的用户文件，1,569 字节）里对应那句也**不自动改写** ——
打印一条提示让用户自己删（自动改写用户文件的风险大于收益）。

**但时间戳本身必须留在提示词里**（挪到本轮 user 消息）。这不是可选项：
README 和 `test_working_memory.py` 记录了一个真实修过的线上 bug ——
agent 不知道当前时间，排「30 分钟后」的日程时反问用户现在几点。

因此 `test_working_memory.py::test_system_prompt_includes_current_time`
**改指向而非删除**：从「system prompt 含 HH:MM」改成「本轮提示词（system + user）含 HH:MM」。

### 仍需你决定

1. **`deepseek-flash` 要不要补进 `MODEL_PRICING`？**
   现在模型名修对了，但价格仍是 `PRICING["deepseek"] = (0.435, 0.87)` 的粗估值。
   我没有可靠的 Flash 系列官方价目，**不擅自填数字**。你有官方价目的话我按
   `(输入$, 输出$, 缓存命中$)` 补上；否则并入 D5 一起做。

2. **压缩的触发阈值初值**：我建议 `KNOWME_CONTEXT_BUDGET=8000`、70% 迟滞。
   这是拍的，最终要靠 D5 的实测数据校准。


