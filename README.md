# 📖 网文 GraphRAG + 多 Agent 协作分析系统
# Novel GraphRAG + Multi-Agent Analysis System

基于知识图谱与多 Agent 协作的网络小说智能分析系统。上传网文 TXT → 自动清洗 → LLM 逐章编译 Wiki → 构建人物关系图谱 → 多 Agent 协作回答分析问题。

> An intelligent novel analysis system powered by Knowledge Graph and Multi-Agent collaboration. Upload TXT → Auto-clean → LLM chapter-by-chapter Wiki compilation → Character relationship graph → Multi-Agent Q&A.

---

## ✨ 功能 / Features

| 功能 | 说明 |
|------|------|
| 📤 **上传清洗 / Upload & Clean** | 上传 TXT 文件，自动去除广告、作者话、打赏名单 |
| 🧠 **Wiki 编译 / Wiki Compilation** | LLM 逐章提取人物、事件、关系，生成结构化摘要（支持断点续传、分级路由、语义去重） |
| 🕸️ **知识图谱 / Knowledge Graph** | 跨章合并人物实体，NetworkX 力导向图（支持 Neo4j 时序图谱存储） |
| 🔍 **三级检索 / 3-Level Retrieval** | Wiki → 知识图谱 → 原文，动态 top_k，自动适配问题粒度 |
| 🤖 **多 Agent 协作 / Multi-Agent** | Coordinator 意图识别 → Researcher 检索 → Writer 写报告 → Reviewer 审核（含结构化锚点校验） |
| 💬 **智能问答 / Smart Q&A** | 支持人物分析、关系分析、情节梳理、全书总结，含 SSE 流式输出 |
| 🔗 **MCP Server** | 5 个 Tool 暴露给 Cursor / Claude Desktop：搜索人物、追踪时间线、分析章节、全文搜索、书籍列表 |
| 📊 **RAG 质量评估** | LLM-as-Judge 评估 faithfulness + answer_relevancy，含黄金测试集 + 回归门禁 |
| 💰 **Token 分级路由** | 基于实体密度 + 对话占比动态路由（PRO/LIGHT/SKIP），SimHash + MinHash 双重语义去重 |
| 📤 **生态导出 / Export** | Obsidian 知识库、EPUB 电子书、Excel/CSV 人物清单、Markdown 分析报告、GEXF 图数据 |
| 🔒 **安全加固** | JWT 持久密钥、权限中间件、文件上传校验、令牌桶限流、日志脱敏 |

## 🛠 技术栈 / Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, **FastAPI**, Uvicorn |
| Frontend | Jinja2, **Tailwind CSS**, vis-network |
| LLM | **DeepSeek V4 Pro** (支持多模型路由 + 本地模型接口预留) |
| Vector DB | **ChromaDB**, BAAI/bge-small-zh-v1.5 |
| Graph | **NetworkX** / **Neo4j** (时序边模型，支持冷热分层) |
| Evaluation | LLM-as-Judge, **RAGAS**-like 定制化 Prompt |
| Auth | JWT, 4-role permission system, rate limiter |
| Deploy | Docker Compose (6 服务), Nginx, **Let's Encrypt SSL** |
| Agent | LangGraph/AutoGen 接口预留 |
| MCP | **MCP Server** 独立运行 |

## 🚀 快速开始 / Quick Start

```bash
# 1. Clone
git clone https://github.com/yekup/agent_project.git
cd agent_project

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set API Key
export DEEPSEEK_API_KEY=your-key

# 4. Run
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

## 📊 数据 / Data

| Novel | Chapters | Characters | Relations | Events |
|-------|----------|------------|-----------|--------|
| 《绍宋》/ Shaosong | 438 | 759 | 1017 | 3005 |
| 《斗破苍穹》/ Battle Through the Heavens | 1649 | — | — | ⏳ |
| 《神印王座》/ Throne of the Divine Seal | 876 | — | — | ⏳ |

## 📁 项目结构 / Structure

```
novel_project/
├── run.py                     # Entry point (FastAPI)
├── mcp_server.py              # ✨ MCP Server (5 tools)
├── config.yaml                # ✨ 统一配置
├── requirements.txt           # ✨ 依赖清单
│
├── core/
│   ├── llm.py                 # ✨ LLM 统一调用层 (DeepSeek + fallback)
│   ├── chapter_parser.py      # Wiki 编译 (checkpoint, volume detection)
│   ├── chapter_router.py      # ✨ 分级路由 + SimHash/Minhash 去重
│   ├── knowledge_graph.py     # 知识图谱构建
│   ├── retriever.py           # 3-level retrieval
│   ├── memory.py              # Session memory system
│   ├── pipeline.py            # Async build pipeline
│   ├── security.py            # ✨ JWT/权限/限流/文件校验/日志脱敏
│   ├── exporter.py            # ✨ Obsidian/EPUB/Excel/CSV/MD 导出
│   └── agents/
│       ├── coordinator.py     # Intent detection + task decomposition
│       ├── researcher.py      # Multi-source retrieval
│       ├── writer.py          # Report generation
│       ├── structured_writer.py  # ✨ 结构化输出 + 原文锚点校验
│       └── reviewer.py        # Quality scoring + feedback
│
├── interfaces/                # ✨ 预留接口层 (GPU/商业合作)
│   ├── portrait_generator.py  # 角色立绘 (IP-Adapter)
│   ├── rlhf_pipeline.py       # RLHF 微调闭环
│   ├── llm_provider.py        # LLM 多后端 + 降级路由
│   ├── copyright_verifier.py  # 版权合规模块
│   ├── graph_storage.py       # 冷热分层图存储
│   └── eval_metrics.py        # 可扩展评估指标
│
├── scripts/
│   ├── clean_novel.py         # TXT cleaning tool
│   ├── rag_evaluate.py        # ✨ RAG 质量评估 + 黄金测试集 + 回归门禁
│   ├── migrate_to_neo4j.py    # ✨ NetworkX → Neo4j 迁移 + GEXF 导出
│   └── test_*.py              # Tests
│
├── web/
│   ├── app.py                 # FastAPI app
│   ├── routes/agent_routes.py # API routes (含 SSE 流式)
│   ├── templates/             # Jinja2 templates
│   └── static/                # Static files
│
└── data/
    ├── raw/                   # Raw TXT files
    ├── processed/             # Cleaned JSON
    ├── wiki/                  # Compiled Wiki + graphs + 事件节点
    └── eval/golden/           # ✨ 黄金测试集
```

## 🌐 API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ask` | Multi-Agent Q&A |
| POST | `/api/ask/stream` | SSE 流式 Agent 分析 |
| POST | `/api/search` | 3-level search |
| GET | `/api/graph` | Get graph data |
| GET | `/api/novels` | List available novels |
| POST | `/api/upload` | Upload TXT |
| POST | `/api/build` | Start compilation |
| GET | `/health` | Health check |

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
exporter.export_obsidian("output/obsidian/")  # 1207 个双向链接文件
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

# 导出事件节点 (自动从 Wiki 提取 3005 个事件)
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

> 本次更新 (2026-06-23): 新增 MCP Server、RAG 评估 + 黄金测试集、Token 分级路由 + SimHash 去重、安全加固 + Docker Compose、Neo4j 时序图谱迁移、生态导出（Obsidian/EPUB/Excel/GEXF）、6 大预留接口。核心链路全部验证通过。
