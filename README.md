# 📖 网文 GraphRAG + 多 Agent 协作分析系统
# Novel GraphRAG + Multi-Agent Analysis System

基于知识图谱与多 Agent 协作的网络小说智能分析系统。上传 TXT / Word / PDF / Markdown → 自动清洗 → LLM 逐章编译 Wiki → 构建人物关系图谱 → 多 Agent 协作回答分析问题。

> An intelligent novel analysis system powered by Knowledge Graph and Multi-Agent collaboration. Upload TXT/DOCX/PDF/MD → Auto-clean → LLM chapter-by-chapter Wiki compilation → Character relationship graph → Multi-Agent Q&A.

---

## ✨ 功能 / Features

| 功能 | 说明 |
|------|------|
| 📤 **多格式上传** | TXT / Word (.docx) / PDF / Markdown 自动解析，章节边界检测，杂质过滤 |
| 🧠 **Wiki 编译** | LLM 逐章提取人物/事件/关系，长章语义分块 + 子块级断点 + 增量编译 + Token 预算控制 |
| 🕸️ **知识图谱** | Cytoscape.js 驱动，双布局（cose 全图 / concentric 聚焦），自适应参数，**阵营（社区）着色**，缩放标签联动 |
| 🔍 **五级检索** | Wiki 摘要 → 知识图谱 → **PPR 多跳扩散** → 向量混合检索（实体精确腿 + **共现腿** + RRF 融合）→ **人物社群摘要**，多查询扩展 + 跨书共查 |
| 🤖 **多 Agent** | **LangGraph 状态图编排**：意图/拆解合并规划 → 并行多源检索 → Writer 流式生成 → Reviewer 质量审核 + 分类修复 |
| 💬 **智能问答** | **SSE 真流式**（进度事件 + 报告逐 token 推送）、Markdown 渲染、语义缓存（相似问题秒回）、分段式上下文管理 |
| 🔗 **MCP Server** | 5 个 Tool 暴露给 Cursor / Claude Desktop |
| 📊 **RAG 评估** | LLM-as-Judge 评估 faithfulness，黄金测试集 + 回归门禁 |
| 💰 **Token 分级路由** | 实体密度 + 对话占比动态路由 PRO/LIGHT/SKIP，SimHash + MinHash 去重 |
| 📤 **生态导出** | Obsidian 知识库、EPUB 电子书、Excel/CSV 清单、Markdown 报告、GEXF 图数据 |
| 🔐 **权限系统** | JWT 4 角色（admin/editor/viewer/api），中间件拦截，前端 data-perm 显隐 |
| 🗄️ **DB 抽象层** | 用户 + 角色 + 审计日志通过统一接口操作，JSON 文件 → MySQL 无缝切换 |
| ✏️ **图谱编辑** | 编辑模式下右键删除/修改关系、合并人物节点，操作自动备份 |
| ⏯️ **编译控制** | 全量/范围编译，异步后台执行，实时进度轮询，暂停/恢复 |

## 🛠 技术栈 / Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, **FastAPI**, Uvicorn, **LangGraph**（Agent 编排） |
| Frontend | **Vue 3 + Vite**（独立 SPA）, **Tailwind CSS**, **Cytoscape.js**, markdown-it |
| LLM | **DeepSeek V4 Pro** (多模型路由 + 降级开关) |
| Vector DB | **ChromaDB** (内置 ONNX embedding，可选 BGE 中文模型) |
| Graph | **NetworkX** (图谱构建 + Louvain 社区检测 + PPR) → **Cytoscape.js** (前端渲染) |
| Auth | JWT, 4-role permission, BaseHTTPMiddleware |
| DB | 抽象层: SQLite (当前) / JSON / MySQL (预留) |
| Deploy | Docker Compose (多阶段构建), Nginx |

## 🚀 快速开始 / Quick Start

```bash
# 1. Clone
git clone https://github.com/yekup/novel-graphrag.git
cd novel-graphrag

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate       # Windows
source .venv/bin/activate    # Linux/Mac

# 3. 安装依赖
pip install -r requirements.txt

# 4. 构建前端（Vue3 SPA，需要 Node ≥ 18）
cd frontend && npm install && npm run build && cd ..

# 5. 设置 API Key（从 https://platform.deepseek.com 获取）
copy .env.template .env
# 编辑 .env 填入 DEEPSEEK_API_KEY

# 6. 启动
cd novel_project && python run.py
```

> 前端开发模式：`cd frontend && npm run dev`（端口 5173，已配置 `/api` 代理到 8000）。

打开 http://localhost:8000 — 首次启动会自动创建管理员 `admin`：密码取环境变量 `ADMIN_PASSWORD`；未设置时自动生成随机密码并打印到控制台。

> Open http://localhost:8000 — on first launch an `admin` account is created: the password comes from the `ADMIN_PASSWORD` env var, or a random one is generated and printed to the console if unset.

### 支持的文档格式

| 格式 | 解析器 | 特性 |
|------|--------|------|
| `.txt` | TxtParser | 自动编码检测 UTF-8/GBK/GB18030 |
| `.docx` | DocxParser | Heading 样式检测章节，页眉/目录过滤 |
| `.pdf` | PdfParser | 文字层检测，多栏排序，段落重组，扫描件提示 |
| `.md` | MarkdownParser | 零依赖，# 标题自动转章节 |

### MCP Server（独立运行）

```bash
cd novel_project && python mcp_server.py
```

配置 Cursor / Claude Desktop 连接即可使用。

## 🕸️ 图谱交互

| 功能 | 说明 |
|------|------|
| 双布局引擎 | cose 全图聚类 + concentric 聚焦同心圆 |
| 阵营着色 | 默认按社区（阵营）着色，图例点击高亮，可切回角色着色 |
| 自适应布局 | 后端根据节点数动态计算斥力/重力/边长 |
| 缩放标签显隐 | 缩小自动隐藏次要标签，text-opacity 控制 |
| 节点分离 | 布局后强制分离迭代，防重叠 |
| 编辑模式 | 右键删除/修改关系、合并节点（admin/editor） |
| 高清导出 | PNG/SVG 导出，2x/4x 分辨率，含图例 |
| 详情面板 | 点击节点查看关联关系（分页）、角色信息 |
| 孤立节点切换 | 「仅关联实体/显示全部」下拉切换 |

## 🌐 API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/register` | 注册 |
| GET | `/api/auth/me` | 当前用户 + 权限列表 |
| GET | `/api/auth/users` | 用户列表（admin） |
| POST | `/api/ask` | 问答（含语义缓存） |
| POST | `/api/ask/stream` | SSE 流式问答 |
| POST | `/api/search` | Wiki + 图谱检索 |
| POST | `/api/search/vector` | 向量语义检索 |
| POST | `/api/search/all` | 五级统一检索 |
| POST | `/api/search/multi` | 跨书全文检索 |
| GET | `/api/search/multi/books` | 跨书索引书籍列表 |
| GET | `/api/graph?novel=` | 图谱数据 + 自适应布局参数 |
| GET | `/api/novels` | 可用书籍列表 |
| POST | `/api/upload` | 上传文档（TXT/DOCX/PDF/MD） |
| POST | `/api/build` | 编译 Wiki |
| POST | `/api/build/pause` | 暂停编译 |
| POST | `/api/build/resume` | 恢复编译 |
| GET | `/api/build/progress` | 编译进度 |
| POST | `/api/build/retry` | 重试失败章节 |
| POST | `/api/graph/edit/delete-edge` | 删除关系 |
| POST | `/api/graph/edit/merge-nodes` | 合并人物 |
| POST | `/api/graph/edit/update-relation` | 修改关系 |
| GET | `/api/cache/stats` | 语义缓存统计 |
| GET | `/api/chapter?keyword=` | 原文检索 |
| GET | `/health` | 健康检查 |

## 📁 项目结构 / Structure

```
novel-graphrag/
├── README.md
├── config.yaml              # 统一配置
├── requirements.txt         # Python 依赖
├── Dockerfile / docker-compose.yml / nginx.conf
├── SQL/schema.sql           # MySQL 建表语句
│
├── frontend/                # Vue3 + Vite SPA（独立前端工程）
│   ├── vite.config.js       # dev 代理 /api → 8000
│   └── src/
│       ├── api/             # fetch 封装（JWT / 401 处理）
│       ├── stores/          # Pinia：auth（权限）/ novel（当前书籍）
│       ├── components/      # AppShell / BookSwitcher / StreamingMessage
│       └── views/           # Login / Dashboard / Chat / Graph / Upload
│
└── novel_project/
    ├── run.py               # 启动入口
    ├── mcp_server.py        # MCP Server
    │
    ├── core/
    │   ├── llm.py           # LLM 调用层（多 Provider + 重试 + .env 加载）
    │   ├── chapter_parser.py  # Wiki 编译（v2: 子块断点+增量+Token预算）
    │   ├── compiler_config.py # 编译配置
    │   ├── chunker.py       # 层级分块 + 向量索引（ChromaDB）
    │   ├── document_parser.py # TXT/DOCX/PDF/MD 解析
    │   ├── knowledge_graph.py # 图谱构建 + 别名消歧
    │   ├── graph_community.py # 社区检测 + LLM 社群摘要
    │   ├── retriever.py     # 五级检索（Wiki/图谱/PPR/向量混合/社群）
    │   ├── semantic_cache.py # 语义缓存
    │   ├── material_pool.py  # 分段式材料管理（延迟压缩）
    │   ├── multi_book_search.py # 跨书倒排索引
    │   ├── security.py      # JWT/权限/限流/审计日志
    │   ├── exporter.py      # 生态导出
    │   ├── chapter_router.py # 分级路由 + 去重
    │   ├── slang_map.json   # 谐音黑话归一化
    │   ├── db/              # DB 抽象层（SQLite/JSON/MySQL）
    │   └── agents/
    │       ├── coordinator.py  # LangGraph 编排（合并规划 + 并行检索）
    │       ├── researcher.py   # 多源检索（含向量+跨书）
    │       ├── writer.py       # 报告生成（流式 + 引用校验）
    │       ├── structured_writer.py # 结构化输出
    │       └── reviewer.py     # 质量审核（白名单 + 全量上下文）
    │
    ├── interfaces/          # 预留接口层
    ├── scripts/             # 工具脚本（评估/社区编译/召回测量）
    ├── web/
    │   ├── app.py           # FastAPI 主入口（挂载 SPA dist）
    │   └── routes/          # API 路由
    ├── tests/               # 单元测试（110+ 用例）
    └── data/                # 数据目录（.gitignore 排除）
```

## 💡 核心优化

### 混合检索与召回率
纯向量检索对中文实体召回不足，为此在纯向量腿之外加两条实体腿：
**实体精确腿**（查询命中图谱节点名时，用 `where_document` 限定分块必须包含该实体）
与**实体共现腿**（≥2 实体时，限定分块同时包含两个实体——关系类问题的关键证据）。
三腿结果按 RRF 融合。三本书的黄金集实测（各 17 条实体类问题）：

| 书 | recall@1 | recall@3 | recall@5 |
|----|---------|---------|---------|
| 绍宋 | 94.1% | 100% | 100% |
| 斗破苍穹 | 100% | 100% | 100% |
| 神印王座 | 94.1% | 100% | 100% |

（纯向量基线对照：recall@1 29.4% / recall@5 64.7%）

复现：`python scripts/vector_recall_eval.py --book shaosong|doupo|shenyin`（`--mode vector` 为基线）。
黄金集：`data/eval/golden/{shaosong,doupo,shenyin}.json`，各 23 条
（全书摘要 + 主要角色 + 卷摘要 + 人物关系），取材自各书真实编译产物。

### PPR 多跳图检索
从查询命中的实体出发做个性化 PageRank，发现查询未点名但结构上紧密关联的
桥接人物（如问「赵玖和岳飞的关系」能挖出韩世忠、张俊等相关人物）。

### 人物社群摘要
Louvain 社区检测 + LLM 生成阵营摘要，作为检索中间层；图谱页默认按阵营着色，
势力划分一目了然。增量编译时成员集未变的社区自动复用旧摘要。

### 问答链路延迟优化
意图识别与任务拆解合并为一次 LLM 调用；多检索步骤并行执行；材料池延迟压缩
（单轮流程零压缩开销）；Writer 逐 token 流式推送。实测同一问题
总时长 101.8s → 56.5s，首 token 20.2s。

### 语义缓存
相同/相似问题秒回，Embedding 余弦相似度 > 0.85 命中，命中率随使用增长。

### 分段式材料管理
多轮问答中旧材料自动 LLM 压缩为摘要，始终控制上下文长度，避免 Token 膨胀。

### 编译管道 v2
| 特性 | 说明 |
|------|------|
| 子块级断点 | 超长章节每子块落地，崩溃只重跑未完成块 |
| 增量编译 | 只处理新增/修改章节，自动更新对应卷摘要 |
| 失败隔离 | 失败章节单独记录，不阻塞全流程，支持单章重试 |
| 原子写入 | 临时文件 → os.replace，杜绝半残文件 |
| Token 预算 | 全局计数器，到达阈值自动终止 |
| 后台异步 | 编译不阻塞请求，前端轮询实时进度 |

### 跨书全文检索
基于倒排索引，支持多书共查，关键词命中排序，去重输出。

### RAG 质量评估
LLM-as-Judge 自动化评估，黄金测试集覆盖三本书（各 23 条：全书摘要、卷摘要、人物关系、主要角色）。

| 指标 | 当前分数 | 阈值 |
|------|---------|------|
| Faithfulness | 0.852 | ≥ 0.75 ✅ |
| Answer Relevancy | 0.811 | — |

每次 Prompt 变更或模型切换后自动回归验证：

```bash
cd novel_project
python scripts/rag_evaluate.py --gate --threshold 0.75
```

## 🚫 待实现 / Roadmap

| 功能 | 接口 | 所需资源 |
|------|------|---------|
| MySQL 迁移 | `core/db/mysql_backend.py` | MySQL 服务 |
| PDF 扫描件 OCR | `PdfParser.ocr_required` | PaddleOCR / 魔塔 GPU |
| 角色立绘 | `interfaces/portrait_generator.py` | GPU + IP-Adapter |
| 本地 LLM 备胎 | `interfaces/llm_provider.py` | GPU + Qwen-7B |

## 📄 许可证 / License

MIT
