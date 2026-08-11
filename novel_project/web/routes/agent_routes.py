"""
API 路由：检索、问答、图谱、上传、编译
"""
import sys, os, json, re, time, hashlib, threading
import logging
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, BackgroundTasks
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from core.memory import SessionMemory
from core.security import validate_path_name

logger = logging.getLogger(__name__)

router = APIRouter()
memory = SessionMemory()

DEFAULT_NOVEL = "绍宋作者：榴弹怕水"  # 请求未指定书籍时的默认书（向后兼容）


def _wiki_path(novel: str) -> str:
    return os.path.join(BASE_DIR, "data", "wiki", f"{novel}_hierarchical.json")


def _graph_path(novel: str) -> str:
    return os.path.join(BASE_DIR, "data", "wiki", f"{novel}_graph.json")


def _novel_json_path(novel: str) -> str:
    """由 wiki 名解析原文 JSON 路径（《书名》作者：xx.json；glob 兜底）"""
    import glob
    core = novel.split("作者：")[0].strip().strip("《》")
    base = os.path.join(BASE_DIR, "data", "processed")
    for p in sorted(glob.glob(os.path.join(base, f"*{core}*.json"))):
        if not p.endswith("_chunks.json"):
            return p
    # 找不到时返回约定路径（调用方负责检查存在性）
    return os.path.join(base, f"{novel}.json")


def _resolve_novel(novel: str = "") -> str:
    """解析请求的书籍参数：空 → 默认书；非空 → 路径安全校验后返回"""
    if not novel:
        return DEFAULT_NOVEL
    return _safe_name(novel)


def _safe_name(name: str, kind: str = "书籍名") -> str:
    """校验将用于拼接文件路径的名字，非法时抛 400（防路径穿越）"""
    try:
        return validate_path_name(name, kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _ensure_graph(novel: str = DEFAULT_NOVEL):
    graph_path = _graph_path(novel)
    if os.path.exists(graph_path):
        return
    from core.knowledge_graph import merge_characters, merge_relationships, build_graph, save_graph
    from core.chapter_parser import load_wiki
    data = load_wiki(_wiki_path(novel))
    chapters = data["chapters"] if isinstance(data, dict) else data
    char_map = merge_characters(chapters)
    rels = merge_relationships(chapters)
    G = build_graph(char_map, rels)
    save_graph(G, graph_path)


# ── NovelRetriever 进程级缓存（按书） ─────────────────────────────────
# 构造函数会同步加载整个 wiki + 整本小说原文并重建 NetworkX 图（数百 MB IO），
# 不能每个请求重建；首次调用前仍需 _ensure_graph() 生成图谱文件。
_retrievers: dict = {}
_retriever_lock = threading.Lock()


def _get_retriever(novel: str = DEFAULT_NOVEL):
    """获取指定书籍的 NovelRetriever（进程级缓存，每书一个实例）"""
    with _retriever_lock:
        r = _retrievers.get(novel)
        if r is None:
            from core.retriever import NovelRetriever
            _ensure_graph(novel)
            r = NovelRetriever(_wiki_path(novel), _graph_path(novel), _novel_json_path(novel))
            _retrievers[novel] = r
        return r


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    novel: str = ""  # 空 → 默认书


class AgentQueryRequest(BaseModel):
    query: str
    session_id: str = ""
    novel: str = ""  # 空 → 默认书


@router.post("/search")
async def search(body: QueryRequest):
    novel = _resolve_novel(body.novel)
    if not os.path.exists(_wiki_path(novel)):
        return {"wiki_results": [], "graph_results": {}}
    retriever = await run_in_threadpool(_get_retriever, novel)
    wiki_results = await run_in_threadpool(retriever.search_wiki, body.query, top_k=body.top_k)
    graph_results = await run_in_threadpool(retriever.search_by_graph, body.query)
    return {"wiki_results": wiki_results, "graph_results": graph_results}


@router.post("/search/vector")
async def vector_search(body: QueryRequest):
    """语义向量搜索（Level 3 检索）"""
    novel = _resolve_novel(body.novel)
    if not os.path.exists(_wiki_path(novel)):
        return {"vector_results": []}
    retriever = await run_in_threadpool(_get_retriever, novel)
    results = await run_in_threadpool(retriever.search_by_vector, body.query, top_k=body.top_k)
    return {"vector_results": results}


@router.post("/search/all")
async def search_all(body: QueryRequest):
    """三级统一检索"""
    novel = _resolve_novel(body.novel)
    if not os.path.exists(_wiki_path(novel)):
        return {"wiki_results": [], "graph_results": {}, "vector_results": []}
    retriever = await run_in_threadpool(_get_retriever, novel)
    return await run_in_threadpool(retriever.search, body.query, top_k=body.top_k)


@router.post("/search/multi")
async def search_multi(body: QueryRequest):
    """跨书全文检索"""
    from core.multi_book_search import get_index
    index = get_index()
    results = index.search(body.query, top_k=body.top_k or 10)
    return {"results": results, "total": len(results)}


@router.get("/search/multi/books")
async def search_multi_books():
    """获取跨书索引的书籍列表"""
    from core.multi_book_search import get_index
    index = get_index()
    return {"books": index.book_stats()}


@router.post("/search/multi/rebuild")
async def rebuild_multi_index():
    """重建跨书索引"""
    from core.multi_book_search import get_index
    index = get_index(rebuild=True)
    return {"status": "ok", "keywords": len(index._index)}


def _compile_dialogue_wiki(sid: str, novel_key: str, mem) -> None:
    """会话记录 → 对话 Wiki 编译（后台任务用，同步函数，异常不冒泡）"""
    try:
        record_path = os.path.join(mem.memory_dir, "sessions", f"{sid}.json")
        if not os.path.exists(record_path):
            logger.warning("[DialogueCompiler] 会话文件不存在: %s", record_path)
            return
        with open(record_path, "r", encoding="utf-8") as f:
            record = json.load(f)
        from core.dialogue_compiler import compile_session, save_dialogue_wiki
        entry = compile_session(record)
        if entry is not None:
            save_dialogue_wiki(novel_key, entry)
    except Exception:
        logger.exception("[DialogueCompiler] 后台编译异常")


@router.post("/ask")
async def ask_agent(body: AgentQueryRequest, background_tasks: BackgroundTasks = None):
    from core.semantic_cache import get_cache as get_semantic_cache
    from core.agents.researcher import Researcher
    from core.agents.writer import Writer
    from core.agents.reviewer import Reviewer
    from core.agents.coordinator import Coordinator
    from core.memory import extract_entities_from_graph
    novel = _resolve_novel(body.novel)
    if not os.path.exists(_wiki_path(novel)):
        return {"report": "数据未就绪"}

    # 语义缓存查詢（key 带书籍前缀，避免跨书串答）
    cache = get_semantic_cache()
    cache_key = f"[{novel}] {body.query}"
    cached_report = cache.get(cache_key)
    if cached_report is not None:
        logger.info(f"  [SemanticCache] ✅ 缓存命中: {body.query[:30]}...")
        session_id = body.session_id or memory.new_session(body.query, novel=novel)
        # 缓存命中不编译（无增量），只记完整对话原料
        entities = extract_entities_from_graph(body.query, cached_report, novel)
        memory.append_turn(session_id, body.query, cached_report, novel, True, entities)
        return {
            "report": cached_report,
            "rounds": 0,
            "intent": "cached",
            "session_id": session_id,
            "cached": True,
        }

    related = memory.search(body.query)
    related_context = ""
    if related:
        summaries = [f"[历史] {r['title']}: {r['summary'][:200]}" for r in related if r.get('summary')]
        if summaries:
            related_context = "\n".join(summaries)
    augmented_query = body.query
    if related_context:
        augmented_query = f"{body.query}\n\n【相关历史】\n{related_context}"
    retriever = await run_in_threadpool(_get_retriever, novel)
    researcher = Researcher(retriever)
    writer = Writer()
    reviewer = Reviewer()
    coordinator = Coordinator(researcher, writer, reviewer)
    result = await run_in_threadpool(coordinator.run, augmented_query)
    session_id = body.session_id or memory.new_session(body.query, novel=novel)
    report = result.get("final_report", "")
    review_passed = result.get("review_result", {}).get("passed", False)

    # 实体提取（不调 LLM，从图谱节点做子串匹配）
    entities = extract_entities_from_graph(body.query, report, novel)
    # 写入完整对话原料
    memory.append_turn(session_id, body.query, report, novel, review_passed, entities)

    # 只有 review 通过的问答才触发对话 Wiki 编译（后台任务，异常不阻塞响应）
    if review_passed and report and background_tasks is not None:
        background_tasks.add_task(_compile_dialogue_wiki, session_id, novel, memory)

    # 写入语义缓存（key 带书籍前缀，与查询时一致）
    if report:
        cache.put(cache_key, report)
    return {
        "report": report,
        "rounds": result.get("rounds", 0),
        "intent": result.get("intent", ""),
        "session_id": session_id,
    }


@router.post("/ask/stream")
async def ask_agent_stream(body: AgentQueryRequest):
    """SSE 流式 Agent 分析（真流式：进度事件 + 报告草稿逐 token 推送）"""
    from core.agents.researcher import Researcher
    from core.agents.writer import Writer
    from core.agents.reviewer import Reviewer
    from core.agents.coordinator import Coordinator
    from core.memory import extract_entities_from_graph
    from fastapi.responses import StreamingResponse
    import asyncio

    novel = _resolve_novel(body.novel)

    async def event_stream():
        loop = asyncio.get_running_loop()
        que: asyncio.Queue = asyncio.Queue()

        def cb(ev):
            # 图在工作线程中执行，事件经 loop 线程安全地转入 async 队列
            loop.call_soon_threadsafe(que.put_nowait, ev)

        async def work():
            try:
                # 语义缓存：命中则跳过整条 Agent 链路（与 /api/ask 行为一致）。
                # key 带书籍前缀，避免跨书串答
                from core.semantic_cache import get_cache as get_semantic_cache
                cache = get_semantic_cache()
                cache_key = f"[{novel}] {body.query}"
                cached = cache.get(cache_key)
                if cached is not None:
                    logger.info(f"  [SemanticCache] ✅ 缓存命中: {body.query[:30]}...")
                    session_id = body.session_id or memory.new_session(body.query, novel=novel)
                    # 缓存命中不编译（无增量），只记完整对话原料
                    entities = extract_entities_from_graph(body.query, cached, novel)
                    memory.append_turn(session_id, body.query, cached, novel, True, entities)
                    cb({"event": "progress", "message": "命中语义缓存，直接返回"})
                    cb({"event": "result", "report": cached, "rounds": 0,
                        "intent": "cached", "cached": True, "session_id": session_id})
                    return

                retriever = await run_in_threadpool(_get_retriever, novel)
                coordinator = Coordinator(Researcher(retriever), Writer(), Reviewer())
                result = await run_in_threadpool(
                    coordinator.run, body.query, event_cb=cb)
                report = result.get("final_report", "")
                # 写入语义缓存（旧版流式路由只读不写，缓存永远不会命中）
                if report:
                    cache.put(cache_key, report)

                # 会话记忆 + 对话 Wiki 编译（与 /api/ask 对齐：
                # 旧流式路由不写记忆，多轮对话和对话 Wiki 在流式入口下静默失效）
                session_id = body.session_id or memory.new_session(body.query, novel=novel)
                review_passed = result.get("review_result", {}).get("passed", False)
                entities = extract_entities_from_graph(body.query, report, novel)
                memory.append_turn(session_id, body.query, report, novel, review_passed, entities)

                # 只有 review 通过才触发对话 Wiki 编译（后台执行，不阻塞 SSE 收尾）
                if review_passed and report:
                    async def _bg_compile(sid=session_id):
                        try:
                            await run_in_threadpool(
                                _compile_dialogue_wiki, sid, novel, memory)
                        except Exception:
                            logger.exception("[DialogueCompiler] 后台编译异常")
                    asyncio.create_task(_bg_compile())

                cb({"event": "result",
                    "report": report,
                    "rounds": result.get("rounds", 0),
                    "intent": result.get("intent", ""),
                    "review": result.get("review_result", {}),
                    "session_id": session_id})
            except Exception as e:
                logger.exception("流式问答失败")
                cb({"event": "error", "message": str(e)})
            finally:
                cb(None)  # 结束哨兵

        task = asyncio.create_task(work())
        try:
            yield f"data: {json.dumps({'event': 'start', 'message': '开始分析...'}, ensure_ascii=False)}\n\n"
            while True:
                ev = await que.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/ask/auto")
async def ask_agent_auto(body: AgentQueryRequest):
    """自主 Agent 问答（工具驱动模式，LLM 自行选择检索工具）"""
    from core.agents.tool_agent import (
        SearchWikiTool, SearchGraphTool, SearchVectorTool,
        GenerateReportTool, ReviewReportTool,
        ToolRegistry, AutonomousCoordinator,
    )
    from core.material_pool import MaterialPool
    from core.agents.writer import Writer
    from core.agents.reviewer import Reviewer

    novel = _resolve_novel(body.novel)
    retriever = await run_in_threadpool(_get_retriever, novel)
    pool = MaterialPool(llm_compress=True, max_rounds=3)

    registry = ToolRegistry()
    registry.register(SearchWikiTool(retriever))
    registry.register(SearchGraphTool(retriever))
    registry.register(SearchVectorTool(retriever))
    registry.register(GenerateReportTool(Writer(), pool))
    registry.register(ReviewReportTool(Reviewer(), []))

    coordinator = AutonomousCoordinator(registry)
    result = await run_in_threadpool(coordinator.run, body.query)

    return {
        "report": result.get("final_report", ""),
        "rounds": result.get("rounds", 0),
        "llm_calls": result.get("llm_calls", 0),
        "mode": "autonomous",
    }


@router.get("/novels")
async def list_novels():
    """列出已有图谱/编译数据的书籍"""
    import glob
    # 同时检查 _graph.json 和 _hierarchical.json
    all_names = set()

    for gf in glob.glob("data/wiki/*_graph.json"):
        name = os.path.basename(gf).replace("_graph.json", "")
        if name != "test":
            all_names.add(name)

    for hf in glob.glob("data/wiki/*_hierarchical.json"):
        name = os.path.basename(hf).replace("_hierarchical.json", "")
        if name != "test":
            all_names.add(name)

    # 显示名称映射
    display_names = {
        "shaosong": "绍宋",
        "绍宋": "绍宋",
        "斗破苍穹": "斗破苍穹",
        "神印王座": "神印王座",
    }

    novels = []
    for name in sorted(all_names):
        graph_path = f"data/wiki/{name}_graph.json"
        wiki_path = f"data/wiki/{name}_hierarchical.json"
        # 去掉"作者：xxx"后缀，提取核心名
        clean_name = name.replace("作者：", " ").strip().split(" ")[0] if "作者：" in name else name
        display = display_names.get(clean_name) or display_names.get(name) or name
        novels.append({
            "name": name,
            "display_name": display,
            "has_graph": os.path.exists(graph_path),
            "has_wiki": os.path.exists(wiki_path),
        })
    return novels


@router.get("/chapter")
async def get_chapter(keyword: str = "", novel: str = "绍宋"):
    """
    搜索原文中匹配关键词的章节内容。

    修复 (2026-07-10):
      - 支持「引自第一章 明道宫」等完整引用格式
      - 按章节号直接定位（如「第1章」「第一章」）
      - 模糊匹配兜底（取相似度最高）
      - 跨目录自动检测 data 位置
    """
    if not keyword:
        return {"chapters": []}

    _safe_name(novel)
    # ── 定位小说 JSON 文件 ──
    import glob

    # 清理关键词用于匹配
    kw_raw = keyword.replace("引自 ", "").replace("引自", "").replace("出自 ", "").replace("出自", "")
    kw_raw = kw_raw.strip()

    # 候选路径：优先 novel_project/data/processed/
    candidates = []
    for base in ["novel_project/data/processed", "data/processed"]:
        if os.path.exists(base):
            candidates.extend(glob.glob(os.path.join(base, f"*{novel}*.json")))
            # 去掉 _chunks.json 只取主文件
            candidates = [p for p in candidates if not p.endswith("_chunks.json") and not p.endswith("_chunks.json")]
    if not candidates:
        candidates = sorted(glob.glob("**/*绍宋*.json", recursive=True))
    if not candidates:
        # 全局搜索
        for base in ["novel_project/data/processed", "data/processed"]:
            for f in ["《绍宋》作者：榴弹怕水.json", "绍宋.json", "shaosong.json"]:
                p = os.path.join(base, f)
                if os.path.exists(p):
                    candidates.append(p)

    novel_data = None
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    novel_data = json.load(f)
                break
            except Exception:
                continue

    if not novel_data:
        return {"chapters": [], "error": "数据文件不存在"}

    chapters = novel_data.get("chapters", [])

    # ── 辅助函数 ──
    def clean_name(name: str) -> str:
        """标准化章节名用于匹配"""
        n = name.lower()
        n = re.sub(r"[\s　,，、　]", "", n)
        return n

    results = []

    # ── 策略 1: 提取章节号直接定位（按标题编号查找，兼容前言偏移）──
    from core.cn_num import chinese_to_int, find_chapter_by_number
    ch_num = None
    # 匹配 "第一章" "第1章" "第100章"
    num_match = re.search(r"第\s*([一二两三四五六七八九十百千零\d]+)\s*[章回]", kw_raw)
    if num_match:
        ch_num = chinese_to_int(num_match.group(1).strip())

    if ch_num:
        ch = find_chapter_by_number(chapters, ch_num)
        if ch:
            results.append({
                "chapter_index": ch.get("chapter_index", ch_num - 1),
                "chapter_title": ch.get("title", ch.get("chapter_title", f"第{ch_num}章")),
                "snippet": ch.get("text", "")[:800],
                "text_length": len(ch.get("text", "")),
                "match_type": "exact_index",
            })
            return {"chapters": results, "total": len(results)}

    # ── 策略 2: 标题精确匹配 ──
    kw_clean = clean_name(kw_raw)
    for ch in chapters:
        title_raw = ch.get("title", ch.get("chapter_title", ""))
        title_clean = clean_name(title_raw)

        # 去除"第""章"后的核心匹配
        kw_core = re.sub(r"第[一-鿿\d]+[章回]", "", kw_clean)
        title_core = re.sub(r"第[一-鿿\d]+[章回]", "", title_clean)

        if kw_clean == title_clean or kw_core == title_core or kw_core in title_clean or title_core in kw_clean:
            results.append({
                "chapter_index": ch.get("chapter_index", 0),
                "chapter_title": title_raw,
                "snippet": ch.get("text", "")[:500],
                "text_length": len(ch.get("text", "")),
                "match_type": "title",
            })
            if len(results) >= 3:
                break

    if results:
        return {"chapters": results, "total": len(results)}

    # ── 策略 3: 正文关键词匹配 ──
    kw_text = kw_clean
    for ch in chapters:
        text = ch.get("text", "")
        if not text:
            continue
        text_clean = clean_name(text)
        if kw_text in text_clean:
            idx = text_clean.find(kw_text)
            start = max(0, idx - 80)
            end = min(len(text), idx + 250)
            results.append({
                "chapter_index": ch.get("chapter_index", 0),
                "chapter_title": ch.get("title", ch.get("chapter_title", "")),
                "snippet": text[start:end] if start >= 0 else text[:300],
                "text_length": len(text),
                "match_type": "text",
            })
            if len(results) >= 5:
                break

    if results:
        return {"chapters": results, "total": len(results)}

    # ── 策略 4: 拆分关键词逐字匹配兜底 ──
    # 把 "一章 明道宫" 拆成 ["明道宫"] 再匹配
    kw_parts = re.findall(r"[一-鿿]{2,}", kw_clean)
    for ch in chapters[:10]:
        title_raw = ch.get("title", ch.get("chapter_title", ""))
        title_clean = clean_name(title_raw)
        for part in kw_parts:
            if len(part) >= 2 and part in title_clean:
                results.append({
                    "chapter_index": ch.get("chapter_index", 0),
                    "chapter_title": title_raw,
                    "snippet": ch.get("text", "")[:300],
                    "text_length": len(ch.get("text", "")),
                    "match_type": "fuzzy",
                })
                break

    if not results:
        # 完全无结果 → 返回前 3 章的标题让用户知道有哪些内容
        for i, ch in enumerate(chapters[:3]):
            results.append({
                "chapter_index": ch.get("chapter_index", i),
                "chapter_title": ch.get("title", ch.get("chapter_title", f"第{i+1}章")),
                "snippet": "",
                "text_length": len(ch.get("text", "")),
                "match_type": "fallback",
            })

    return {"chapters": results, "total": len(results)}


@router.get("/graph")
async def get_graph(novel: str = "", request: Request = None):
    """获取知识图谱数据，指定 novel 参数可切换书籍（含自适应布局参数 + ETag 缓存）"""
    if not novel:
        novels = await list_novels()
        return {"nodes": [], "edges": [], "novels": novels}

    _safe_name(novel)
    graph_path = f"data/wiki/{novel}_graph.json"
    if not os.path.exists(graph_path):
        return {"nodes": [], "edges": [], "error": f"图谱未构建，请先上传并编译"}

    # ETag: 基于文件修改时间 + 大小（MD5 哈希确保纯 ASCII）
    stat = os.stat(graph_path)
    etag_raw = f"{novel}-{stat.st_mtime:.0f}-{stat.st_size}"
    etag = '"' + hashlib.md5(etag_raw.encode()).hexdigest() + '"'

    # 客户端缓存匹配则返回 304
    if request:
        if_none_match = request.headers.get("If-None-Match", "")
        if if_none_match == etag:
            from fastapi.responses import Response
            return Response(status_code=304)

    with open(graph_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 自适应布局参数
    n = len(data.get("nodes", []))
    e = len(data.get("edges", []))
    density = e / max(n * (n - 1) / 2, 1)

    # 节点斥力: 大图需要极强斥力
    repulsion = int(min(100000 * (max(n, 10) / 100) ** 2.0, 3000000))

    # 重力: 大图需要极低重力
    gravity = round(max(0.003, min(0.08, 0.04 * (100 / max(n, 10)) ** 0.4)), 4)

    # 理想边长: 随 sqrt(N) 增长
    ideal = int(min(100 + n ** 0.5 * 6, 300))

    # 迭代次数: 随 N 增长但上限 3000
    iterations = min(2000 + n * 2, 3000)

    # 节点尺寸范围: 随 N 增长缩小
    max_node_size = max(8, min(40, 55 - n * 0.02))
    min_node_size = max(2, min(8, 5 - n * 0.002))

    # 分离阈值: 与节点尺寸联动
    sep_threshold = int(min(30 + n * 0.02, 60))

    # 标签显隐阈值: 随 N 比例缩放
    label_hide_full = max(0.05, 0.15 - n * 0.00008)
    label_hide_core = max(0.10, 0.30 - n * 0.00015)

    # 边上限: 按权重裁剪
    edge_limit = min(300 + n * 0.3, 600)

    # 最小关联度数
    min_degree = max(2, min(5, int(n * 0.005)))

    config = {
        "nodeRepulsion": repulsion,
        "gravity": gravity,
        "idealEdgeLength": ideal,
        "numIter": iterations,
        "maxNodeSize": round(max_node_size, 1),
        "minNodeSize": round(min_node_size, 1),
        "sepThreshold": sep_threshold,
        "labelHideFull": round(label_hide_full, 3),
        "labelHideCore": round(label_hide_core, 3),
        "edgeLimit": int(edge_limit),
        "minDegree": min_degree,
        "totalNodes": n,
        "totalEdges": e,
        "density": round(density, 6),
    }

    data["layout"] = config
    from fastapi.responses import JSONResponse
    return JSONResponse(content=data, headers={"ETag": etag})



# ── 图谱手动修正 ─────────────────────────────────────────────────────

@router.post("/graph/edit/delete-edge")
async def graph_delete_edge(data: dict, request: Request):
    """删除一条关系。{"novel": "绍宋作者：榴弹怕水", "source": "赵玖", "target": "岳飞"}"""
    from core.security import JWTHandler
    from core.chapter_parser import _backup, _atomic_write
    auth = request.headers.get("Authorization", "")
    payload = JWTHandler.get_default().decode(auth[7:]) if auth.startswith("Bearer ") else None
    if not payload or payload.get("role") not in ("admin", "editor"):
        raise HTTPException(status_code=403, detail="仅编辑和管理员可修改图谱")
    novel = data.get("novel", "")
    source = data.get("source", "")
    target = data.get("target", "")
    if not all([novel, source, target]):
        raise HTTPException(status_code=400, detail="参数缺失")
    _safe_name(novel)
    path = f"data/wiki/{novel}_graph.json"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="图谱文件不存在")
    with open(path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    before = len(graph.get("edges", []))
    graph["edges"] = [e for e in graph.get("edges", []) if not (e["source"] == source and e["target"] == target)]
    removed = before - len(graph["edges"])
    if removed == 0:
        raise HTTPException(status_code=404, detail="未找到该关系")
    _backup(path)
    _atomic_write(graph, path)
    return {"status": "ok", "removed": removed}


@router.post("/graph/edit/merge-nodes")
async def graph_merge_nodes(data: dict, request: Request):
    """合并两个人物节点。{"novel": "绍宋作者：榴弹怕水", "source": "赵管家", "target": "赵玖"}"""
    from core.security import JWTHandler
    from core.chapter_parser import _backup, _atomic_write
    auth = request.headers.get("Authorization", "")
    payload = JWTHandler.get_default().decode(auth[7:]) if auth.startswith("Bearer ") else None
    if not payload or payload.get("role") not in ("admin", "editor"):
        raise HTTPException(status_code=403, detail="仅编辑和管理员可修改图谱")
    novel = data.get("novel", "")
    source = data.get("source", "")
    target = data.get("target", "")
    if not all([novel, source, target]):
        raise HTTPException(status_code=400, detail="参数缺失")
    _safe_name(novel)
    path = f"data/wiki/{novel}_graph.json"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="图谱文件不存在")
    with open(path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    # 把 source 的所有边迁移到 target
    for e in graph.get("edges", []):
        if e["source"] == source:
            e["source"] = target
        if e["target"] == source:
            e["target"] = target
    # 删除 source 节点
    graph["nodes"] = [n for n in graph.get("nodes", []) if n["name"] != source]
    # 删除自指边
    graph["edges"] = [e for e in graph.get("edges", []) if e["source"] != e["target"]]
    _backup(path)
    _atomic_write(graph, path)
    return {"status": "ok", "merged": source, "into": target}


@router.post("/graph/edit/update-relation")
async def graph_update_relation(data: dict, request: Request):
    """修改关系描述。{"novel": "绍宋作者：榴弹怕水", "source": "赵玖", "target": "岳飞", "relation": "君臣关系"}"""
    from core.security import JWTHandler
    from core.chapter_parser import _backup, _atomic_write
    auth = request.headers.get("Authorization", "")
    payload = JWTHandler.get_default().decode(auth[7:]) if auth.startswith("Bearer ") else None
    if not payload or payload.get("role") not in ("admin", "editor"):
        raise HTTPException(status_code=403, detail="仅编辑和管理员可修改图谱")
    novel = data.get("novel", "")
    source = data.get("source", "")
    target = data.get("target", "")
    new_rel = data.get("relation", "")
    if not all([novel, source, target, new_rel]):
        raise HTTPException(status_code=400, detail="参数缺失")
    _safe_name(novel)
    path = f"data/wiki/{novel}_graph.json"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="图谱文件不存在")
    with open(path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    updated = 0
    for e in graph.get("edges", []):
        if e["source"] == source and e["target"] == target:
            e["relation"] = new_rel
            updated += 1
    if updated == 0:
        raise HTTPException(status_code=404, detail="未找到该关系")
    _backup(path)
    _atomic_write(graph, path)
    return {"status": "ok", "updated": updated}


@router.get("/graph/edit/check")
async def graph_edit_check(request: Request):
    """检查当前用户是否有编辑权限"""
    from core.security import JWTHandler
    auth = request.headers.get("Authorization", "")
    payload = JWTHandler.get_default().decode(auth[7:]) if auth.startswith("Bearer ") else None
    role = payload.get("role", "") if payload else ""
    return {"editable": role in ("admin", "editor")}


@router.get("/cache/stats")
async def cache_stats():
    """语义缓存统计"""
    from core.semantic_cache import get_cache as get_semantic_cache
    return get_semantic_cache().stats()


@router.post("/cache/clear")
async def cache_clear(request: Request = None):
    """清空语义缓存"""
    from core.semantic_cache import get_cache as get_semantic_cache
    get_semantic_cache().clear()
    return {"status": "cleared"}


@router.post("/index")
async def index_novel(data: dict, request: Request = None):
    """
    将已编译的 novels 索引到向量库。
    {"novel": "shaosong"}
    """
    novel = data.get("novel", "shaosong")
    _safe_name(novel)
    import glob

    from core.chunker import NOVEL_SHORT_TO_FULLNAME
    name = NOVEL_SHORT_TO_FULLNAME.get(novel, novel)
    filepath = f"data/processed/{name}.json"

    if not os.path.exists(filepath):
        return {"status": "error", "message": f"文件不存在: {filepath}"}

    with open(filepath, "r", encoding="utf-8") as f:
        novel_data = json.load(f)

    from core.chunker import NovelChunker, VectorStoreIndexer
    chunker = NovelChunker(chunk_size=512, overlap=128)
    chunks = chunker.chunk_novel(novel_data, novel_key=novel)

    indexer = VectorStoreIndexer()
    result = indexer.index_novel(novel, chunks)

    return {
        "status": "ok" if result.get("status") == "ok" else "partial",
        "novel": novel,
        "total_chunks": len(chunks),
        "indexed": result.get("indexed", 0),
    }


# ---- 上传与编译 ----

@router.post("/upload")
async def upload_novel(file: UploadFile = File(...), request: Request = None):
    """
    上传文档，返回章节数供用户选择。

    支持格式: .txt (逐步扩展 .docx .pdf .md)
    """
    from core.document_parser import get_router
    from core.security import FileValidator
    from core.security import ROLE_HIERARCHY

    # 权限校验
    role = getattr(request.state, 'role', 'viewer') if request else 'viewer'
    if ROLE_HIERARCHY.get(role, 0) < 50:
        raise HTTPException(status_code=403, detail='权限不足，需要 editor 或 admin 角色')

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()

    # 格式校验
    router = get_router()
    if ext not in router.supported_extensions():
        supported = router.supported_formats_display()
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}。当前支持: {supported}",
        )

    # 文件内容安全校验
    content = await file.read()
    validation = FileValidator.validate(filename, content)
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=validation["error"])

    # 保存到 raw 目录
    raw_path = f"data/raw/{filename}"
    os.makedirs("data/raw", exist_ok=True)
    with open(raw_path, "wb") as f:
        f.write(content)

    # 用 DocumentRouter 解析（同步重型解析移出事件循环）
    try:
        result = await run_in_threadpool(router.parse, raw_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {e}")

    # 同步保存为 processed JSON（兼容现有编译管道）
    import json
    from core.chunker import NovelChunker

    title = result.get("title", "").replace("《", "").replace("》", "").strip()
    processed_dir = "data/processed"
    os.makedirs(processed_dir, exist_ok=True)
    processed_path = f"{processed_dir}/{filename.replace(ext, '.json')}"

    chapters = result["chapters"]
    novel_data = {
        "title": title or filename.replace(ext, ""),
        "chapters": chapters,
    }

    def _write_processed():
        with open(processed_path, "w", encoding="utf-8") as f:
            json.dump(novel_data, f, ensure_ascii=False, indent=2)

    # 大 JSON 写盘同样移出事件循环
    await run_in_threadpool(_write_processed)

    # 可选：用分块引擎预分块并保存（同步重型分块移出事件循环）
    chunker = NovelChunker(chunk_size=512, overlap=128)
    chunks = await run_in_threadpool(chunker.chunk_novel, novel_data, novel_key=title or filename)
    chunk_path = f"data/processed/{filename.replace(ext, '_chunks.json')}"

    def _write_chunks():
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump([c.to_dict() for c in chunks], f, ensure_ascii=False, indent=2)

    await run_in_threadpool(_write_chunks)

    return {
        "filename": filename,
        "format": result.get("metadata", {}).get("format", "txt"),
        "total_chapters": len(chapters),
        "total_chunks": len(chunks),
        "chars_total": result.get("metadata", {}).get("chars_total", 0),
        "chars_cleaned": result.get("metadata", {}).get("chars_cleaned", 0),
        "message": f"解析完成，共 {len(chapters)} 章，{len(chunks)} 个语义块",
        "file_info": result.get("metadata", {}),
    }


@router.post("/build")
async def build_wiki_api(data: dict, background_tasks: BackgroundTasks, request: Request = None):
    """
    开始编译（后台异步执行，前端轮询进度）
    {"filename": "xxx.txt", "chapters": 50} 或 {"filename": "xxx.txt", "chapters": -1}（全量）
    增量编译（复用已完成章节的断点，只编译新增章节）:
    {"filename": "xxx.txt", "chapters": -1, "incremental": true}
    """
    from core.chapter_parser import CheckpointManager
    import uuid

    filename = data.get("filename")
    chapters = data.get("chapters", -1)
    start_chapter = data.get("start_chapter")
    end_chapter = data.get("end_chapter")
    incremental = bool(data.get("incremental", False))

    if not filename:
        raise HTTPException(status_code=400, detail="参数缺失: filename")
    _safe_name(filename, "文件名")

    ext = os.path.splitext(filename)[1]
    novel_name = filename.replace(ext, "").replace("《", "").replace("》", "")
    _safe_name(novel_name)

    # 清空旧断点（仅全量编译；增量编译保留断点以复用已完成章节）
    if not incremental:
        cpm = CheckpointManager(novel_name)
        cpm.reset("wiki")
        cpm.reset("volume")
        cpm.reset("book")

    # 保存总章节数到 meta 文件（让进度接口能读到）
    total_chapters = 0
    processed_path = f"data/processed/{filename.replace(ext, '.json')}"
    if os.path.exists(processed_path):
        with open(processed_path, "r", encoding="utf-8") as f:
            novel = json.load(f)
        total_chapters = len(novel.get("chapters", []))
    # 写入 meta 文件
    os.makedirs("data/checkpoints", exist_ok=True)
    with open(f"data/checkpoints/{novel_name}_meta.json", "w", encoding="utf-8") as f:
        json.dump({"novel": novel_name, "total_chapters": total_chapters}, f)

    # 后台任务
    background_tasks.add_task(_run_build, filename, chapters, novel_name, start_chapter, end_chapter, incremental)

    return {
        "status": "started",
        "novel": novel_name,
        "total_chapters": total_chapters,
        "incremental": incremental,
        "message": f"{'增量' if incremental else '全量'}编译已启动，共 {total_chapters} 章",
    }


# ── 编译暂停控制 ────────────────────────────────────────────────────────
_build_paused: dict[str, bool] = {}

@router.post("/build/pause")
async def pause_build(data: dict):
    """暂停编译 {"novel": "绍宋作者：榴弹怕水"} """
    novel = data.get("novel", "")
    if novel:
        _build_paused[novel] = True
        return {"status": "paused", "novel": novel}
    return {"status": "error", "detail": "novel required"}

@router.post("/build/resume")
async def resume_build(data: dict):
    """恢复编译 {"novel": "绍宋作者：榴弹怕水"} """
    novel = data.get("novel", "")
    if novel:
        _build_paused[novel] = False
        return {"status": "resumed", "novel": novel}
    return {"status": "error", "detail": "novel required"}

@router.get("/build/status")
async def build_status(novel: str = ""):
    """获取编译运行状态"""
    return {
        "novel": novel,
        "paused": _build_paused.get(novel, False),
        "running": novel in _build_paused,
    }


def _run_build(filename: str, chapters: int, novel_name: str, start_chapter: int = None, end_chapter: int = None, incremental: bool = False):
    """后台执行编译（支持暂停；incremental=True 时复用断点只编译新增章节）"""
    import json, os, tempfile
    from core.chapter_parser import (
        CheckpointManager, build_wiki, build_volume_summaries,
        build_book_summary, save_hierarchical_wiki,
    )

    ext = os.path.splitext(filename)[1]
    processed_path = f"data/processed/{filename.replace(ext, '.json')}"

    with open(processed_path, "r", encoding="utf-8") as f:
        novel = json.load(f)

    if chapters > 0:
        novel["chapters"] = novel["chapters"][:chapters + 1]
    elif start_chapter is not None and end_chapter is not None:
        s = max(0, start_chapter - 1)
        e = min(len(novel["chapters"]), end_chapter)
        novel["chapters"] = novel["chapters"][s:e]
        total_in_range = e - s
        # 更新 meta 文件中的总数
        meta_path = f"data/checkpoints/{novel_name}_meta.json"
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
            meta["total_chapters"] = total_in_range
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(meta, mf)
        logger.info(f"范围编译: 第{start_chapter}~{end_chapter}章 (共 {total_in_range} 章)")

    os.makedirs("data/wiki", exist_ok=True)
    wiki_path = f"data/wiki/{novel_name}_wiki.json"

    try:
        # 编译开始前置为未暂停
        _build_paused[novel_name] = False

        def _is_paused() -> bool:
            return _build_paused.get(novel_name, False)

        wiki = build_wiki(
            novel, batch_size=5, delay=2,
            checkpoint_path=wiki_path, novel_key=novel_name,
            pause_check=_is_paused,
            incremental=incremental,
        )

        if incremental and wiki:
            # 追加章节会使末卷内容增长：作废末卷摘要与全书摘要的断点，
            # 其余已完成卷的摘要断点保留复用
            from core.chapter_parser import _detect_natural_volumes
            vol_groups = _detect_natural_volumes(wiki)
            last_vol_start = vol_groups[-1][1] if vol_groups else ((len(wiki) - 1) // 50) * 50
            cpm = CheckpointManager(novel_name)
            cpm.unmark(last_vol_start, "volume")
            cpm.reset("book")

        volumes = build_volume_summaries(wiki, volume_size=50, novel_key=novel_name)
        book = build_book_summary(wiki, volumes, novel_key=novel_name)
        hier_path = f"data/wiki/{novel_name}_hierarchical.json"
        save_hierarchical_wiki(wiki, volumes, book, hier_path)

        # 别名映射表
        alias_mapping = {}
        for entry in wiki:
            for c in entry.get("characters", []):
                name = c.get("name", "")
                aliases = c.get("aliases", [])
                if name and aliases:
                    if name not in alias_mapping:
                        alias_mapping[name] = {"main_entity": name, "aliases": []}
                    existing = set(alias_mapping[name]["aliases"])
                    for a in aliases:
                        if a and a != name and a not in existing:
                            alias_mapping[name]["aliases"].append(a)
        alias_path = f"data/wiki/{novel_name}_alias.json"
        with open(alias_path, "w", encoding="utf-8") as f:
            json.dump(alias_mapping, f, ensure_ascii=False, indent=2)
        logger.info(f"别名映射已保存: {alias_path}")

        # 图谱
        from core.knowledge_graph import merge_characters, merge_relationships, build_graph, save_graph
        char_map = merge_characters(wiki)
        rels = merge_relationships(wiki)
        G = build_graph(char_map, rels)
        graph_path = f"data/wiki/{novel_name}_graph.json"

        # 增量编译时先取出旧社区摘要（save_graph 会覆盖图谱文件），
        # 成员集合未变的社区直接复用，跳过 LLM 调用
        prev_summaries = None
        if incremental:
            try:
                from core.graph_community import load_community_data
                prev_summaries = (load_community_data(graph_path) or {}).get("summaries")
            except Exception:
                prev_summaries = None

        save_graph(G, graph_path)

        # 社区检测 + 摘要（编译流程末尾）
        try:
            from core.graph_community import detect_communities, generate_community_summaries, save_community_data
            communities = detect_communities(G)
            community_summaries = generate_community_summaries(
                G, communities, wiki, novel_name, cached_summaries=prev_summaries)
            save_community_data(communities, community_summaries, graph_path)
            logger.info("社区检测完成: %d 个社区", len(community_summaries))
        except Exception:
            logger.warning("社区检测跳过（非致命）")

    except Exception as e:
        logger.exception(f"编译失败: {e}")
    finally:
        # 写入完成标记，前端据此停止轮询
        meta_path = f"data/checkpoints/{novel_name}_meta.json"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                meta["build_complete"] = True
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f)
            except Exception:
                pass
        # 清理暂停状态
        _build_paused.pop(novel_name, None)


@router.get("/build/progress")
async def get_build_progress(novel: str = ""):
    """获取编译进度（基于断点文件 + 源数据估算）"""
    if not novel:
        return {"progress": 0, "total": 0, "completed": 0, "phase": "", "build_complete": False}

    _safe_name(novel)
    # 从 meta 文件读取总章节数和完成标记
    meta_path = f"data/checkpoints/{novel}_meta.json"
    total = 0
    build_complete = False
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                m = json.load(f)
            total = m.get("total_chapters", 0)
            build_complete = m.get("build_complete", False)
        except Exception:
            pass

    # 从断点文件读取已完成数
    ck_path = f"data/checkpoints/{novel}_wiki_checkpoint.json"
    wiki_done = 0
    if os.path.exists(ck_path):
        try:
            with open(ck_path, "r", encoding="utf-8") as f:
                ck = json.load(f)
            wiki_done = len(ck.get("completed_indices", []))
        except Exception:
            pass

    if total == 0:
        total = max(wiki_done, 1)

    # build_complete 为 true 时才返回 100%
    if build_complete:
        display_progress = 100
    else:
        raw_progress = wiki_done / max(total, 1) * 100
        display_progress = max(1, min(99, int(raw_progress))) if wiki_done > 0 else 0

    return {
        "progress": display_progress,
        "total": total,
        "completed": wiki_done,
        "phase": "wiki",
        "build_complete": build_complete,
    }


@router.get("/build/failed")
async def get_failed_chapters(novel: str = ""):
    """获取指定书籍的失败章节清单"""
    if not novel:
        return {"failed": []}
    _safe_name(novel)
    from core.chapter_parser import FailedChaptersManager
    mgr = FailedChaptersManager(novel)
    return {"failed": mgr.get_all()}


@router.post("/build/retry")
async def retry_chapter(data: dict):
    """
    重试单章编译。
    {"novel": "shaosong", "chapter_index": 5}
    """
    novel = data.get("novel", "")
    chapter_index = data.get("chapter_index", -1)

    if novel == "" or chapter_index < 0:
        raise HTTPException(status_code=400, detail="参数缺失")
    _safe_name(novel)

    # 加载数据
    from core.chunker import NOVEL_SHORT_TO_FULLNAME
    name = NOVEL_SHORT_TO_FULLNAME.get(novel, novel)
    import glob
    processed_path = f"data/processed/{name}.json"
    if not os.path.exists(processed_path):
        raise HTTPException(status_code=404, detail="数据文件不存在")

    with open(processed_path, "r", encoding="utf-8") as f:
        novel_data = json.load(f)

    chapters = novel_data.get("chapters", [])
    if chapter_index >= len(chapters):
        raise HTTPException(status_code=400, detail="章节索引超出范围")

    ch = chapters[chapter_index]
    ch_title = ch.get("title", ch.get("chapter_title", f"第{chapter_index}章"))
    ch_text = ch.get("text", "")

    from core.chapter_parser import parse_chapter, FailedChaptersManager
    try:
        result = parse_chapter(ch_title, ch_text, chapter_index=chapter_index)
        if result and result.get("characters"):
            failed_mgr = FailedChaptersManager(novel)
            failed_mgr.remove(chapter_index)
            # 更新断点文件
            wiki_path = f"data/wiki/{novel}_wiki.json"
            if os.path.exists(wiki_path):
                with open(wiki_path, "r", encoding="utf-8") as f:
                    wiki_data = json.load(f)
                result["chapter_index"] = chapter_index
                result["chapter_title"] = ch_title
                existing_idx = next(
                    (pos for pos, e in enumerate(wiki_data)
                     if e.get("chapter_index") == chapter_index),
                    None,
                )
                if existing_idx is not None:
                    wiki_data[existing_idx] = result
                else:
                    wiki_data.insert(chapter_index, result)
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix=".tmp", dir="data/wiki")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(wiki_data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, wiki_path)

            return {"status": "ok", "chapter_index": chapter_index, "title": ch_title}
        return {"status": "failed", "detail": "编译返回空结果"}
    except Exception as e:
        return {"status": "failed", "detail": str(e)}
