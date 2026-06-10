# 网文 GraphRAG + 多 Agent 协作分析系统

基于知识图谱与多 Agent 协作的网络小说智能分析系统。上传网文 TXT → 自动清洗 → LLM 逐章编译 Wiki → 构建人物关系图谱 → 多 Agent 协作回答分析问题。

## 功能

- **📤 上传清洗** — 上传 TXT 文件，自动去除广告、作者话、打赏名单等杂质
- **🧠 逐章 Wiki 编译** — LLM 逐章提取人物、事件、关系，生成结构化摘要（支持断点续传）
- **🕸️ 知识图谱** — 跨章合并人物实体，构建人物关系网络（NetworkX + 力导向图可视化）
- **🔍 三级检索** — Wiki → 知识图谱 → 原文，动态 top_k，自动适配问题粒度的层级检索
- **🤖 多 Agent 协作** — Coordinator 意图识别 → Researcher 检索 → Writer 写报告 → Reviewer 审核
- **💬 智能问答** — 支持人物分析、关系分析、情节梳理、全书总结等类型问题

## 技术栈

| 层面 | 技术 |
|------|------|
| 后端 | Python 3.10, FastAPI, Uvicorn |
| 前端 | Jinja2, Tailwind CSS, vis-network |
| LLM | DeepSeek V4 Pro |
| 向量 | ChromaDB, BAAI/bge-small-zh-v1.5 |
| 图谱 | NetworkX |
| 认证 | JWT, 4 角色权限 |
| 部署 | Nginx, systemd, Let's Encrypt SSL |

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/yekup/agent_project.git
cd agent_project

# 2. 安装依赖
pip install openai chromadb sentence-transformers networkx fastapi uvicorn jinja2 python-multipart aiofiles

# 3. 配置 API Key
export DEEPSEEK_API_KEY=your-key

# 4. 启动
python run.py
```

访问 http://localhost:8000

## 数据

已入库三部小说（清洗后结构化 JSON）：

| 小说 | 章节 | 说明 |
|------|------|------|
| 《绍宋》 | 438 章 | 人物关系复杂，已完整编译 Wiki + 图谱 |
| 《斗破苍穹》 | 1649 章 | 编译中 |
| 《神印王座》 | 876 章 | 待编译 |

> 注：原始 TXT 未包含在仓库中，需自行准备。使用 `scripts/clean_novel.py` 清洗后即可使用。

## 项目结构

```
novel_project/
├── run.py                     # 启动入口
├── core/
│   ├── chapter_parser.py      # 逐章 Wiki 编译（断点续传、卷检测、全书摘要）
│   ├── knowledge_graph.py     # 知识图谱构建（实体合并、关系建模）
│   ├── retriever.py           # 三级检索（Wiki + 图谱 + 原文）
│   ├── memory.py              # Session 记忆系统
│   ├── pipeline.py            # 异步编译管道
│   └── agents/
│       ├── coordinator.py     # 协调员（意图识别 + 任务拆解）
│       ├── researcher.py      # 研究员（三级检索 + 原文兜底）
│       ├── writer.py          # 撰稿人（生成分析报告）
│       └── reviewer.py        # 审核员（评分 + 修改反馈）
├── web/
│   ├── app.py                 # FastAPI 主入口
│   ├── routes/
│   │   └── agent_routes.py    # API 路由
│   ├── templates/             # Jinja2 模板
│   └── static/                # 静态文件
├── scripts/
│   ├── clean_novel.py         # TXT 清洗工具
│   ├── run_night_build.py     # 批量编译脚本
│   ├── test_agents.py         # Agent 测试
│   └── evaluate.py            # 检索评估
└── data/
    ├── raw/                   # 原始 TXT
    ├── processed/             # 清洗后 JSON
    └── wiki/                  # 编译后的 Wiki + 图谱
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ask` | 多 Agent 分析问答 |
| POST | `/api/search` | 三级检索 |
| GET | `/api/graph` | 获取知识图谱（支持 `?novel=` 参数） |
| GET | `/api/novels` | 列出已入库书籍 |
| POST | `/api/upload` | 上传 TXT |
| POST | `/api/build` | 开始编译 |

## 许可证

MIT
