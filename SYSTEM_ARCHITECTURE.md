# Novel-GraphRAG 系统架构

> 完整数据流：上传 → 解析 → 编译 → 检索 → 问答

---

## 一、整体架构分层

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                  用户交互层                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │ 仪表盘   │  │ 图谱查看  │  │ 问答对话  │  │ 上传编译  │  │ MCP API  │             │
│  │ /        │  │ /graph   │  │ /chat    │  │ /upload  │  │ (Cursor) │             │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘             │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                        │
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                  API 路由层                                          │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐                  │
│  │  Auth Routes     │  │  Agent Routes    │  │  安全中间件      │                  │
│  │  /api/auth/*     │  │  /api/ask/       │  │  JWT验证/限流    │                  │
│  │  login/register  │  │  /api/search/    │  │  Permission      │                  │
│  │  /auth/me        │  │  /api/graph/     │  └──────────────────┘                  │
│  └──────────────────┘  │  /api/upload/    │                                       │
│                         │  /api/build/     │                                       │
│                         └──────────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                        │
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                   核心应用层                                         │
│                                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐           │
│  │                       多 Agent 问答系统                               │           │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐              │           │
│  │  │Coordinator│→│Researcher│→│  Writer  │→│ Reviewer │              │           │
│  │  │意图识别   │  │多源检索   │  │报告生成   │  │质量审核   │              │           │
│  │  │任务分解   │  │Wiki/图谱 │  │          │  │分类反馈   │              │           │
│  │  │多轮修复   │  │向量/原文 │  │          │  │审计日志   │              │           │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘              │           │
│  └──────────────────────────────────────────────────────────────────────┘           │
│                                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐           │
│  │                     编译管道                                           │           │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐              │           │
│  │  │Document  │→│Novel-    │→│Wiki      │→│Knowledge │              │           │
│  │  │Router    │  │Chunker   │  │Compiler  │  │Graph     │              │           │
│  │  │多格式解析 │  │四级分块   │  │LLM提取   │  │NetworkX  │              │           │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘              │           │
│  └──────────────────────────────────────────────────────────────────────┘           │
│                                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐           │
│  │                     公共基础设施                                       │           │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │           │
│  │  │ LLM 调用  │  │ 语义缓存  │  │ 会话记忆  │  │ 跨书检索 │  │生态导出 │ │           │
│  │  │ DeepSeek │  │ Embedding│  │ Session  │  │倒排索引  │  │Obsidian│ │           │
│  │  │ 降级策略  │  │ 相似>0.85│  │ 关联历史  │  │多书共查  │  │EPUB/CSV│ │           │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └────────┘ │           │
│  └──────────────────────────────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                        │
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                  数据存储层                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │ 文件系统  │  │ ChromaDB │  │ JSON DB  │  │ Neo4j    │  │ Redis    │             │
│  │ processed │  │ 向量索引  │  │ 用户/日志 │  │ (预留)   │  │ (预留)   │             │
│  │ wiki/graph│  │          │  │          │  │          │  │          │             │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘             │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、完整数据流程图

### 2.1 上传 → 解析 → 编译

```
用户上传 .txt/.docx/.pdf/.md 文件
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  DocumentRouter (策略模式)                             │
│                                                       │
│  1. detect_encoding()  → UTF-8 / GBK / GB18030        │
│                                                       │
│  2. Parser.parse()  → 统一输出格式:                    │
│     {                                                  │
│       "title": "小说名",                                │
│       "chapters": [                                    │
│         {"title": "第一章", "text": "..."},             │
│         ...                                            │
│       ],                                               │
│       "metadata": {"format": "txt", "chars_total": N}   │
│     }                                                  │
│                                                       │
│  3. extract_chapters()  → 章节边界检测                  │
│     正则: 第X章 / Chapter N / 楔子/序章/尾声            │
│                                                       │
│  4. _is_impurity_line()  → 过滤杂质                    │
│     起点网址 / 求月票 / 作者的话 / QQ群 等14种模式       │
└───────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  保存 Processed JSON                                   │
│                                                        │
│  data/processed/{书名}.json                            │
│  {                                                     │
│    "title": "绍宋",                                    │
│    "chapters": [{"title": "第一章 明道宫", "text": "..."}, │
│                 ... 438 chapters total]                 │
│  }                                                     │
└───────────────────────────────────────────────────────┘
        │
        ▼ (用户点击"开始编译")
┌─────────────────────────────────────────────────────────────────────────────┐
│  Wiki Compilation Pipeline                                                   │
│                                                                              │
│  Unit of work: single chapter                                                │
│                                                                              │
│  For each chapter i (0..N):                                                  │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │  TokenBudget.assert_available()  → 超预算则终止                   │        │
│  │  CheckpointManager.is_completed(i)  → 已编译则跳过                │        │
│  │                                                                   │        │
│  │  if len(ch_text) ≤ 2800 chars:                                   │        │
│  │    └─ _parse_single_chunk() → LLM 提取一次                        │        │
│  │                                                                   │        │
│  │  else:  # 长章节 → 语义分块 + 并发提取 + LLM 合并                  │        │
│  │    chunks = _semantic_split(text, max_chars=2800)                 │        │
│  │    max 8 sub-chunks                                              │        │
│  │    ┌──────────────────────────────────────────────────┐          │        │
│  │    │  ThreadPoolExecutor(workers=3)                    │          │        │
│  │    │  for each chunk: send to LLM in parallel          │          │        │
│  │    │  each chunk → {summary, characters, events, rels} │          │        │
│  │    │  each result → SubChunkCache(mark_completed)      │          │        │
│  │    └──────────────────────────────────────────────────┘          │        │
│  │    merged = _llm_merge_chunks()  # 统一称谓，消弭冲突             │        │
│  │    if LLM fails: _simple_merge() # 去重+拼接回退                  │        │
│  │                                                                   │        │
│  │  CheckpointManager.mark_completed(i)                              │        │
│  │  atomic_write(wiki_entries, wiki_path)  # 每章落盘                 │        │
│  └─────────────────────────────────────────────────────────────────┘        │
│                                                                              │
│  After all chapters:                                                         │
│  → build_volume_summaries()    # LLM 生成每卷摘要 (自动检测自然卷)            │
│  → build_book_summary()        # LLM 生成全书摘要                            │
│  → save_hierarchical_wiki()    # 保存三层结构                                │
│  → merge_characters()          # 跨章合并人物实体                            │
│  → merge_relationships()       # 跨章合并关系，权重累加                       │
│  → build_graph()               # NetworkX 图谱                              │
│  → save_graph()                # JSON 序列化                                 │
│  → MultiBookIndex.build()      # 更新跨书倒排索引                            │
│                                                                              │
│  产物:                                                                       │
│    data/wiki/{novel}_hierarchical.json  # 三层 Wiki                          │
│    data/wiki/{novel}_graph.json         # 知识图谱 (662 nodes, 1682 edges)   │
│    data/wiki/{novel}_alias.json         # 别名映射表                          │
│    data/index/inverted_index.json       # 跨书倒排索引                        │
└─────────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  向量索引                                               │
│                                                        │
│  NovelChuner.chunk_novel(novel_data)                    │
│  → 四级分块 (book/volume/chapter/paragraph)             │
│  → 只索引 paragraph 级到 ChromaDB (batch 500)           │
│  → 存储: data/chroma/chroma.sqlite3 (96MB)              │
└───────────────────────────────────────────────────────┘
```

### 2.2 问答流程

```
用户提问: "赵玖和岳飞是什么关系？"
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  1. SemanticCache.get(query)                           │
│                                                        │
│  embedder = ChromaDB DefaultEmbeddingFunction()         │
│  query_vec = embedder.embed(query)                     │
│                                                        │
│  for entry in cache_entries:                           │
│    score = cosine_similarity(query_vec, entry.vec)     │
│    if score ≥ 0.85: return entry.answer  ← 秒回       │
│                                                        │
│  → 未命中，继续走 Agent 流程                            │
└───────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  2. SessionMemory.search(query)                        │
│                                                        │
│  for entry in index:                                   │
│    if query 中的关键词匹配 entry.key_entities:          │
│      构建增强 query: query + \n【历史摘要】+ summary    │
│                                                        │
│  → 增强后的 query 送 Coordinator                       │
└───────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Coordinator.run(augmented_query)                                       │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Step 1: detect_intent(query)                                     │    │
│  │  → LLM 分类: character | relationship | summary | complex | other │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                │                                       │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Step 2: decompose_task(query, intent)                           │    │
│  │  → LLM 拆解为 2-4 个检索步骤:                                    │    │
│  │    [{step: 1, description: "在Wiki搜索「赵玖」信息"},            │    │
│  │     {step: 2, description: "在知识图谱查找赵玖-岳飞关系"},        │    │
│  │     {step: 3, description: "在原文核实两人交集"}]                │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                │                                       │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Step 3: Multi-round loop (max 5 rounds)                        │    │
│  │                                                                  │    │
│  │  for round_num in range(max_rounds):                            │    │
│  │    ┌───────────────────────────────────────────────────────┐     │    │
│  │    │  3a. Researcher.execute(each incomplete step)          │     │    │
│  │    │      desc_lower → route to search method:             │     │    │
│  │    │      "向量"/"语义" → _search_vector(query)            │     │    │
│  │    │      "Wiki"/"章节"  → _search_wiki(query)             │     │    │
│  │    │      "图谱"/"关系"  → _search_graph(query)            │     │    │
│  │    │      "原文"        → _search_original(query)          │     │    │
│  │    │      其他          → _search_all(query)               │     │    │
│  │    │                                                       │     │    │
│  │    │      _search_wiki = Wiki全文匹配 + 自动附加向量检索      │     │    │
│  │    │      _search_vector = 原问题 + 实体对扩展检索           │     │    │
│  │    │      _search_original = 章节号提取 → 标题匹配 → 向量   │     │    │
│  │    │      _search_graph = 节点匹配 → 邻接关系                 │     │    │
│  │    │                                                       │     │    │
│  │    │      返回: 结构化的文本材料                             │     │    │
│  │    └───────────────────────────────────────────────────────┘     │    │
│  │                                │                                 │    │
│  │    ┌───────────────────────────────────────────────────────┐     │    │
│  │    │  3b. MaterialPool.add_round(materials)                 │     │    │
│  │    │      → 第1轮: 全量保留                                 │     │    │
│  │    │      → 第2轮+: LLM 压缩旧材料为 200字摘要               │     │    │
│  │    │      → get_effective() → 全局摘要 + 最近2轮全量         │     │    │
│  │    └───────────────────────────────────────────────────────┘     │    │
│  │                                │                                 │    │
│  │    ┌───────────────────────────────────────────────────────┐     │    │
│  │    │  3c. Writer.write(query, intent, materials)            │     │    │
│  │    │      → 格式化材料为 prompt                             │     │    │
│  │    │      → LLM 生成 500-800 字分析报告                     │     │    │
│  │    └───────────────────────────────────────────────────────┘     │    │
│  │                                │                                 │    │
│  │    ┌───────────────────────────────────────────────────────┐     │    │
│  │    │  3d. Reviewer.review(draft, query)                     │     │    │
│  │    │      → LLM 从4个维度审核:                               │     │    │
│  │    │        passed? / score(0-10) / failure_type / feedback │     │    │
│  │    │      → audit_log() 双写审计: DB + data/logs/audit.log  │     │    │
│  │    │                                                        │     │    │
│  │    │      if passed: return result                          │     │    │
│  │    │                                                        │     │    │
│  │    │      if not passed:                                    │     │    │
│  │    │        _handle_review_failure(steps, review):          │     │    │
│  │    │          evidence_missing → 按 retrieval_hints 补充检索 │     │    │
│  │    │          alias_conflict  → 图谱核实实体关系             │     │    │
│  │    │          hallucination   → 原文事实验证步骤             │     │    │
│  │    │          irrelevant     → 重新分解任务                  │     │    │
│  │    │          other          → 通用兜底                     │     │    │
│  │    │        continue  # 进入下一轮                           │     │    │
│  │    └───────────────────────────────────────────────────────┘     │    │
│  │                                                                  │    │
│  │  end for                                                        │    │
│  │                                                                  │    │
│  │  返回: {query, intent, final_report, review_result, rounds,      │    │
│  │         needs_manual_review ? }                                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────┐
│  4. 后处理                                               │
│                                                        │
│  → SemanticCache.put(query, report)   ← 缓存回答        │
│  → SessionMemory.save_summary(...)    ← 保存历史摘要     │
│  → 返回 JSON 给前端                                     │
│  → SSE 流式: 每步推送 progress event                     │
└───────────────────────────────────────────────────────┘
```

---

## 三、伪代码

### 3.1 上传编译流程

```python
# ── upload_and_compile.txt 上传 → 编译 → 图谱 ──

async def upload_and_compile(filename: str, file_content: bytes):
    # ── Step 1: 文件校验 ──
    validation = FileValidator.validate(filename, file_content)
    if not validation["valid"]:
        raise HTTPException(400, validation["error"])

    # ── Step 2: 文档解析 ──
    router = get_router()  # DocumentRouter 单例
    ext = os.path.splitext(filename)[1].lower()

    # 按扩展名自动路由到对应解析器
    if ext == ".txt":    parser = TxtParser()
    elif ext == ".docx": parser = DocxParser()
    elif ext == ".pdf":  parser = PdfParser()
    elif ext == ".md":   parser = MarkdownParser()
    else: raise UnsupportedFormatError(ext)

    result = parser.parse(filepath)
    # result = {
    #   "title": str,                    # 小说名
    #   "chapters": [                    # 章节列表
    #     {"title": "第一章", "text": "..."},
    #     ...,
    #   ],
    #   "metadata": {"format": "txt", "chars_total": 100000},
    # }

    # ── Step 3: 保存为 Processed JSON ──
    processed_path = f"data/processed/{filename.replace(ext, '.json')}"
    novel_data = {"title": result["title"], "chapters": result["chapters"]}
    with open(processed_path, "w", encoding="utf-8") as f:
        json.dump(novel_data, f, ensure_ascii=False, indent=2)

    # ── Step 4: 可选——预分块 ──
    chunker = NovelChunker(chunk_size=512, overlap=128)
    chunks = chunker.chunk_novel(novel_data, novel_key=title)

    return {
        "filename": filename,
        "total_chapters": len(chapters),
        "total_chunks": len(chunks),
        "message": f"解析完成，{len(chapters)}章，{len(chunks)}个语义块"
    }


# ── 后台编译任务 ──

def run_build(novel_name: str, processed_path: str):
    """
    后台异步执行，前端通过 /build/progress 轮询进度。
    """
    # 加载 processed JSON
    with open(processed_path, "r", encoding="utf-8") as f:
        novel = json.load(f)

    chapters = novel["chapters"]
    budget = TokenBudget(max_tokens=2_000_000)
    cpm = CheckpointManager(novel_name)
    failed_mgr = FailedChaptersManager(novel_name)
    wiki_entries = []

    # ── Phase 1: 逐章 Wiki 编译 ──
    for i, ch in enumerate(chapters):
        if budget.exhausted: break
        if cpm.is_completed(i, "wiki"): continue  # 断点跳过

        ch_title = ch["title"]
        ch_text = ch["text"]
        print(f"  [{i}/{len(chapters)}] {ch_title}...")

        try:
            # 长章节自动分块
            if len(ch_text) > 2800:
                entry = parse_long_chapter(ch_title, ch_text, i,
                                           novel_name, budget)
            else:
                entry = parse_short_chapter(ch_title, ch_text,
                                           chapter_index=i, budget=budget)
            # entry = {summary, characters, events, relationships}
            failed_mgr.remove(i)

        except Exception as e:
            print(f"  失败 [{i}]: {e}")
            failed_mgr.add(i, ch_title, str(e))
            entry = {"summary": "", "characters": [],
                     "events": [], "relationships": []}

        entry["chapter_index"] = i
        entry["chapter_title"] = ch_title
        wiki_entries.append(entry)
        cpm.mark_completed(i, "wiki")
        atomic_write(wiki_entries, checkpoint_path)  # 每章落盘

    # ── Phase 2: 卷摘要 ──
    volumes = build_volume_summaries(wiki_entries, novel_key=novel_name)
    # volumes = [{title, summary, main_characters, chapter_range}, ...]

    # ── Phase 3: 全书摘要 ──
    book = build_book_summary(wiki_entries, volumes, novel_key=novel_name)
    # book = {summary, main_characters, themes}

    # ── Phase 4: 保存三层 Wiki ──
    save_hierarchical_wiki(wiki_entries, volumes, book,
                           f"data/wiki/{novel_name}_hierarchical.json")

    # ── Phase 5: 图谱构建 ──
    alias_map = build_alias_map(wiki_entries)          # 别名消歧
    char_map  = merge_characters(wiki_entries, alias_map)  # 人物合并
    rels      = merge_relationships(wiki_entries, alias_map) # 关系合并
    G = build_graph(char_map, rels)                    # NetworkX 图
    save_graph(G, f"data/wiki/{novel_name}_graph.json")

    # ── Phase 6: 向量索引 ──
    chunker = NovelChunker(chunk_size=512, overlap=128)
    all_chunks = chunker.chunk_novel(novel, novel_key=novel_name)
    indexer = VectorStoreIndexer()
    indexer.index_novel(novel_name, all_chunks)  # → ChromaDB

    # ── Phase 7: 更新跨书索引 ──
    MultiBookIndex(rebuild=True)
```

### 3.2 长章节语义分块 + 并发提取

```python
# ── parse_long_chapter.py 长章节 → 分块 → 并发 LLM → 合并 ──

def parse_long_chapter(title: str, text: str, chapter_index: int,
                       novel_key: str, budget: TokenBudget) -> dict:
    """
    超长章节处理流程：
    1. 语义分块（段落边界感知，不打断对话）
    2. 子块级断点检查（崩溃恢复）
    3. 并发 LLM 提取（max 3 workers）
    4. LLM 合并规整（统一称谓，消弭冲突）
    """
    MAX_CHARS = 2800
    MAX_SUB_CHUNKS = 8

    # Step 1: 语义分块
    chunks = semantic_split(text, max_chars=MAX_CHARS)
    # [chunk_text_1, chunk_text_2, ...]

    if len(chunks) > MAX_SUB_CHUNKS:
        # 超限块合并到最后一块
        extra = "\n\n".join(chunks[MAX_SUB_CHUNKS - 1:])
        chunks = chunks[:MAX_SUB_CHUNKS - 1] + [extra]

    # Step 2: 子块级断点
    cache = SubChunkCache(novel_key)
    incomplete = cache.get_incomplete(chapter_index, len(chunks))

    if len(incomplete) == len(chunks):
        cache.init_chapter(chapter_index, len(chunks))
    else:
        print(f"  子块断点: {len(chunks)-len(incomplete)}/{len(chunks)} 块已缓存")

    results = cache.get_cached_results(chapter_index)

    # Step 3: 只跑未完成的子块
    if incomplete:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(extract_single_chunk,
                    f"{title}[{i+1}/{len(chunks)}]", chunks[i]): i
                for i in incomplete
            }

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    result = future.result()
                    # result = {summary, characters, events, relationships}
                    cache.mark_subchunk_done(chapter_index, idx, result)
                    budget.record(len(chunks[idx]) // 2)
                except Exception as e:
                    cache.mark_subchunk_done(chapter_index, idx,
                        {"summary": "", "characters": [],
                         "events": [], "relationships": []})

        results = cache.get_cached_results(chapter_index)

    # Step 4: LLM 合并规整
    valid = [r for r in results if r and r.get("characters")]
    if not valid:
        cache.cleanup(chapter_index)
        return {"summary": title, "characters": [],
                "events": [], "relationships": []}

    merged = llm_merge_chunks(title, valid)
    # merged = {
    #   "summary": "合并后的摘要（150-200字）",
    #   "characters": [
    #     {"name": "赵玖", "role": "主角",
    #      "description": "...", "aliases": ["官家", "赵构"]},
    #   ],
    #   "events": ["斩杀内侍康履", "八公山抗金诏书", ...],
    #   "relationships": [
    #     {"source": "赵玖", "target": "岳飞", "relation": "君臣"},
    #   ],
    # }

    cache.cleanup(chapter_index)
    budget.record(500)
    return merged


def extract_single_chunk(chunk_title: str, chunk_text: str) -> dict:
    """单子块 LLM 提取"""
    prompt = CHAPTER_WIKI_PROMPT.format(
        chapter_title=chunk_title,
        chapter_text=chunk_text,
    )
    # CHAPTER_WIKI_PROMPT 要求 LLM 输出:
    # {summary, characters[{name, role, description, aliases}],
    #  events[], relationships[{source, target, relation}]}

    response = call_llm_with_retry([{"role": "user", "content": prompt}])
    # 指数退避重试: max 3次, 处理 429/5xx

    if response:
        data = parse_llm_json(response)
        if data and "summary" in data:
            # 实体校验: 过滤幻觉（空名/纯符号/纯英文）
            data["characters"] = [
                c for c in data.get("characters", [])
                if validate_entity(c)
            ]
            data["relationships"] = [
                r for r in data.get("relationships", [])
                if validate_relationship(r)
            ]
            return data

    return {"summary": "", "characters": [],
            "events": [], "relationships": []}


def llm_merge_chunks(title: str, chunk_results: list[dict]) -> dict:
    """多块结果合并 —— LLM 规整"""
    prompt = MERGE_PROMPT.format(
        chunks_data=json.dumps(chunk_results, ensure_ascii=False)
    )
    # MERGE_PROMPT 要求:
    # 1. 统一同名人物（跨块用不同称谓的合并）
    # 2. 消弭冲突（关系描述不一致的取权重高者）
    # 3. aliases 去重合并
    # 4. 事件按时间/逻辑排序
    # 5. 生成统一摘要

    response = call_llm_with_retry([{"role": "user", "content": prompt}])
    if response:
        data = parse_llm_json(response)
        if data and "summary" in data:
            return data

    # LLM 失败时回退到规则合并
    return simple_merge(chunk_results)
```

### 3.3 问答流程（多 Agent 核心）

```python
# ── multi_agent_qa.py Coordinator 驱动多 Agent 问答 ──

class Coordinator:
    """
    协调员：意图识别 → 任务分解 → 多轮执行 → 汇总输出
    """

    def __init__(self, researcher, writer, reviewer):
        self.researcher = researcher  # 多源检索
        self.writer = writer          # 报告撰写
        self.reviewer = reviewer      # 质量审核

    def run(self, query: str, max_rounds=5) -> dict:
        """
        完整 Agent 流程。

        Args:
            query: 用户问题，如"赵玖和岳飞是什么关系？"
            max_rounds: 最大修复轮数

        Returns:
            {query, intent, final_report, review_result, rounds}
        """
        # ── Step 1: 意图识别 ──
        # LLM 将问题分类，指导后续检索策略
        intent = self.detect_intent(query)
        # "赵玖和岳飞是什么关系？" → "relationship"

        # ── Step 2: 任务分解 ──
        # LLM 拆解为可执行的检索步骤
        steps = self.decompose_task(query, intent)
        # [
        #   {"step": 1, "description": "在知识图谱查找赵玖-岳飞关系"},
        #   {"step": 2, "description": "在Wiki搜索两人章节摘要"},
        #   {"step": 3, "description": "向量搜索原文中两人交集"},
        # ]

        # ── Step 3: 多轮执行 ──
        pool = MaterialPool(llm_compress=True, max_rounds=3)
        all_materials = []
        completed_steps = set()

        for round_num in range(max_rounds):
            print(f"--- Round {round_num + 1} ---")

            # 3a. 执行未完成的检索步骤
            round_materials = []
            for step in steps:
                if step["step"] in completed_steps:
                    continue

                desc = step["description"]
                print(f"  Researcher: {desc}")

                # Researcher 自动路由到正确的检索方法
                result = self.researcher.execute(desc, query, intent)
                # 返回: 结构化的检索结果文本

                round_materials.append({
                    "step": step["step"],
                    "description": desc,
                    "result": result,
                })
                completed_steps.add(step["step"])

            all_materials.extend(round_materials)

            # 3b. 材料池压缩（自动压缩旧轮次为摘要）
            pool.add_round(round_materials)
            effective_text = pool.get_effective()

            # 3c. Writer 生成报告
            draft = self.writer.write(query, intent, [
                {"step": 0, "description": "汇总资料",
                 "result": effective_text}
            ])
            # 返回: 500-800 字分析报告（Markdown）

            # 3d. Reviewer 审核
            review = self.reviewer.review(draft, query)
            # 返回: {passed, score, failure_type, feedback,
            #        suggestions, missing_entities, retrieval_hints}

            # 记录审计日志（双写: DB + text file）
            audit_log(
                action="review",
                user="system",
                resource=query[:120],
                detail=f"Round {round_num+1}/{max_rounds} | "
                       f"Type: {review.get('failure_type', 'none')} | "
                       f"Score: {review.get('score', 0)}",
                status="success" if review["passed"] else "failure",
                failure_type=review.get("failure_type", ""),
            )

            if review["passed"]:
                return {
                    "query": query,
                    "intent": intent,
                    "final_report": draft,
                    "review_result": review,
                    "rounds": round_num + 1,
                }

            # 审核不通过 → 分类修复
            print(f"  审核未通过: {review['failure_type']}")
            new_steps = self.handle_review_failure(steps, review)
            if new_steps:
                steps.extend(new_steps)
                continue  # 进入下轮重试

            break  # 无法修复

        # 所有轮次用完仍未通过
        return {
            "query": query,
            "intent": intent,
            "final_report": draft,
            "review_result": review,
            "rounds": max_rounds,
            "needs_manual_review": True,  # 标记需人工复核
        }

    def handle_review_failure(self, old_steps, review):
        """
        根据失败类型生成针对性补充步骤。
        """
        ft = review.get("failure_type", "other")
        next_step = max(s["step"] for s in old_steps) + 1
        new_steps = []

        if ft == "evidence_missing":
            # 证据不足 → 用 retrieval_hints 定向补充
            for i, hint in enumerate(review.get("retrieval_hints", [])):
                new_steps.append({
                    "step": next_step + i,
                    "description": f"补充检索: {hint}",
                })

        elif ft == "alias_conflict":
            # 别名冲突 → 知识图谱核实
            for i, entity in enumerate(review.get("missing_entities", [])):
                new_steps.append({
                    "step": next_step + i,
                    "description": f"核实关系: 查询「{entity}」的所有关系",
                })

        elif ft == "hallucination":
            # 幻觉 → 原文验证
            new_steps.append({
                "step": next_step,
                "description": "原文验证: 基于原文核实报告中的每条结论",
            })

        elif ft == "irrelevant":
            # 偏离问题 → 通用反馈
            pass  # 让 _refine_plan 兜底

        # 兜底: LLM 根据反馈生成补充步骤
        if not new_steps:
            new_steps = self.refine_plan(old_steps, review["feedback"])

        return new_steps


class Researcher:
    """研究员：根据任务描述自动选择检索数据源"""

    def execute(self, description: str, query: str, intent: str) -> str:
        desc_lower = description.lower()

        # 关键词路由
        if "向量" in desc_lower or "语义" in desc_lower:
            return self.search_vector(query)
        elif "wiki" in desc_lower or "章节" in desc_lower or "摘要" in desc_lower:
            return self.search_wiki(query)
        elif "图谱" in desc_lower or "关系" in desc_lower:
            return self.search_graph(query)
        elif "原文" in desc_lower or "细节" in desc_lower:
            return self.search_original(query)
        else:
            return self.search_all(query)  # 全量搜索

    def search_vector(self, query: str) -> str:
        """
        多查询扩展向量检索:
        1. 原问题检索 top_k=15
        2. 实体对组合检索 top_k=8 each
        3. 按章节去重合并 → top_k=25
        """
        results = []

        # 1. 原问题检索
        r1 = retriever.search_by_vector(query, top_k=15)
        seen_chapters = set()
        for r in r1:
            ch = r.get("metadata", {}).get("chapter_title", "")
            if ch and ch not in seen_chapters:
                seen_chapters.add(ch)
                results.append(r)

        # 2. 实体对扩展检索
        entities = extract_entities(query)
        for i in range(len(entities)):
            for j in range(i+1, len(entities)):
                pair_query = f"{entities[i]} {entities[j]}"
                pair_results = retriever.search_by_vector(pair_query, top_k=8)
                for r in pair_results:
                    ch = r.get("metadata", {}).get("chapter_title", "")
                    if ch and ch not in seen_chapters:
                        seen_chapters.add(ch)
                        results.append(r)

        # 格式化输出
        lines = ["【向量检索结果】"]
        for r in results[:25]:
            text = r.get("text", "")[:400]
            ch_title = r.get("metadata", {}).get("chapter_title", "")
            lines.append(f"  📄 [{ch_title}]")
            lines.append(f"    {text}")
        return "\n".join(lines)

    def search_wiki(self, query: str) -> str:
        """Wiki 检索（含自动附加向量检索）"""
        results = retriever.search_wiki(query, top_k=40)

        lines = ["【Wiki 检索结果】"]
        # 全书摘要
        if retriever.book and retriever.book.get("summary"):
            lines.append(retriever.book["summary"][:500])

        # 卷摘要
        for vol in retriever.volumes:
            if any(w in query for w in vol.get("summary", "")):
                lines.append(f"  {vol['title']}: {vol['summary'][:200]}")

        # 章节摘要
        seen = set()
        for w in results:
            title = w.get("chapter_title", "")
            if title in seen: continue
            seen.add(title)
            lines.append(f"  章节：{title}")
            lines.append(f"  摘要：{w.get('summary', '')}")
            chars = "、".join(c["name"] for c in w.get("characters", [])[:5])
            if chars:
                lines.append(f"  人物：{chars}")

        # + 向量检索补充
        vec = self.search_vector(query)
        if "未找到" not in vec:
            lines.append(vec)

        return "\n".join(lines)


class Writer:
    """撰稿人：基于检索资料生成分析报告"""

    def write(self, query, intent, materials):
        formatted = []
        for m in materials:
            if m["result"]:
                formatted.append(
                    f"【步骤{m['step']}】{m['description']}\n{m['result']}"
                )

        prompt = f"""
你是一个网文分析专家。请根据资料写一份分析报告。

用户问题：{query}
分析类型：{intent}

资料：
{"\n\n".join(formatted)}

要求：
1. 结构清晰，使用标题和小标题
2. 引用标注来源（如「引自第一章」）
3. 严格基于资料，不编造
4. 500-800字
"""
        return call_llm([{"role": "user", "content": prompt}])


class Reviewer:
    """审核员：检查报告质量，返回结构化审核结果"""

    def review(self, report, query):
        if not report or len(report) < 50:
            return {"passed": False, "score": 0,
                    "failure_type": "evidence_missing",
                    "feedback": "报告为空或太短"}

        prompt = f"""
严格审核以下分析报告：

问题：{query}
报告：{report}

从4方面审核：
1. 是否回答了问题？
2. 每条结论有原文依据？（无→hallucination）
3. 关系准确？（写反→alias_conflict）
4. 结构清晰？

以JSON返回：
{{"passed": bool, "score": 0-10,
  "failure_type": "evidence_missing"|"alias_conflict"|"hallucination"|"irrelevant"|"other",
  "feedback": str,
  "suggestions": [str],
  "missing_entities": [str],
  "retrieval_hints": [str]}}
"""
        response = call_llm([{"role": "user", "content": prompt}],
                            response_format={"type": "json_object"})
        return ensure_review_fields(parse_review_json(response))
```

### 3.4 Web 层（FastAPI 路由）

```python
# ── api_routes.py 核心 API 路由 ──

@router.post("/ask")
async def ask_agent(body: AgentQueryRequest):
    """多 Agent 问答（含语义缓存 + 会话记忆）"""

    # 1. 语义缓存查询
    cache = get_semantic_cache()
    cached = cache.get(body.query)
    if cached is not None:
        return {"report": cached, "cached": True}

    # 2. 会话记忆增强
    related = memory.search(body.query)
    augmented_query = body.query
    if related:
        summaries = [r["summary"][:200] for r in related if r.get("summary")]
        augmented_query = f"{body.query}\n\n【相关历史】\n{''.join(summaries)}"

    # 3. 初始化 Agent
    retriever = NovelRetriever(WIKI_PATH, GRAPH_PATH, NOVEL_PATH)
    researcher = Researcher(retriever)
    writer = Writer()
    reviewer = Reviewer()
    coordinator = Coordinator(researcher, writer, reviewer)

    # 4. 运行多 Agent 问答
    result = coordinator.run(augmented_query)

    # 5. 缓存结果
    report = result.get("final_report", "")
    if report:
        cache.put(body.query, report)

    return {
        "report": report,
        "rounds": result.get("rounds", 0),
        "intent": result.get("intent", ""),
        "session_id": session_id,
    }


@router.post("/ask/stream")
async def ask_agent_stream(body: AgentQueryRequest):
    """SSE 流式问答（推送进度事件）"""
    async def event_stream():
        yield json.dumps({"event": "start", "message": "开始分析..."})

        coordinator = Coordinator(
            Researcher(NovelRetriever(...)),
            Writer(), Reviewer()
        )

        yield json.dumps({"event": "progress",
                          "message": "意图识别中..."})
        intent = coordinator.detect_intent(body.query)

        yield json.dumps({"event": "progress",
                          "message": f"任务拆解中...（{intent}）"})
        steps = coordinator.decompose_task(body.query, intent)

        yield json.dumps({"event": "progress",
                          "message": f"共{len(steps)}个检索步骤，执行中..."})
        result = coordinator.run(body.query)

        yield json.dumps({"event": "result", "report": result["final_report"]})
        yield "data: [DONE]\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

---

## 四、数据存储汇总

```
data/
├── raw/                              # 上传原文件
│   └── 《绍宋》作者：榴弹怕水.txt
│
├── processed/                        # 解析后的结构化 JSON
│   ├── 《绍宋》作者：榴弹怕水.json         # {title, chapters[]}
│   └── 《绍宋》作者：榴弹怕水_chunks.json  # 预分块结果
│
├── wiki/                             # 编译产物
│   ├── 绍宋作者：榴弹怕水_hierarchical.json  # 三层 Wiki（全书+卷+章节）
│   ├── 绍宋作者：榴弹怕水_graph.json         # 知识图谱（662节点/1682边）
│   └── 绍宋作者：榴弹怕水_alias.json         # 别名映射
│
├── checkpoints/                      # 编译断点
│   └── 绍宋作者：榴弹怕水_wiki_checkpoint.json  # {completed_indices: [...]}
│
├── chroma/chroma.sqlite3             # 向量数据库（96MB, ~5万段落块）
│
├── cache/semantic_cache.json         # 语义缓存
│
├── index/inverted_index.json         # 跨书倒排索引
│
├── logs/
│   ├── app_2026-07-09.log            # 应用日志
│   └── audit.log                     # 审计日志（按行 JSON）
│
├── eval/golden/shaosong.json         # 黄金测试集（27条 QA）
│
└── memory/index.json                 # 会话记忆索引
```

---

## 五、关键数字

| 指标 | 数值 |
|------|------|
| 支持的小说数 | 3 部 (绍宋438章/斗破苍穹/神印王座) |
| 知识图谱节点 | 662 人物 |
| 知识图谱边 | 1682 关系 |
| 向量库规模 | ~5万段落块 (96MB ChromaDB) |
| 黄金测试集 | 27条 QA |
| Hybrid Recall@5 | 81% |
| Docker 服务数 | 6 |
| 单元测试数 | 11 |
| Agent 轮次上限 | 5 轮 |
| Token 预算/书 | 200万 |
| LLM 调用并发 | 3 workers |
| 语义缓存阈值 | cosine > 0.85 |
| 审计日志 | 双写 (DB + text file) |
