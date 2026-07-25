# 网文 GraphRAG + 多 Agent 协作分析系统 · 完全项目文档

> Novel GraphRAG + Multi-Agent Analysis System  
> 生成时间: 2026-07-09  
> 版本: 0.2.0

---

## 目录

1. [项目概览](#1-项目概览)
2. [技术栈](#2-技术栈)
3. [系统架构](#3-系统架构)
4. [数据处理管道](#4-数据处理管道)
5. [核心模块详解](#5-核心模块详解)
   - 5.1 LLM 调用层 (`core/llm.py`)
   - 5.2 文档解析引擎 (`core/document_parser.py`)
   - 5.3 层级分块引擎 (`core/chunker.py`)
   - 5.4 Wiki 编译管道 (`core/chapter_parser.py`)
   - 5.5 编译配置 (`core/compiler_config.py`)
   - 5.6 章节分级路由 (`core/chapter_router.py`)
   - 5.7 知识图谱 (`core/knowledge_graph.py`)
   - 5.8 三级检索引擎 (`core/retriever.py`)
   - 5.9 跨书全文检索 (`core/multi_book_search.py`)
   - 5.10 语义缓存 (`core/semantic_cache.py`)
   - 5.11 多 Agent 系统 (`core/agents/`)
   - 5.12 分段式材料管理 (`core/material_pool.py`)
   - 5.13 安全中间件 (`core/security.py`)
   - 5.14 数据库抽象层 (`core/db/`)
   - 5.15 会话记忆系统 (`core/memory.py`)
   - 5.16 生态导出模块 (`core/exporter.py`)
   - 5.17 异步编译管道 (`core/pipeline.py`)
   - 5.18 评估指标 (`interfaces/eval_metrics.py`)
6. [Web 层](#6-web-层)
7. [MCP Server](#7-mcp-server)
8. [配置文件](#8-配置文件)
9. [部署方案](#9-部署方案)
10. [数据文件结构](#10-数据文件结构)
11. [API 参考](#11-api-参考)
12. [测试](#12-测试)
13. [Roadmap](#13-roadmap)

---

## 1. 项目概览

### 一句话

上传网络小说 TXT / Word / PDF / Markdown 文件 → 自动清洗解析 → LLM 逐章编译结构化 Wiki → 构建人物关系图谱 → 多 Agent 协作回答分析问题 → 支持图文导出。

### 核心能力

| 能力 | 说明 |
|------|------|
| **多格式上传** | TXT / DOCX / PDF / MD，自动编码检测、章节边界识别、杂质过滤 |
| **Wiki 编译** | LLM 逐章提取摘要/人物/事件/关系，长章自动分块 + 子块级断点 + 增量编译 + Token 预算 |
| **知识图谱** | NetworkX 构建，Cytoscape.js 渲染，自适应布局，编辑模式（删除/合并/修改关系） |
| **三级检索** | Wiki 摘要层 → 知识图谱层 → 向量语义层（ChromaDB），多查询扩展 |
| **多 Agent 问答** | Coordinator → Researcher（多源检索）→ Writer（报告生成）→ Reviewer（质量审核）+ 审计日志 |
| **语义缓存** | Embedding 相似度 > 0.85 秒回，减少 LLM 调用 |
| **跨书检索** | 倒排索引，多书共查 |
| **生态导出** | Obsidian 知识库、EPUB 电子书、Excel/CSV 清单、Markdown 报告 |
| **权限系统** | JWT 4 角色（admin/editor/viewer/api），路由级鉴权 |
| **MCP Server** | 5 个 Tool 暴露给 Cursor / Claude Desktop |

---

## 2. 技术栈

| 层 | 技术 |
|----|------|
| **后端框架** | Python 3.10+, FastAPI, Uvicorn |
| **前端** | Jinja2 模板, Tailwind CSS, Cytoscape.js, Font Awesome |
| **LLM** | DeepSeek Chat（通过 OpenAI SDK 兼容接口），支持多模型降级 |
| **向量数据库** | ChromaDB（内置 ONNX embedding） |
| **知识图谱** | NetworkX（构建）→ Cytoscape.js（渲染）/ Neo4j（预留） |
| **认证** | JWT (HS256), 4 级角色权限, BaseHTTPMiddleware |
| **数据库** | 抽象层：JSON 文件（当前）/ MySQL（预留） |
| **MCP** | `mcp` Python SDK，stdio 传输 |
| **部署** | Docker Compose（6 服务）, Nginx 反向代理 |
| **文档解析** | python-docx（.docx）、pdfplumber（.pdf） |

---

## 3. 系统架构

### 3.1 整体流程

```
用户上传/提问
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  上传管道                                                │
│  TXT/DOCX/PDF/MD → 章节解析 → 编码检测 → 杂质过滤       │
│  → 层级分块 → ChromaDB 索引                             │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  编译管道                                                │
│  → LLM 逐章提取（语义分块 + 并发 + 子块断点）            │
│  → 卷摘要 (LLM) → 全书摘要 (LLM)                        │
│  → 别名消歧 → 图谱构建 (NetworkX)                       │
│  → 跨书倒排索引                                         │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  问答管道 (多 Agent)                                     │
│  Coordinator (意图识别 → 任务分解)                       │
│    ├→ Researcher (Wiki + 图谱 + 向量 + 原文检索)         │
│    ├→ Writer (报告生成)                                 │
│    └→ Reviewer (质量审核 + 审计日志)                     │
│  MaterialPool (上下文压缩)                               │
│  SemanticCache (相似问题秒回)                            │
└──────────────────────────────────────────────────────────┘
```

### 3.2 目录结构

```
novel-graphrag/
├── README.md                    # 项目文档（中英双语）
├── PROJECT_OVERVIEW.md          # 本文档
├── DEVLOG.md                    # 开发日志
├── config.yaml                  # 全局配置（支持环境变量覆盖）
├── requirements.txt             # Python 依赖
├── .env / .env.template         # 环境变量
│
├── Dockerfile                   # Docker 构建
├── docker-compose.yml           # Docker 编排（6 服务）
├── nginx.conf                   # Nginx 反向代理
│
├── SQL/schema.sql               # MySQL 建表语句（预留）
│
├── data/                        # 根数据目录
│   ├── logs/                    #   应用日志 + 审计日志
│   ├── raw/                     #   上传的原始文件
│   ├── processed/               #   清洗后的 JSON
│   ├── wiki/                    #   编译产物（Wiki + 图谱 + 别名）
│   ├── checkpoints/             #   编译断点
│   ├── cache/                   #   语义缓存
│   ├── chroma/                  #   ChromaDB 持久化
│   ├── index/                   #   跨书检索倒排索引
│   ├── memory/                  #   会话记忆索引
│   ├── eval/                    #   评估黄金数据集
│   ├── vocab/                   #   词汇表
│   └── pipeline/                #   编译管道状态
│
└── novel_project/               # 主 Python 包
    ├── run.py                   # 启动入口
    ├── mcp_server.py            # MCP Server
    │
    ├── core/
    │   ├── __init__.py
    │   ├── llm.py               # LLM 调用层
    │   ├── document_parser.py   # 文档解析（TXT/DOCX/PDF/MD）
    │   ├── chunker.py           # 层级分块引擎 + ChromaDB 索引
    │   ├── chapter_parser.py    # Wiki 编译 v2（核心管线）
    │   ├── compiler_config.py   # 编译参数配置
    │   ├── chapter_router.py    # 分级路由 + SimHash/MinHash 去重
    │   ├── knowledge_graph.py   # 图谱构建 + 别名消歧
    │   ├── retriever.py         # 三级检索
    │   ├── multi_book_search.py # 跨书倒排索引
    │   ├── semantic_cache.py    # 语义缓存
    │   ├── material_pool.py     # 分段式材料管理
    │   ├── memory.py            # 会话记忆
    │   ├── security.py          # JWT/权限/限流/审计日志/文件校验
    │   ├── exporter.py          # 生态导出（Obsidian/EPUB/Excel/MD）
    │   ├── pipeline.py          # 编译管道进度管理
    │   ├── slang_map.json       # 谐音黑话归一化
    │   │
    │   ├── agents/
    │   │   ├── coordinator.py   # 协调员（意图识别 + 任务分解 + 多轮审核）
    │   │   ├── researcher.py    # 研究员（多源检索）
    │   │   ├── writer.py        # 撰稿人（报告生成）
    │   │   ├── structured_writer.py  # 结构化输出
    │   │   └── reviewer.py      # 审核员（质量检查 + 分类反馈）
    │   │
    │   └── db/
    │       ├── __init__.py      # get_db() / set_db()
    │       ├── interface.py     # DBBackend 抽象接口
    │       ├── models.py        # UserModel / AuditLogModel
    │       ├── json_backend.py  # JSON 文件实现
    │       └── mysql_backend.py # MySQL 桩（待实现）
    │
    ├── interfaces/
    │   ├── eval_metrics.py      # RAG 评估指标（Faithfulness）
    │   ├── llm_provider.py      # LLM 多 Provider 接口（预留）
    │   ├── graph_storage.py     # 图谱存储接口（预留）
    │   ├── copyright_verifier.py # 版权校验（预留）
    │   ├── portrait_generator.py # 角色立绘（预留）
    │   └── rlhf_pipeline.py     # RLHF 管线（预留）
    │
    ├── web/
    │   ├── app.py               # FastAPI 主入口
    │   ├── routes/
    │   │   ├── agent_routes.py  # 问答/检索/上传/编译/图谱编辑 API
    │   │   └── auth_routes.py   # 登录/注册/权限 API
    │   ├── templates/
    │   │   ├── dashboard.html   # 仪表盘首页
    │   │   ├── graph.html       # 图谱可视化
    │   │   ├── chat.html        # 问答交互
    │   │   ├── upload.html      # 上传页
    │   │   └── login.html       # 登录页
    │   └── static/
    │       ├── js/              # cytoscape.min.js, vis-network.min.js
    │       └── css/             # vis-network.min.css
    │
    ├── scripts/                 # 工具脚本
    │   ├── clean_novel.py       # 文本清洗
    │   ├── test_agents.py       # Agent 系统测试
    │   ├── test_build_graph.py  # 图谱构建测试
    │   ├── test_retriever.py    # 检索测试
    │   ├── rag_evaluate.py      # RAG 评估
    │   ├── migrate_to_neo4j.py  # Neo4j 迁移
    │   ├── backup.py            # 数据备份
    │   └── run_night_build.py   # 夜间编译
    │
    └── tests/
        ├── test_core.py         # 核心模块单元测试（11 个用例）
        └── test_mcp_server.py   # MCP Server 测试
```

---

## 4. 数据处理管道

### 4.1 上传管道流程

```
上传 TXT/DOCX/PDF/MD
    │
    ▼
┌──────────────────────────────┐
│  DocumentRouter               │
│  策略模式: 按扩展名选择解析器 │
│  - TxtParser                  │
│  - DocxParser                 │
│  - PdfParser                  │
│  - MarkdownParser             │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  章节边界检测 (extract_chapters)│
│  正则匹配: "第X章" / Chapter N│
│  / 楔子/序章/尾声/后记/番外    │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  杂质过滤 (_is_impurity_line)  │
│  - 起点/笔趣阁网址广告         │
│  - 求收藏/求月票模板          │
│  - 作者有话说                 │
│  - 书友群/公众号              │
│  - QQ 群                     │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  保存 processed JSON + 分块  │
└──────────────────────────────┘
```

### 4.2 编译管道流程

```
processed JSON
    │
    ▼
┌──────────────────────────────────────────────────┐
│  编译管道 (build_wiki)  — chapter_parser.py      │
│                                                   │
│  for 每章:                                        │
│    ├ 短章 → 单次 LLM 提取                          │
│    ├ 长章 → 语义分块 (≤2800字/块, ≤8块/章)         │
│    │      → 并发多块提取 (ThreadPool, ≤3并发)       │
│    │      → LLM 合并规整（消弭冲突、统一称谓）       │
│    └ 子块级断点 (SubChunkCache)                   │
│                                                   │
│  → 卷摘要 (build_volume_summaries)                │
│  → 全书摘要 (build_book_summary)                  │
│  → 别名映射表 (alias_mapping)                     │
│  → 图谱构建 (build_graph)                         │
└──────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  保存产物:                     │
│  - {novel}_hierarchical.json  │
│  - {novel}_graph.json         │
│  - {novel}_alias.json         │
└──────────────────────────────┘
```

### 4.3 问答管道流程

```
用户问题
    │
    ▼
┌──────────────────────────────┐
│  语义缓存 (SemanticCache)     │
│  Embedding → 余弦相似度 >0.85 │
│  → 命中直接返回               │
└──────────────────────────────┘
    │ 未命中
    ▼
┌──────────────────────────────┐
│  会话记忆 (SessionMemory)     │
│  检索相关历史对话             │
└──────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  Coordinator.run()                                   │
│                                                      │
│  1. detect_intent → 意图识别                         │
│     (character / relationship / summary / complex)    │
│                                                      │
│  2. decompose_task → 拆解为 2-4 个检索步骤            │
│                                                      │
│  3. 多轮执行 (max 5 轮):                              │
│     for 每轮:                                        │
│       for 每步: Researcer.execute()                   │
│                → Wiki / 图谱 / 原文 / 向量检索         │
│       → MaterialPool.add_round()  # 压缩旧材料        │
│       → Writer.write()  # 生成报告                    │
│       → Reviewer.review()  # 质量审核                 │
│       → 审核不通过 → _handle_review_failure()         │
│         (按证据不足/别名冲突/幻觉 分类处理 + 审计日志)  │
│                                                      │
│  4. 返回 final_report                                │
└──────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  写入语义缓存 → 保存会话摘要  │
└──────────────────────────────┘
```

---

## 5. 核心模块详解

### 5.1 LLM 调用层 (`core/llm.py`)

**职责:** 封装 OpenAI SDK 兼容的 API 调用。

```
call_llm(messages, temperature=0.7, max_tokens=4096, model=None, response_format=None) → str
call_llm_stream(messages, ...) → yield str
```

- 默认 provider: DeepSeek（`deepseek-v4-pro`）
- 支持 `response_format={"type": "json_object"}` 强制 JSON 输出
- API Key 未配置时自动回退为模拟响应（返回友好提示）
- `_fallback_response`: 当 API 不可用时生成降级响应

**环境变量:**
- `DEEPSEEK_API_KEY` — DeepSeek API 密钥
- `LLM_MODEL` — 模型名（默认 `deepseek-v4-pro`）
- `LLM_BASE_URL` — API 地址（默认 `https://api.deepseek.com`）

---

### 5.2 文档解析引擎 (`core/document_parser.py`)

**职责:** 统一的文档解析入口，策略模式注册多格式解析器。

**架构:**

```
DocumentRouter (路由器) → 按扩展名分发
    ├── TxtParser        (.txt)      — 自动编码检测 UTF-8/GBK/GB18030
    ├── DocxParser       (.docx)     — Heading 样式检测章节
    ├── PdfParser        (.pdf)      — 文字层检测 + 段落重组
    └── MarkdownParser   (.md/.markdown) — # 标题转章节
```

**统一输出格式:**
```python
{
    "title": str,
    "chapters": [{"title": str, "text": str}, ...],
    "metadata": {"format": str, "chars_total": int, ...}
}
```

**章节检测正则 (`CHAPTER_PATTERN`):**
```python
r"^(?:第[一-鿿\d]+[章回节部集]"
r"|[一二三四五六七八九十百千万]+[章回节部集]"
r"|楔子|序章|尾声|后记|番外"
r"|Chapter\s+\d+|CHAPTER\s+\d+)"
```

**杂质过滤 (`_is_impurity_line`):** 匹配起点网址/求收藏/求月票/作者有话说/QQ群等 14 种杂质模式。

---

### 5.3 层级分块引擎 (`core/chunker.py`)

**职责:** 将小说按 全书 → 卷 → 章 → 段落 四级结构进行语义分块。

**核心类:**

| 类 | 职责 |
|----|------|
| `Chunk` | 分块数据模型（chunk_id/level/text/token_estimate 等） |
| `NovelChunker` | 四级分块引擎（滑窗 + 重叠 + 段落边界感知） |
| `VectorStoreIndexer` | 将分块写入 ChromaDB 并支持语义检索 |

**分块策略:**
- 窗口大小: 512 tokens（可配），重叠 128 tokens
- Token 估算: 中文 1.3 chars/token，英文 4 chars/token
- 段落边界感知: 不打断 `\n\n` 分隔的完整段落
- 对话边界标记: 不打断 `「」` `""` 内的对话
- 长段落二次分割: 按句号/问号/感叹号拆分
- 尾部小块合并: `< 64 tokens` 的碎片块与前一块合并

**Chunk 四级结构:**
```
Level 0 - 全书摘要  (book)      → 1 块
Level 1 - 卷摘要    (volume)    → 每卷 1 块
Level 2 - 章节摘要   (chapter)   → 每章 1 块
Level 3 - 原文段落   (paragraph) → 每章 N 块（滑窗 + 重叠）
```

**向量索引 (ChromaDB):**
- 只索引 paragraph 级别块（不索引摘要类块）
- HNSW 空间: cosine
- 批量写入（每批 500 条）
- 嵌入模型: ChromaDB 内置 ONNX embedding（零网络依赖）

---

### 5.4 Wiki 编译管道 (`core/chapter_parser.py`)

**职责:** 将清洗后的小说逐章编译为结构化 Wiki 条目（核心管线）。

这是项目中**最复杂**的模块（约 1474 行），历经 v1→v2 重构。

#### 核心函数

| 函数 | 职责 |
|------|------|
| `build_wiki()` | 整书编译入口，支持全量/增量/断点/暂停 |
| `parse_chapter()` | 单章编译：短章直出，长章分块+并发+合并 |
| `build_volume_summaries()` | 用 LLM 生成卷摘要（自动检测自然卷） |
| `build_book_summary()` | 用 LLM 生成全书摘要 |
| `save_hierarchical_wiki()` | 保存三层 Wiki（原子写入+备份） |
| `load_wiki()` | 加载 Wiki（兼容新旧格式） |

#### 长章节处理流程

```
长文本（>2800 chars）
    │
    ▼
┌──────────────────────────────┐
│  _semantic_split()            │
│  按段落边界切割，每块 ≤2800 字 │
│  最多 8 块，超出的合并到最后一块│
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  ThreadPoolExecutor (≤3 并发) │
│  每块独立调用 LLM 提取:       │
│  LLM → {summary, characters, │
│         events, relationships}│
│  每块完成后落地 SubChunkCache │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  LLM 合并规整:                │
│  - 同一人物跨块统一称谓        │
│  - 消弭关系冲突（朋友 vs 敌人）│
│  - aliases 合并去重           │
│  - 事件按时间顺序排列          │
│  - 生成统一摘要               │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  LLM 失败 → _simple_merge     │
│  基于规则的合并（去重+拼接）   │
└──────────────────────────────┘
```

#### 关键设计

| 设计 | 说明 |
|------|------|
| **子块级断点** | 超长章节每子块结果落地到独立 JSON 文件，崩溃只重跑未完成块 |
| **子块缓存** | `SubChunkCache` 存储每块的 LLM 提取结果 |
| **增量编译** | `build_wiki_incremental()` 只处理新增/修改章节，自动更新对应卷摘要 |
| **失败隔离** | `FailedChaptersManager` 独立记录失败章节，不阻塞流程，支持单章重试 |
| **Token 预算** | `TokenBudget` 全局计数器，到达上限自动终止（默认 200 万 tokens/书） |
| **原子写入** | 临时文件 → `os.replace`，杜绝半残文件 |
| **自动备份** | 每次保存前备份，保留最近 3 个时间戳版本 |
| **暂停恢复** | 支持外部暂停信号，暂停时落盘当前进度，恢复后继续 |
| **实体校验** | `_validate_entity()` 过滤幻觉实体（空名/纯符号/纯英文无意义名） |
| **关系校验** | `_validate_relationship()` 过滤自指/空名关系 |
| **并发控制** | `max_concurrency = 3`，可配置 |

#### Prompt 模板

- `CHAPTER_WIKI_PROMPT` — 单章提取 prompt，要求输出 `summary`/`characters`/`events`/`relationships`
- `MERGE_PROMPT` — 合并 prompt，要求统一称谓、消弭冲突、去重

---

### 5.5 编译配置 (`core/compiler_config.py`)

**职责:** 集中管理所有编译管道的可调参数。

**默认配置:**
```python
{
    "chunk_max_chars": 2800,         # 单块最大字符数
    "max_sub_chunks_per_chapter": 8, # 单章最大分块数
    "max_retries": 3,                # LLM 重试次数
    "retry_base_delay": 2.0,         # 重试基础延迟（秒）
    "max_concurrency": 3,            # 最大并发数
    "max_tokens_per_book": 2000000,  # Token 预算
    "volume_size": 50,               # 每卷章节数
    "batch_size": 5,                 # 每批章节数
    "batch_delay": 1.0,              # 批次间隔
    "summary_compress_threshold": 500,
    "summary_compress_target": 200,
    "entity_min_mention": 2,
}
```

---

### 5.6 章节分级路由 (`core/chapter_router.py`)

**职责:** 根据章节复杂度动态分配模型等级（PRO/LIGHT/SKIP），配合 SimHash + MinHash 去重。

**架构:**

```
ChapterRouter — 复杂度分级
    └── analyze() → entity_density + dialogue_ratio + novelty_score
         └── route() → ModelTier.PRO / LIGHT / SKIP

SimHashDeduplicator — 粗筛
    └── 64-bit SimHash, hamming <= 3 → 重复

MinHashDeduplicator — 二次确认
    └── 128-perm MinHash, Jaccard > 0.85 → 重复

TextDeduplicator — 组合去重器
    └── SimHash 粗筛 → MinHash 确认

SmartChapterPipeline — 集成管线
    └── calibrate() → 前 N 章校准阈值 → process() → 逐章处理
```

**分级标准:**
```python
score = 0.6 * entity_density  + 0.3 * dialogue_ratio + 0.1 * paragraph_count
# PRO:   score >= 0.6
# LIGHT: 0.2 < score < 0.6
# SKIP:  score <= 0.2
# 前 10 章自动校准该书专属阈值
```

---

### 5.7 知识图谱 (`core/knowledge_graph.py`)

**职责:** 将 Wiki 条目中的人物实体跨章节合并，构建人物关系网络。

**核心流程:**

```
Wiki 条目列表
    │
    ▼
┌──────────────────────────────┐
│  _build_alias_map()           │
│  规则:                        │
│  1. 包含关系（"萧炎"∈"萧炎小子"）│
│  2. 共现+同role               │
│  3. 同姓氏+低频合并到高频       │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  merge_characters()           │
│  跨章合并:                    │
│  {规范名: {出场章节, 描述,    │
│            提及次数}}          │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  merge_relationships()        │
│  跨章合并:                    │
│  [(source, target, relation,  │
│    权重, 章节列表)]            │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  build_graph() → NetworkX    │
│  节点: {name, role,          │
│         mention_count,        │
│         chapter_count}        │
│  边:   {source, target,       │
│         relation, weight,     │
│         chapters}              │
└──────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  save_graph() → JSON         │
│  load_graph() ← JSON         │
└──────────────────────────────┘
```

---

### 5.8 三级检索引擎 (`core/retriever.py`)

**职责:** 实现 Wiki → 知识图谱 → 向量语义 三级递进检索。

**核心类: `NovelRetriever`**

| 方法 | 级别 | 说明 |
|------|------|------|
| `search_wiki(query)` | 1 | 检索 Wiki 摘要（全书→卷→章节三级优先） |
| `search_by_graph(query)` | 2 | 检索知识图谱（节点匹配 + 邻接关系） |
| `search_by_vector(query)` | 3 | 向量语义检索（ChromaDB 语义匹配） |
| `search(query)` | 统一 | 三级合一 |

**动态 top_k 算法 (`_dynamic_top_k`):**
- "前N章" → `max(default, N)`
- "第X章到第Y章" → `max(default, Y-X+1)`
- "全书/整本" → 返回所有
- 其他 → 默认值（5）

**Wiki 检索优先级:**
```
全书摘要 > 卷摘要 > 章节摘要
```
匹配算法 (`_match_entry`): 标题(×10) + 摘要(×5) + 人物(×10) + 事件(×5) 加权打分。

**向量检索（增强版）:**
```
原问题检索 (top_k=15)
  + 实体对扩展检索 (top_k=8 each)
→ 章节去重合并 (top_k=25)
```

---

### 5.9 跨书全文检索 (`core/multi_book_search.py`)

**职责:** 为多本书建立倒排索引，支持跨书共查。

**索引结构:**
```json
{
    "keyword": [
        {"book": "绍宋作者：榴弹怕水", "chapter_index": 12,
         "chapter_title": "第十二章", "summary": "..."},
        ...
    ]
}
```

**关键词提取 (`_extract_keywords`):**
- 从章节标题/摘要/人物名/事件中提取 2-4 字组合
- 全小写 + 去重

**搜索:**
- 关键词拆分为多词 → 各自匹配 → 按命中数求和 → 倒序输出 top_k
- 支持按书目标过滤

---

### 5.10 语义缓存 (`core/semantic_cache.py`)

**职责:** 对用户提问做语义相似度匹配，相同/相似问题秒回。

**原理:**
```
用户提问 → Embedding (ChromaDB Default ONNX)
         → 与缓存条目逐一算余弦相似度
         → 最高分 > 0.85 → 直接返回缓存回答
         → 否则 → 走完整 LLM 链路 → result 入库
```

**核心类: `SemanticCache`**

| 方法 | 说明 |
|------|------|
| `get(query)` | 查缓存，命中返回 answer |
| `put(query, answer)` | 写入缓存（语义相似则更新，否则新增） |
| `stats()` | 统计信息（条目数/命中率/热门问题） |
| `clear()` | 清空缓存 |

**淘汰策略:** 缓存上限 2000 条，超限时按命中次数排序淘汰低频条目。

**集成到问答:**
```python
answer = cached_ask("赵玖是谁？", lambda: coordinator.run("赵玖是谁？"))
```

---

### 5.11 多 Agent 系统 (`core/agents/`)

#### 架构

```
Coordinator (协调员)
    ├── detect_intent(query)      → character/relationship/summary/complex
    ├── decompose_task(query)     → [step1, step2, step3, step4]
    ├── run(query)                → 主循环
    │     ├── Researcher.execute()  → 多源检索
    │     ├── Writer.write()        → 报告生成
    │     ├── Reviewer.review()     → 质量审核
    │     └── _handle_review_failure() → 分类修复
    └── _refine_plan()            → 补充检索步骤
```

#### Coordinator (`agents/coordinator.py`)

**完整流程:**

| 步骤 | 方法 | 说明 |
|------|------|------|
| 1 | `detect_intent()` | LLM 分类为 character/relationship/summary/complex/other |
| 2 | `decompose_task()` | LLM 拆解为 2-4 个 Researcher 检索步骤 |
| 3 | 多轮循环 | 每轮: 执行未完成步骤 → MaterialPool 压缩 → Writer 生成 → Reviewer 审核 |
| 4 | `_handle_review_failure()` | 按失败类型分类处理 + 审计日志 |

**审核失败分类处理 (新增于 2026-07-09 修复):**

| 失败类型 | 检测条件 | 自动修复 |
|----------|----------|----------|
| `evidence_missing` | `retrieval_hints` 或 `missing_entities` 非空 | 按 hints + missing 定向补充检索步骤 |
| `alias_conflict` | `missing_entities` 非空 | 对冲突实体发起知识图谱关系查询 |
| `hallucination` | failure_type 为 hallucination | 新增「原文验证」步骤，基于原文核实 |
| `irrelevant` | failure_type 为 irrelevant | 通过 `_refine_plan` 重新分解任务 |
| `other` | 兜底 | 通用反馈处理 |

**审计日志 (2026-07-09 新增):**
每轮审核结果（通过/失败 + 类型 + 分数 + 反馈）写入审计日志：
- DB 层: `data/logs/audit.json`
- 文本层: `data/logs/audit.log`

#### Researcher (`agents/researcher.py`)

**职责:** 根据 Coordinator 分配的任务，选择合适的数据源检索。

**路由逻辑:**
```python
if "向量" in desc:     → _search_vector(query)     # 语义向量检索
elif "Wiki" in desc:   → _search_wiki(query)        # Wiki + 自动附加向量检索
elif "图谱" in desc:   → _search_graph(query)       # 知识图谱检索
elif "原文" in desc:   → _search_original(query)    # 原文关键词 + 向量兜底
else:                  → _search_all(query)         # 全量搜索
```

**向量检索增强:** 原问题搜索 + 实体对扩展搜索（两两组合），结果按章节去重合并。
**Wiki 检索增强:** Wiki 结果 + 自动附加向量全文检索。
**原文检索:** 章节号提取 → 章节标题匹配 → 向量兜底，三级递进。

#### Writer (`agents/writer.py`)

**职责:** 基于 Researcher 收集的资料撰写分析报告。

- Prompt 要求: 结构清晰、引用标注来源、严格基于资料、500-800 字
- 输入格式: `[{step, description, result}, ...]`

#### Reviewer (`agents/reviewer.py`)

**职责:** 检查 Writer 报告质量，返回结构化审核结果。

**审核维度:**
1. 是否回答用户问题（偏离主题 → 不通过）
2. 每条结论是否有原文依据（编造 → hallucination）
3. 人物关系描述是否准确（张冠李戴 → alias_conflict）
4. 结构是否清晰、来源是否标注

**返回格式:**
```python
{
    "passed": bool,
    "score": 0-10,
    "failure_type": "evidence_missing" | "alias_conflict" | "hallucination"
                    | "irrelevant" | "other",
    "feedback": str,
    "suggestions": [str],
    "missing_entities": [str],
    "retrieval_hints": [str],
}
```

---

### 5.12 分段式材料管理 (`core/material_pool.py`)

**职责:** 解决多轮问答中上下文不断膨胀的问题。

**策略:**

| 轮次 | 策略 |
|------|------|
| 第 1 轮 | 所有材料完整保留 |
| 第 2 轮起 | 旧材料用 LLM 摘要压缩到约 1/5 |
| 始终 | 保留最近 2 轮全量 + 压缩后的历史摘要 |

**核心类: `MaterialPool`**

| 方法 | 说明 |
|------|------|
| `add_round(materials)` | 添加一轮材料，自动 LLM 压缩 + 更新全局摘要 |
| `get_effective()` | 获取有效材料文本（全局摘要 + 最近 2 轮全量） |
| `get_prompt_for_writer(query, intent)` | 一键生成 Writer 完整 prompt |

**压缩算法:**
- `_summarize()`: 200 字以内的简洁摘要（3000 chars → LLM → 200 字）
- `_summarize_short()`: 50 字以内的极简摘要（1500 chars → LLM → 50 字）

---

### 5.13 安全中间件 (`core/security.py`)

**职责:** 认证、授权、限流、文件校验、审计日志。

| 组件 | 说明 |
|------|------|
| `JWTHandler` | JWT 签发/验证，HS256，72h 过期，密钥持久化 |
| `PermissionMiddleware` | FastAPI 中间件，拦截非公开路径，验证 Bearer Token |
| `require_role(min_role)` | 路由级权限装饰器（admin/editor/viewer/api） |
| `require_user_owns_resource()` | 资源归属校验（预留） |
| `FileValidator` | 上传文件校验链：扩展名→MIME→安全名→大小→编码 |
| `TokenBucket` | 令牌桶限流器（60 req/min, burst 100） |
| `RateLimitMiddleware` | FastAPI 限流中间件（用户→IP→接口三级 key） |
| `SanitizeLogFilter` | 日志脱敏（过滤 API Key/密码/Token） |
| `audit_log()` | **审计日志 双写** (2026-07-09 增强) |

**角色层级:**
```python
ROLE_HIERARCHY = {"admin": 100, "editor": 50, "viewer": 10, "api": 5}
```

**权限映射 (`auth_routes.py`):**
```python
page:dashboard  → level ≥ 5   (所有角色)
page:graph      → level ≥ 5
page:chat       → level ≥ 5
page:upload     → level ≥ 50  (editor/admin)
action:build    → level ≥ 50
action:delete   → level ≥ 50
action:admin:*  → level ≥ 100 (admin only)
```

**审计日志 (2026-07-09 增强):**
- 新增 `failure_type` 参数
- **双写策略:** DB (audit.json) + 文本文件 (audit.log)
- 文本文件按行 JSON，支持 `tail -f` 实时监控
- 日志路径: `data/logs/audit.log`

---

### 5.14 数据库抽象层 (`core/db/`)

**架构:**

```
get_db() → DBBackend 接口
    ↓
┌─────────────────────┐
│  JSON 后端 (当前)    │  ← JsonBackend
│  MySQL 后端 (预留)   │  ← MySQLBackend (桩)
└─────────────────────┘
```

**数据模型:**

| 模型 | 字段 |
|------|------|
| `UserModel` | id/username/password_hash/role/created_at/is_active |
| `AuditLogModel` | id/action/username/resource/detail/status/ip/created_at |

**JSON 后端存储路径:**
- `data/users.json` — 用户表
- `data/logs/audit.json` — 审计日志（最多保留 10000 条）

**MySQL 迁移接口** — `MySQLBackend` 已完成桩代码，实现后即可无缝切换。

---

### 5.15 会话记忆系统 (`core/memory.py`)

**职责:** 跨对话检索相关内容，实现多轮上下文关联。

**存储结构:**
```json
// data/memory/index.json
[{
    "session_id": "20260101_120000",
    "title": "赵玖的性格分析",
    "summary": "...",
    "key_entities": ["赵玖", "绍宋"],
    "message_count": 3,
    "is_active": true
}]
```

**检索:** 当前问题与历史会话的 key_entities + summary 做关键词匹配。

---

### 5.16 生态导出模块 (`core/exporter.py`)

**职责:** 将编译产物导出为多种生态格式。

**支持的导出格式:**

| 格式 | 方法 | 说明 |
|------|------|------|
| Obsidian 知识库 | `export_obsidian()` | 双向链接 Markdown，人物/卷/章节分级 |
| EPUB 电子书 | `export_epub()` | 需要 ebooklib，降级为 HTML |
| Excel 人物清单 | `export_excel()` | 需要 openpyxl，降级为 CSV |
| Markdown 报告 | `export_markdown_report()` | 全书分析报告 |
| HTML | `export_html()` | EPUB 降级方案 |
| CSV | `export_csv()` | Excel 降级方案 |

**Obsidian 导出结构:**
```
output/
├── 全书总览.md      # 全书概要 + 主要人物 + 主题 + 卷链接
├── 人物/
│   ├── 赵玖.md       # 角色信息 + 关系网络 + 出场章节
│   └── ...
├── 卷/
│   └── 第1-50章.md
└── 章节/
    └── 第一章 明道宫.md
```

---

### 5.17 异步编译管道 (`core/pipeline.py`)

**职责:** 管理编译任务状态，支持前端轮询进度。

**状态文件:** `data/pipeline/{novel_name}.json`，记录 `status/phase/completed_chapters` 等字段。

---

### 5.18 RAG 评估指标 (`interfaces/eval_metrics.py`)

**职责:** 评估 RAG 系统回答质量。

**核心指标: Faithfulness (忠实度)**

- 使用 LLM-as-Judge 评估回答中的每条结论是否有原文依据
- 阈值 0.75，低于此值阻断发布
- 黄金测试集 27 条 QA（覆盖全书/卷/人物关系三类）
- 作为回归门禁：Prompt 变更或模型切换后自动验证

---

## 6. Web 层

### 6.1 FastAPI 主入口 (`web/app.py`)

- 配置加载（`config.yaml` + 环境变量替换）
- 静态文件挂载（`/static`）
- 注册路由: `/api/...` — agent_routes + auth_routes
- 注册中间件: `PermissionMiddleware`（JWT 验证）
- 页面路由: `/` → dashboard, `/graph` → 图谱, `/chat` → 问答, `/upload` → 上传, `/login` → 登录
- 健康检查: `GET /health`

### 6.2 页面

| 页面 | 路由 | 功能 |
|------|------|------|
| 仪表盘 | `/` | 系统状态、书籍列表、快速入口 |
| 图谱 | `/graph` | Cytoscape.js 知识图谱可视化 |
| 问答 | `/chat` | 多 Agent 问答交互（SSE 流式输出） |
| 上传 | `/upload` | 文档上传 + 编译控制 |
| 登录 | `/login` | JWT 登录 |

### 6.3 图谱编辑功能

| 操作 | API | 说明 |
|------|-----|------|
| 删除关系 | `POST /api/graph/edit/delete-edge` | 删除指定 source→target 的边 |
| 合并节点 | `POST /api/graph/edit/merge-nodes` | 将 source 合并到 target |
| 修改关系 | `POST /api/graph/edit/update-relation` | 修改关系描述 |
| 权限检查 | `GET /api/graph/edit/check` | 检查当前用户编辑权限 |

所有修改操作均有 JWT 鉴权（仅 admin/editor）和自动备份。

### 6.4 图谱自适应布局

后端根据节点数动态计算 Cytoscape.js 布局参数:

| 参数 | 计算公式 |
|------|----------|
| 节点斥力 | `min(100000 * (n/100)², 3000000)` |
| 重力 | `max(0.003, min(0.08, 0.04 * (100/n)^0.4))` |
| 理想边长 | `min(100 + √n * 6, 300)` |
| 迭代次数 | `min(2000 + n * 2, 3000)` |
| 节点尺寸 | `max(8, min(40, 55 - n * 0.02))` |
| 标签显隐 | `max(0.05, 0.15 - n * 0.00008)` |

---

## 7. MCP Server (`mcp_server.py`)

**职责:** 通过 MCP 协议暴露网文知识图谱数据给 Cursor / Claude Desktop 等客户端。

**暴露的 5 个 Tool:**

| Tool | 参数 | 功能 |
|------|------|------|
| `list_novels` | 无 | 列出所有已编译的小说 |
| `search_novel_graph` | `novel`, `character`, `include_relations` | 搜索人物和关系网络 |
| `get_character_timeline` | `novel`, `character` | 追踪人物全书出场时间线 |
| `analyze_chapter` | `novel`, `chapter` | 获取单章结构化分析 |
| `search_wiki` | `novel`, `query`, `top_k` | 全文搜索 Wiki 条目 |

**传输:** stdio（MCP 标准协议）
**启动:** `python mcp_server.py`

---

## 8. 配置文件

### 8.1 `config.yaml` — 全局配置

| 部分 | 关键配置 |
|------|----------|
| `app` | name/version/debug/host/port |
| `llm` | primary(provider/model/api_key/base_url/timeout), fallback, budget |
| `auth` | jwt_secret/algorithm/expire_hours/roles |
| `db` | backend(json/mysql), mysql connection |
| `storage` | neo4j/chromadb/redis |
| `security` | upload_allowed/rate_limit/cors |
| `logs` | level/format/rotation/retention/audit_log_path |
| `compilation` | batch_size/delay/chapter_router/dedup |
| `eval` | faithfulness_threshold/golden_dataset_path |

### 8.2 `.env` — 环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（必需） |
| `LLM_MODEL` | 模型名（默认 deepseek-v4-pro） |
| `LLM_BASE_URL` | API 地址 |
| `JWT_SECRET` | JWT 签名密钥 |
| `DB_HOST/PORT/USER/PASSWORD/NAME` | MySQL 连接（可选） |
| `NEO4J_URI/PASSWORD` | Neo4j 连接（可选） |
| `REDIS_URL` | Redis 连接（可选） |

---

## 9. 部署方案

### 9.1 本地开发

```bash
cd novel_project
python run.py
# → http://localhost:8000
```

### 9.2 Docker Compose（6 服务）

```yaml
services:
  app:          # FastAPI 主应用
  chromadb:     # 向量数据库
  neo4j:        # 知识图谱
  redis:        # 缓存
  nginx:        # 反向代理
  mysql:        # 关系数据库（可选）
```

---

## 10. 数据文件结构

```
data/
├── logs/
│   ├── app_2026-07-09.log          # 应用日志（按日滚动）
│   └── audit.log                   # 审计日志（按行 JSON）
│
├── raw/                            # 上传的原始文件
│   ├── 《斗破苍穹》作者：天蚕土豆.txt
│   ├── 《神印王座》作者：唐家三少.txt
│   └── 《绍宋》作者：榴弹怕水.txt
│
├── processed/                      # 清洗后的 JSON
│   ├── 《斗破苍穹》作者：天蚕土豆.json
│   ├── 《神印王座》作者：唐家三少.json
│   └── 《绍宋》作者：榴弹怕水.json
│
├── wiki/                           # 编译产物
│   ├── shaosong_hierarchical.json  #   三层 Wiki（全书+卷+章节）
│   ├── shaosong_graph.json         #   知识图谱（节点+边）
│   ├── shaosong_alias.json         #   别名映射表
│   ├── shaosong_wiki.json          #   旧格式（兼容）
│   ├── 斗破苍穹_hierarchical.json
│   ├── 斗破苍穹_graph.json
│   ├── 神印王座_hierarchical.json
│   └── 神印王座_graph.json
│
├── checkpoints/                    # 编译断点
│   └── {novel}_{phase}_checkpoint.json
│
├── cache/
│   └── semantic_cache.json         # 语义缓存
│
├── chroma/                         # ChromaDB 持久化（SQLite + HNSW）
│
├── index/
│   └── inverted_index.json         # 跨书倒排索引
│
├── memory/
│   └── index.json                  # 会话记忆索引
│
├── eval/
│   └── golden/{novel}.json         # 黄金测试集
│
├── vocab/                          # 词汇表
│   └── domain_dict.json            # 网文领域词表（实体消歧用）
│
└── pipeline/                       # 编译管道状态
    └── {novel}.json
```

---

## 11. API 参考

### 11.1 认证

| Method | Path | Auth | 说明 |
|--------|------|------|------|
| POST | `/api/auth/login` | 无 | 登录，返回 JWT Token |
| POST | `/api/auth/register` | 无 | 注册新用户 |
| GET | `/api/auth/me` | Bearer | 当前用户信息 + 权限列表 |
| GET | `/api/auth/users` | admin | 用户列表 |

### 11.2 问答

| Method | Path | Auth | 说明 |
|--------|------|------|------|
| POST | `/api/ask` | Bearer | 多 Agent 问答（含语义缓存） |
| POST | `/api/ask/stream` | Bearer | SSE 流式问答（含进度事件） |

### 11.3 检索

| Method | Path | Auth | 说明 |
|--------|------|------|------|
| POST | `/api/search` | Bearer | Wiki + 图谱检索 |
| POST | `/api/search/vector` | Bearer | 语义向量检索 |
| POST | `/api/search/all` | Bearer | 三级统一检索 |
| POST | `/api/search/multi` | Bearer | 跨书全文检索 |
| GET | `/api/search/multi/books` | Bearer | 跨书索引书籍列表 |
| GET | `/api/chapter?keyword=` | Bearer | 原文检索 |

### 11.4 图谱

| Method | Path | Auth | 说明 |
|--------|------|------|------|
| GET | `/api/graph?novel=` | Bearer | 图谱数据 + 自适应布局参数 |
| GET | `/api/novels` | Bearer | 可用书籍列表 |
| POST | `/api/graph/edit/delete-edge` | editor/admin | 删除关系 |
| POST | `/api/graph/edit/merge-nodes` | editor/admin | 合并人物节点 |
| POST | `/api/graph/edit/update-relation` | editor/admin | 修改关系描述 |
| GET | `/api/graph/edit/check` | Bearer | 编辑权限检查 |

### 11.5 上传与编译

| Method | Path | Auth | 说明 |
|--------|------|------|------|
| POST | `/api/upload` | editor/admin | 上传文档 |
| POST | `/api/build` | editor/admin | 开始编译（后台异步） |
| GET | `/api/build/progress?novel=` | Bearer | 编译进度 |
| GET | `/api/build/failed?novel=` | Bearer | 失败章节清单 |
| POST | `/api/build/retry` | editor/admin | 重试失败章节 |
| POST | `/api/build/pause` | editor/admin | 暂停编译 |
| POST | `/api/build/resume` | editor/admin | 恢复编译 |
| POST | `/api/index` | editor/admin | 索引到向量库 |

### 11.6 缓存管理

| Method | Path | Auth | 说明 |
|--------|------|------|------|
| GET | `/api/cache/stats` | Bearer | 语义缓存统计 |
| POST | `/api/cache/clear` | admin | 清空语义缓存 |

### 11.7 系统

| Method | Path | Auth | 说明 |
|--------|------|------|------|
| GET | `/health` | 无 | 健康检查 |

---

## 12. 测试

### 12.1 单元测试

位置: `novel_project/tests/test_core.py`

| 测试类 | 用例数 | 覆盖模块 |
|--------|--------|----------|
| `TestChunker` | 3 | 分块引擎 |
| `TestDocumentParser` | 2 | 文档解析 |
| `TestSecurity` | 3 | JWT/文件校验/限流 |
| `TestExporter` | 1 | 导出模块 |
| `TestAuthAPI` | 2 | 权限系统 |

**共 11 个测试用例。**

### 12.2 运行测试

```bash
cd novel_project
python -m pytest tests/ -v
```

### 12.3 RAG 评估

```bash
python scripts/rag_evaluate.py --gate --threshold 0.75
```

---

## 13. Roadmap

| 功能 | 状态 | 所需资源 |
|------|------|----------|
| MySQL 迁移 | 桩代码完成 | MySQL 服务 |
| PDF 扫描件 OCR | 检测逻辑完成 | PaddleOCR / 魔塔 GPU |
| 角色立绘生成 | 接口预留 | GPU + IP-Adapter |
| 本地 LLM 备胎 | 接口预留 | GPU + Qwen-7B |
| Neo4j 迁移 | 脚本完成 | Neo4j 服务 |
| 战力一致性评估 | 不建议实现 | 开放研究问题 |

---

## 附录: 最新改动记录 (2026-07-09)

### 审核链路修复

| 改动前 | 改动后 |
|--------|--------|
| 第 1 轮跳过审核直接返回 | 每轮都审 |
| 审核不通过只 `print()` | 调用 `audit_log()` 双写 DB + 文件 |
| `_refine_plan` 无分类处理 | 新增 `_handle_review_failure()` 按证据不足/别名冲突/幻觉 分类修复 |
| 返回无标注 | 无法自动修复时 `needs_manual_review: True` |
| 审计日志仅写 DB | 双写策略: `audit.json` + `audit.log` 文本文件 |
| `audit_log` 无 `failure_type` 参数 | 新增 `failure_type` 记录失败类型 |
| `AUDIT_LOG_PATH` 指向错误目录 | 修复为项目根 `data/logs/audit.log` |

### 修改文件

- `novel_project/core/agents/coordinator.py`
- `novel_project/core/security.py`

---

> 本文档由 Claude Code 基于源码自动生成，最后更新于 2026-07-09。
