# 📖 网文 GraphRAG + 多 Agent 协作分析系统

基于知识图谱与多 Agent 协作的网络小说智能分析系统。

```
上传 TXT → 自动清洗 → LLM 逐章编译 Wiki → 构建人物关系图谱 → 多 Agent 问答
```

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 设置 API Key
set DEEPSEEK_API_KEY=your-key

# 启动
python run.py
```

访问 http://localhost:8000

## 新增功能（2026-06-23 更新）

| 功能 | 入口 | 说明 |
|------|------|------|
| MCP Server | `python mcp_server.py` | 5 个 Tool 供 Cursor/Claude Desktop 调用 |
| RAG 评估 | `scripts/rag_evaluate.py` | LLM-as-Judge + 黄金测试集 + 回归门禁 |
| 分级路由 + 去重 | `core/chapter_router.py` | 实体密度评分 + SimHash/MinHash 去重 |
| 安全加固 | `core/security.py` | JWT + 权限 + 限流 + 文件校验 + 日志脱敏 |
| Neo4j 迁移 | `scripts/migrate_to_neo4j.py` | NetworkX → Neo4j 时序图谱 |
| 生态导出 | `core/exporter.py` | Obsidian / EPUB / Excel / CSV / MD |
| 配置管理 | `config.yaml` | 统一配置 + 环境变量覆盖 |
| 预留接口 | `interfaces/` | 6 大功能接口，标注所需资源 |

## 项目结构

```
novel_project/
├── run.py              # 启动入口
├── mcp_server.py       # MCP Server
├── config.yaml         # 配置文件
├── core/               # 核心模块
│   ├── agents/         # 多 Agent 系统
│   ├── llm.py          # LLM 调用层
│   ├── chapter_router.py  # 分级路由
│   ├── security.py     # 安全模块
│   ├── exporter.py     # 导出模块
│   └── ...
├── interfaces/         # 预留接口
├── scripts/            # 工具脚本
├── web/                # FastAPI Web
└── data/               # 数据目录
```
