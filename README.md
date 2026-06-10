# 📖 网文 GraphRAG + 多 Agent 协作分析系统
# Novel GraphRAG + Multi-Agent Analysis System

基于知识图谱与多 Agent 协作的网络小说智能分析系统。上传网文 TXT → 自动清洗 → LLM 逐章编译 Wiki → 构建人物关系图谱 → 多 Agent 协作回答分析问题。

> An intelligent novel analysis system powered by Knowledge Graph and Multi-Agent collaboration. Upload TXT → Auto-clean → LLM chapter-by-chapter Wiki compilation → Character relationship graph → Multi-Agent Q&A.

---

## ✨ 功能 / Features

| 功能 | 说明 |
|------|------|
| 📤 **上传清洗 / Upload & Clean** | 上传 TXT 文件，自动去除广告、作者话、打赏名单 / Upload TXT, auto-remove ads and author notes |
| 🧠 **Wiki 编译 / Wiki Compilation** | LLM 逐章提取人物、事件、关系，生成结构化摘要（支持断点续传）/ LLM extracts characters, events, relationships per chapter with checkpoint resume |
| 🕸️ **知识图谱 / Knowledge Graph** | 跨章合并人物实体，NetworkX + vis-network 力导向图可视化 / Cross-chapter entity merging, force-directed graph visualization |
| 🔍 **三级检索 / 3-Level Retrieval** | Wiki → 知识图谱 → 原文，动态 top_k，自动适配问题粒度 / Wiki → Graph → Original text with dynamic top-k |
| 🤖 **多 Agent 协作 / Multi-Agent** | Coordinator 意图识别 → Researcher 检索 → Writer 写报告 → Reviewer 审核 / Intent detection → Research → Report writing → Review |
| 💬 **智能问答 / Smart Q&A** | 支持人物分析、关系分析、情节梳理、全书总结 / Character analysis, relationship analysis, plot summary, book overview |

## 🛠 技术栈 / Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10, **FastAPI**, Uvicorn |
| Frontend | Jinja2, **Tailwind CSS**, vis-network |
| LLM | **DeepSeek V4 Pro** |
| Vector DB | **ChromaDB**, BAAI/bge-small-zh-v1.5 |
| Graph | **NetworkX** |
| Auth | JWT, 4-role permission system |
| Deploy | Nginx, systemd, **Let's Encrypt SSL** |

## 🚀 快速开始 / Quick Start

```bash
# 1. Clone
git clone https://github.com/yekup/agent_project.git
cd agent_project

# 2. Install dependencies
pip install openai chromadb sentence-transformers networkx fastapi uvicorn jinja2 python-multipart aiofiles

# 3. Set API Key
export DEEPSEEK_API_KEY=your-key

# 4. Run
python run.py
```

Open http://localhost:8000

## 📊 数据 / Data

| Novel | Chapters | Status |
|-------|----------|--------|
| 《绍宋》/ Shaosong | 438 | ✅ Complete (759 characters, 1017 relations) |
| 《斗破苍穹》/ Battle Through the Heavens | 1649 | ⏳ Compiling |
| 《神印王座》/ Throne of the Divine Seal | 876 | ⏳ Pending |

> Raw TXT files are not included. Use `scripts/clean_novel.py` to clean your own files.

## 📁 项目结构 / Structure

```
novel_project/
├── run.py                     # Entry point
├── core/
│   ├── chapter_parser.py      # Wiki compilation (checkpoint, volume detection)
│   ├── knowledge_graph.py     # Knowledge graph building
│   ├── retriever.py           # 3-level retrieval
│   ├── memory.py              # Session memory system
│   ├── pipeline.py            # Async build pipeline
│   └── agents/
│       ├── coordinator.py     # Intent detection + task decomposition
│       ├── researcher.py      # Multi-source retrieval
│       ├── writer.py          # Report generation
│       └── reviewer.py        # Quality scoring + feedback
├── web/
│   ├── app.py                 # FastAPI app
│   ├── routes/                # API routes
│   ├── templates/             # Jinja2 templates
│   └── static/                # Static files (CSS, JS)
├── scripts/
│   ├── clean_novel.py         # TXT cleaning tool
│   └── test_agents.py         # Agent tests
└── data/
    ├── raw/                   # Raw TXT files
    ├── processed/             # Cleaned JSON
    └── wiki/                  # Compiled Wiki + graphs
```

## 🌐 API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ask` | Multi-Agent Q&A |
| POST | `/api/search` | 3-level search |
| GET | `/api/graph` | Get graph data (`?novel=` param) |
| GET | `/api/novels` | List available novels |
| POST | `/api/upload` | Upload TXT |
| POST | `/api/build` | Start compilation |

## 🔗 链接 / Links

- **GitHub**: https://github.com/yekup/agent_project

## 📄 许可证 / License

MIT
