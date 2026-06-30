# 📖 网文 GraphRAG + 多 Agent 协作分析系统
# Novel GraphRAG + Multi-Agent Analysis System

基于知识图谱与多 Agent 协作的网络小说智能分析系统。上传网文 TXT → 自动清洗 → LLM 逐章编译 Wiki → 构建人物关系图谱 → 多 Agent 协作回答分析问题。

> An intelligent novel analysis system powered by Knowledge Graph and Multi-Agent collaboration. Upload TXT → Auto-clean → LLM chapter-by-chapter Wiki compilation → Character relationship graph → Multi-Agent Q&A.

---

## ✨ 功能 / Features

| 功能 | 说明 |
|------|------|
| 📤 **上传清洗 / Upload & Clean** | 上传 TXT 文件，自动去除广告、作者话、打赏名单 |
| 🧠 **Wiki 编译 / Wiki Compilation** | LLM 逐章提取人物、事件、关系，生成结构化摘要（支持断点续传、长章分块编译、分级路由、语义去重） |
| 🕸️ **知识图谱 / Knowledge Graph** | **Cytoscape.js** 驱动的人物关系图谱，双布局引擎（cose 全图聚类 + concentric 聚焦同心圆），缩放联动标签隐、自适应布局参数 |
| 🔍 **三级检索 / 3-Level Retrieval** | Wiki → 知识图谱 → **向量语义检索**（ChromaDB），动态 top_k |
| 🤖 **多 Agent 协作 / Multi-Agent** | Coordinator 意图识别 → Researcher 检索 → Writer 写报告 → Reviewer 审核（含结构化锚点校验） |
| 💬 **智能问答 / Smart Q&A** | 支持人物分析、关系分析、情节梳理、全书总结，含 SSE 流式输出 |
| 🔗 **MCP Server** | 5 个 Tool 暴露给 Cursor / Claude Desktop：搜索人物、追踪时间线、分析章节、全文搜索、书籍列表 |
| 📊 **RAG 质量评估** | LLM-as-Judge 评估 faithfulness + answer_relevancy，含黄金测试集 + 回归门禁 |
| 💰 **Token 分级路由** | 基于实体密度 + 对话占比动态路由（PRO/LIGHT/SKIP），SimHash + MinHash 双重语义去重 |
| 📤 **生态导出 / Export** | Obsidian 知识库、EPUB 电子书、Excel/CSV 人物清单、Markdown 分析报告、GEXF 图数据 |
| 🔐 **登录认证** | JWT 登录/注册，4 角色权限系统（admin/editor/viewer/api），权限驱动前端 UI |
| 📦 **多格式解析** | 统一 DocumentRouter 引擎，TXT 已就绪（Word/PDF 可扩展），上传自动分块 |
| 🧹 **领域优化** | jieba 自定义词典、谐音/黑话归一化、实体消歧/别名合并 |
| ✅ **单元测试** | 11 个测试覆盖分块/解析/安全/导出/Auth 模块 |

## 🛠 技术栈 / Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, **FastAPI**, Uvicorn |
| Frontend | Jinja2, **Tailwind CSS**, **Cytoscape.js** |
| LLM | **DeepSeek V4 Pro** (支持多模型路由 + 本地模型接口预留) |
| Vector DB | **ChromaDB** |
| Graph | **NetworkX** / **Neo4j** (时序边模型，支持冷热分层) |
| Evaluation | LLM-as-Judge, 定制化 Novel Faithfulness Prompt |
| Auth | JWT, 4-role permission system, rate limiter, file validator |
| Deploy | Docker Compose (6 服务), Nginx |
| Agent | LangGraph/AutoGen 接口预留 |
| MCP | **MCP Server** 独立运行 |

## 🚀 快速开始 / Quick Start

```bash
# 1. Clone
git clone https://github.com/yekup/agent_project.git
cd agent_project

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set API Key
export DEEPSEEK_API_KEY=your-key

# 5. Run
cd novel_project && python run.py
```

Open http://localhost:8000

### MCP Server（独立运行）

```bash
cd novel_project && python mcp_server.py
```

然后在 Cursor / Claude Desktop 中配置 MCP 连接即可使用。

### Docker Compose（全服务部署）

```bash
docker compose up -d
```

## 🕸️ 图谱交互

图谱页面基于 **Cytoscape.js** 渲染，提供以下交互：

| 功能 | 说明 |
|------|------|
| **全图模式** | cose 力导向布局，节点按社区聚类分区展示 |
| **聚焦模式** | concentric 同心圆布局，选中人物置中，关联分层淡化 |
| **自适应布局** | 后端根据节点数动态计算斥力/重力/边长等参数 |
| **缩放标签隐显** | 缩小自动隐藏次要标签，放大恢复，text-opacity 控制 |
| **节点防重叠** | 布局后强制分离迭代，确保节点不互相遮盖 |
| **孤立节点切换** | 「仅关联实体/显示全部节点」下拉切换，实时重渲染 |
| **高清导出** | 原生 PNG/SVG 导出，支持 2x/4x 分辨率，含图例 |
| **角色详情面板** | 点击节点弹出浮动面板，关联关系分页展示 |
| **角色筛选** | 按角色类型（主角/配角）筛选，按名称搜索聚焦 |

## 📊 数据 / Data

| Novel | Chapters | Characters | Relations | Events |
|-------|----------|------------|-----------|--------|
| 《绍宋》/ Shaosong | 438 | — (需编译) | — | — |
| 《斗破苍穹》/ Battle Through the Heavens | 1649 | — | — | ⏳ |
| 《神印王座》/ Throne of the Divine Seal | 876 | — | — | ⏳ |

## 📁 项目结构 / Structure

```
novel_project/
├── run.py                     # Entry point (FastAPI)
├── mcp_server.py              # MCP Server (5 tools)
├── config.yaml                # 统一配置
├── requirements.txt           # 依赖清单
│
├── core/
│   ├── llm.py                 # LLM 统一调用层 (DeepSeek + fallback)
│   ├── chapter_parser.py      # Wiki 编译 (checkpoint, long-chunk split)
│   ├── chapter_router.py      # 分级路由 + SimHash/Minhash 去重
│   ├── chunker.py             # 层级分块引擎 (滑窗+重叠+段落边界)
│   ├── document_parser.py     # 文档解析引擎 (TXT/Word/PDF 扩展)
│   ├── knowledge_graph.py     # 知识图谱构建 + 别名消歧
│   ├── retriever.py           # 3-level retrieval (含向量检索)
│   ├── memory.py              # Session memory system
│   ├── pipeline.py            # Async build pipeline
│   ├── security.py            # JWT/权限/限流/文件校验/日志脱敏
│   ├── exporter.py            # Obsidian/EPUB/Excel/CSV/MD 导出
│   └── agents/
│       ├── coordinator.py     # Intent detection + task decomposition
│       ├── researcher.py      # Multi-source retrieval
│       ├── writer.py          # Report generation
│       ├── structured_writer.py  # 结构化输出 + 原文锚点校验
│       └── reviewer.py        # Quality scoring + feedback
│
├── interfaces/                # 预留接口层 (GPU/商业合作)
│   ├── portrait_generator.py  # 角色立绘 (IP-Adapter)
│   ├── rlhf_pipeline.py       # RLHF 微调闭环
│   ├── llm_provider.py        # LLM 多后端 + 降级路由
│   ├── copyright_verifier.py  # 版权合规模块
│   ├── graph_storage.py       # 冷热分层图存储
│   └── eval_metrics.py        # 可扩展评估指标
│
├── scripts/
│   ├── clean_novel.py         # TXT cleaning tool
│   ├── rag_evaluate.py        # RAG 质量评估 + 黄金测试集
│   ├── migrate_to_neo4j.py    # NetworkX → Neo4j 迁移
│   └── test_*.py              # Tests
│
├── web/
│   ├── app.py                 # FastAPI app (含健康检查)
│   ├── routes/
│   │   ├── agent_routes.py    # API routes (图谱自适应布局参数)
│   │   └── auth_routes.py     # 登录/注册/权限 API
│   ├── templates/
│   │   ├── dashboard.html     # 首页
│   │   ├── graph.html         # 图谱 (Cytoscape.js)
│   │   ├── chat.html          # 问答
│   │   ├── upload.html        # 上传
│   │   └── login.html         # 登录
│   └── static/js/
│       ├── auth.js            # 前端权限管理
│       └── cytoscape.min.js   # 图谱渲染引擎
│
├── tests/
│   └── test_core.py           # 11 个单元测试
│
└── data/
    ├── raw/                   # Raw TXT files
    ├── processed/             # Cleaned JSON
    ├── wiki/                  # Compiled Wiki
    ├── chroma/                # 向量索引
    ├── vocab/                 # 领域分词词典
    └── eval/golden/           # 黄金测试集
```

## 🌐 API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | 用户登录 |
| POST | `/api/auth/register` | 用户注册 |
| GET | `/api/auth/me` | 当前用户信息 + 权限列表 |
| POST | `/api/ask` | Multi-Agent Q&A |
| POST | `/api/ask/stream` | SSE 流式 Agent 分析 |
| POST | `/api/search` | Wiki + 图谱检索 |
| POST | `/api/search/vector` | 语义向量检索 |
| POST | `/api/search/all` | 三级统一检索 |
| GET | `/api/graph?novel=` | 图谱数据（含自适应布局参数）|
| GET | `/api/novels` | 可用的书籍列表 |
| POST | `/api/upload` | 上传文档 |
| POST | `/api/build` | 编译 Wiki |
| POST | `/api/index` | 向量索引 |
| GET | `/health` | 健康检查 |

### MCP Tools (stdio)

| Tool | Description |
|------|-------------|
| `list_novels` | 列出已编译图谱的小说 |
| `search_novel_graph` | 搜索人物和关系网络 |
| `get_character_timeline` | 追踪人物在全书中的演化 |
| `analyze_chapter` | 获取单章的结构化分析 |
| `search_wiki` | 全文搜索 Wiki 条目 |

## 📈 RAG 质量评估

```bash
# 生成黄金测试集
python scripts/rag_evaluate.py --generate --novel shaosong

# 运行回归门禁 (faithfulness ≥ 0.75)
python scripts/rag_evaluate.py --gate --threshold 0.75

# 评估单条
python scripts/rag_evaluate.py --query "问题" --answer "回答" --contexts "原文"
```

## 💰 Token 成本治理

分级路由根据实体密度 + 对话占比自动分章到 PRO/LIGHT/SKIP 三级，配合 SimHash + MinHash 双重去重，可节省 30-50% 编译成本。

```python
from core.chapter_router import ChapterRouter, TextDeduplicator

router = ChapterRouter()
tier = router.route_chapter(chapter_text)  # "pro" | "light" | "skip"

deduper = TextDeduplicator()
is_dup = deduper.is_duplicate(paragraph)   # True/False
```

## 📤 生态导出

```python
from core.exporter import NovelExporter

exporter = NovelExporter("shaosong")
exporter.export_obsidian("output/obsidian/")  # 双向链接知识库
exporter.export_epub("output/shaosong.epub")  # EPUB 电子书
exporter.export_excel("output/shaosong.xlsx")  # 人物+关系清单
exporter.export_markdown_report("output/report.md")  # 分析报告
```

## 🕸️ Neo4j 时序图谱

```bash
# 启动 Neo4j
docker run -d --name neo4j-novel \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:5-community

# 导入图谱
python scripts/migrate_to_neo4j.py --novel shaosong --import

# 验证
python scripts/migrate_to_neo4j.py --novel shaosong --verify

# 导出事件节点
python scripts/migrate_to_neo4j.py --novel shaosong
```

## 🚫 待实现（需 GPU / 商业合作）

| 功能 | 接口文件 | 所需资源 |
|------|----------|----------|
| 🎨 角色立绘 | `interfaces/portrait_generator.py` | GPU + IP-Adapter |
| 🔄 RLHF 微调 | `interfaces/rlhf_pipeline.py` | GPU + TRL/LLaMA-Factory |
| 🧠 本地 LLM 备胎 | `interfaces/llm_provider.py` | GPU + Qwen-7B |
| ⚖️ 版权校验 | `interfaces/copyright_verifier.py` | 起点/晋江 API 合作 |
| 🧊 冷存储 | `interfaces/graph_storage.py` | TimescaleDB/ClickHouse |

详见 [interfaces/README.md](novel_project/interfaces/README.md)

## 🔗 链接 / Links

- **GitHub**: https://github.com/yekup/agent_project
- **Docker Hub**: (待发布)

## 📄 许可证 / License

MIT

---

> 当前版本 (2026-06-30): Cytoscape.js 图谱引擎、自适应布局参数、双布局模式（cose + concentric）、缩放联动标签显隐、节点防重叠分离、后端自适应参数计算、前端登录认证 + 权限驱动 UI、向量语义检索、长章分块编译、文档解析引擎、领域词典 + 谐音归一化 + 实体消歧、ChromaDB 集成、健康检查端点。
