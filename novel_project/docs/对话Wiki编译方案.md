# 对话 Wiki 编译 · 实施方案

> 交给实现者（DeepSeek）执行的任务书。实现完成后由审查者逐项验收（见文末验收清单）。
> 项目根：`D:/AIGC_1/novel-graphrag`，主包 `novel_project/`。

## 1. 目标

把多 Agent 问答的会话内容蒸馏为结构化的「对话 Wiki」，作为**独立检索层**接入现有三级检索，让系统能回答"我们之前讨论过什么"类问题，并复用已有讨论结论。

## 2. 四条红线（不可违反）

1. **独立分层**：对话 Wiki 存独立文件 `data/wiki/{novel}_dialogue.json`，**绝不写入**章节 Wiki（`{novel}_hierarchical.json` / `{novel}_wiki.json`）。检索结果中对话结论必须带明确标识（如 `【讨论结论】`），与原文事实区分。
2. **审核准入**：只有 `review_result.passed == True` 的问答才允许编译；语义缓存命中的问答不编译（无增量）。
3. **失败诚实**：遵守项目惯例——`call_llm` 失败返回 `None`，编译失败记日志并返回 `None`，**禁止**生成"伪装成功"的占位条目落盘。
4. **原子写入**：参照 `core/chapter_parser.py` 的 `_atomic_write` 模式（临时文件 + `os.replace`），杜绝半残文件。

## 3. 现状（已核实，无需重新探索）

- `core/memory.py` `SessionMemory`：只存会话摘要 + key_entities 到 `data/memory/index.json`，**不存完整对话**——需要先补原料。
- `web/routes/agent_routes.py` `/api/ask`（约 147/172 行）：已有 `memory.new_session()` / `memory.save_summary()` 调用点；`result = coordinator.run(...)` 的返回包含 `review_result`、`intent`、`materials`。
- `core/chapter_parser.py:269` `CHAPTER_WIKI_PROMPT`：prompt 风格参照（中文、强制 JSON、双花括号转义）。
- `core/text_match.py` `ngram_hits(a, b)`：中文 n-gram 匹配，可直接复用于检索打分。
- `data/wiki/{novel}_graph.json`：含图谱节点名，可用于实体匹配（避免额外 LLM 调用）。
- `core/agents/researcher.py`：按 description 关键词路由（"向量"/"Wiki"/"图谱"/"原文"，else `_search_all`）。

## 4. 改动清单

### 4.1 `core/memory.py` — 补全对话原料（扩展，不破坏旧 API）

- 新增 `append_turn(session_id, query, report, novel, review_passed, entities)`：
  - 完整记录追加到 `data/memory/sessions/{session_id}.json`（每条：`{session_id, novel, review_passed, turns: [{query, report, ts}], entities, created_at}`），原子写入。
  - `entities` 的提取**不调 LLM**：加载 `data/wiki/{novel}_graph.json` 的节点名，在 query+report 文本中做子串匹配得到。
- `new_session()` 增加可选参数 `novel=""`，写入索引条目。旧调用方不受影响。

### 4.2 `core/dialogue_compiler.py` — 新模块（核心）

```python
def compile_session(session_record: dict) -> dict | None
```

- **准入门槛**（任一不满足即返回 None）：
  1. `review_passed` 为 True
  2. 匹配到的实体数 ≥ 2
  3. 存在实质性报告内容（report ≥ 100 字）
- 通过门槛后调用 LLM（用下文 `DIALOGUE_WIKI_PROMPT`），产出条目：

```json
{
  "id": "dlg_xxxxxxxx",
  "topic": "讨论主题（20字内）",
  "conclusion": "核心结论（200字内）",
  "key_points": ["要点1", "要点2"],
  "entities": ["实体名"],
  "evidence_chapters": ["报告中引用的章节标题"],
  "speculative": false,
  "source_session": "20260725_120000",
  "created_at": "ISO时间"
}
```

- **校验**：`topic`/`conclusion` 非空、`entities` 非空列表，否则丢弃（记日志）。
- `save_dialogue_wiki(novel, entry)`：
  - 读 `data/wiki/{novel}_dialogue.json`（不存在则初始化 `{"entries": []}`）。
  - **冲突合并**：找出与新条目共享 ≥1 实体的旧条目（最多取 3 条），有候选则调 `DIALOGUE_MERGE_PROMPT` 决定 `merge / supersede / keep_both`，按决定写回；无候选直接追加。
  - 原子写入 + 保留最近 3 个 `.bak` 备份（参照 chapter_parser 的 `_backup`）。
- LLM 返回无法解析的 JSON 时返回 None（参照 `coordinator.py` 的 `_extract_json_array` 容忍尾随文字的写法）。

### 4.3 `web/routes/agent_routes.py` — 触发编译

- `/api/ask` 成功路径（review 通过、非缓存命中）：先 `memory.append_turn(...)` 落盘完整对话，再用 FastAPI `BackgroundTasks` 异步执行 `compile_session`——**不得阻塞响应**，编译异常只记日志。
- 缓存命中分支：只 `append_turn`，不编译。

### 4.4 `core/retriever.py` — 检索接入

- 新增 `search_dialogue_wiki(query, top_k=3)`：读取 `{novel}_dialogue.json`，打分 = 实体命中 ×5 + `ngram_hits(query, topic + conclusion)`，返回带 `source_type: "dialogue"` 的结果。
- `search()` 统一入口追加对话层结果，放在 Wiki/图谱之后，结果文本前缀 `【讨论结论】`。

### 4.5 `core/agents/researcher.py` — 路由增强

- description 含「讨论/之前/上次」→ 优先 `search_dialogue_wiki`。
- `_search_all` 结果中附加对话 Wiki 命中（标注来源，供 Writer 区分事实与讨论结论）。

## 5. Prompt 模板（照抄，不要改写）

```python
DIALOGUE_WIKI_PROMPT = """你是一个知识管理专家。以下是一段关于网络小说《{novel}》的问答讨论记录，已通过的讨论质量审核。请把这次讨论蒸馏为一条结构化的「讨论 Wiki」条目。

用户问题：{query}

讨论结论报告：
{report}

讨论中涉及的已知实体：{entities}

请严格按照以下JSON返回（不要加其他文字）：
{{
    "topic": "讨论主题（20字以内）",
    "conclusion": "本次讨论得出的核心结论（200字以内，只写有原文依据的内容）",
    "key_points": ["支撑结论的关键要点，每条50字以内，最多5条"],
    "evidence_chapters": ["结论所依据的章节标题，从报告中提取，没有则为空数组"],
    "speculative": false
}}

要求：
1. conclusion 只能包含报告中有依据的结论，不得添加报告之外的知识
2. 如果结论主要是推测性的（假设、如果、可能），将 speculative 设为 true
3. evidence_chapters 只填报告里明确引用过的章节标题
4. 不要输出 JSON 以外的任何文字
"""

DIALOGUE_MERGE_PROMPT = """你是知识库维护专家。知识库中已有以下旧条目，现在来了一条新条目，请判断如何处理。

旧条目：
{old_entries}

新条目：
{new_entry}

请严格按照以下JSON返回（不要加其他文字）：
{{
    "action": "merge | supersede | keep_both",
    "merged_entry": null,
    "reason": "一句话说明原因"
}}

判断规则：
1. merge —— 新旧条目讨论同一主题且结论互补：合并为一条，merged_entry 填入合并后的完整条目（保留双方 key_points 去重，conclusion 重写为统一表述，entities 并集）
2. supersede —— 新条目推翻了旧条目的结论：merged_entry 填入新条目，旧条目将被删除
3. keep_both —— 主题不同或各有价值：merged_entry 填 null，两条都保留
4. 拿不准时选 keep_both，宁可保留冗余，不可错误合并
"""
```

## 6. 测试要求（追加到 `tests/test_core.py`，用 unittest，mock 掉 `call_llm`）

- 门槛：review 未通过 / 实体 <2 / 报告过短 → 返回 None
- 正常编译：mock LLM 返回合法 JSON → 产出结构完整的条目
- LLM 返回 None 或非法 JSON → 返回 None，不落盘
- `save_dialogue_wiki`：无冲突追加、merge/supersede/keep_both 三种分支、文件内容原子完整
- `search_dialogue_wiki`：实体命中排序、无命中返回空
- 运行方式：`cd novel_project && ../.venv/Scripts/python.exe -m unittest tests.test_core`

## 7. 项目硬性约定

- 不加新依赖（requirements.txt 不动）；`sqlite3`/`json`/`threading` 等标准库优先
- 模型名一律走 `core/llm.py` 默认解析，**禁止硬编码** `deepseek-*` 模型名
- 数据路径参照现有模块的 `data/` 相对定位方式，禁止绝对路径
- 中文注释，风格与周边代码一致；改动最小化，不顺手重构无关代码

## 8. 验收清单（审查用）

- [ ] 完整对话确实落盘到 `data/memory/sessions/`
- [ ] 对话 Wiki 与章节 Wiki 物理隔离，检索结果带 `【讨论结论】` 标识
- [ ] 审核未通过的问答不产生条目（有测试锁死）
- [ ] LLM 失败路径无脏数据落盘（有测试锁死）
- [ ] 冲突合并三分支行为正确（有测试锁死）
- [ ] 全部测试绿；无新增依赖；无硬编码模型名/绝对路径
- [ ] `/api/ask` 响应不因编译而阻塞（编译在 BackgroundTasks 中）
