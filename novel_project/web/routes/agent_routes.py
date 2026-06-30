"""
API 路由：检索、问答、图谱、上传、编译
"""
import sys, os, json, time
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from core.memory import SessionMemory

router = APIRouter()
memory = SessionMemory()

WIKI_PATH = "data/wiki/shaosong_hierarchical.json"
GRAPH_PATH = "data/wiki/shaosong_graph.json"
NOVEL_PATH = "data/processed/《绍宋》作者：榴弹怕水.json"


def _ensure_graph():
    if os.path.exists(GRAPH_PATH):
        return
    from core.knowledge_graph import merge_characters, merge_relationships, build_graph, save_graph
    from core.chapter_parser import load_wiki
    data = load_wiki(WIKI_PATH)
    chapters = data["chapters"] if isinstance(data, dict) else data
    char_map = merge_characters(chapters)
    rels = merge_relationships(chapters)
    G = build_graph(char_map, rels)
    save_graph(G, GRAPH_PATH)


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5


class AgentQueryRequest(BaseModel):
    query: str
    session_id: str = ""


@router.post("/search")
async def search(body: QueryRequest):
    from core.retriever import NovelRetriever
    if not os.path.exists(WIKI_PATH):
        return {"wiki_results": [], "graph_results": {}}
    _ensure_graph()
    retriever = NovelRetriever(WIKI_PATH, GRAPH_PATH, NOVEL_PATH)
    wiki_results = retriever.search_wiki(body.query, top_k=body.top_k)
    graph_results = retriever.search_by_graph(body.query)
    return {"wiki_results": wiki_results, "graph_results": graph_results}


@router.post("/search/vector")
async def vector_search(body: QueryRequest):
    """语义向量搜索（Level 3 检索）"""
    from core.retriever import NovelRetriever
    if not os.path.exists(WIKI_PATH):
        return {"vector_results": []}
    _ensure_graph()
    retriever = NovelRetriever(WIKI_PATH, GRAPH_PATH, NOVEL_PATH)
    results = retriever.search_by_vector(body.query, top_k=body.top_k)
    return {"vector_results": results}


@router.post("/search/all")
async def search_all(body: QueryRequest):
    """三级统一检索"""
    from core.retriever import NovelRetriever
    if not os.path.exists(WIKI_PATH):
        return {"wiki_results": [], "graph_results": {}, "vector_results": []}
    _ensure_graph()
    retriever = NovelRetriever(WIKI_PATH, GRAPH_PATH, NOVEL_PATH)
    return retriever.search(body.query, top_k=body.top_k)


@router.post("/ask")
async def ask_agent(body: AgentQueryRequest):
    from core.retriever import NovelRetriever
    from core.agents.researcher import Researcher
    from core.agents.writer import Writer
    from core.agents.reviewer import Reviewer
    from core.agents.coordinator import Coordinator
    if not os.path.exists(WIKI_PATH):
        return {"report": "数据未就绪"}
    _ensure_graph()
    related = memory.search(body.query)
    related_context = ""
    if related:
        summaries = [f"[历史] {r['title']}: {r['summary'][:200]}" for r in related if r.get('summary')]
        if summaries:
            related_context = "\n".join(summaries)
    augmented_query = body.query
    if related_context:
        augmented_query = f"{body.query}\n\n【相关历史】\n{related_context}"
    retriever = NovelRetriever(WIKI_PATH, GRAPH_PATH, NOVEL_PATH)
    researcher = Researcher(retriever)
    writer = Writer()
    reviewer = Reviewer()
    coordinator = Coordinator(researcher, writer, reviewer)
    result = coordinator.run(augmented_query)
    session_id = body.session_id or memory.new_session(body.query)
    memory.save_summary(session_id, result.get("final_report", "")[:300], [])
    return {
        "report": result.get("final_report", ""),
        "rounds": result.get("rounds", 0),
        "intent": result.get("intent", ""),
        "session_id": session_id,
    }


@router.post("/ask/stream")
async def ask_agent_stream(body: AgentQueryRequest):
    """SSE 流式 Agent 分析（带进度事件）"""
    from core.retriever import NovelRetriever
    from core.agents.researcher import Researcher
    from core.agents.writer import Writer
    from core.agents.reviewer import Reviewer
    from core.agents.coordinator import Coordinator
    from fastapi.responses import StreamingResponse

    _ensure_graph()

    async def event_stream():
        yield f"data: {json.dumps({'event': 'start', 'message': '开始分析...'})}\n\n"
        retriever = NovelRetriever(WIKI_PATH, GRAPH_PATH, NOVEL_PATH)
        researcher = Researcher(retriever)
        writer = Writer()
        reviewer = Reviewer()
        coordinator = Coordinator(researcher, writer, reviewer)

        # 包装 coordinator.run 来发送进度事件
        # 简单方案：先发进度，再返回结果
        yield f"data: {json.dumps({'event': 'progress', 'message': '意图识别中...'})}\n\n"
        intent = coordinator.detect_intent(body.query)

        yield f"data: {json.dumps({'event': 'progress', 'message': f'任务拆解中...（{intent}）'})}\n\n"
        steps = coordinator.decompose_task(body.query, intent)

        yield f"data: {json.dumps({'event': 'progress', 'message': f'共 {len(steps)} 个检索步骤，执行中...'})}\n\n"
        result = coordinator.run(body.query)

        yield f"data: {json.dumps({'event': 'result', 'report': result.get('final_report', ''), 'rounds': result.get('rounds', 0), 'intent': result.get('intent', '')})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/novels")
async def list_novels():
    """列出已有图谱的书籍"""
    import glob
    graph_files = glob.glob("data/wiki/*_graph.json")
    novels = []
    for gf in graph_files:
        name = os.path.basename(gf).replace("_graph.json", "")
        if name == "test":
            continue
        wiki_path = gf.replace("_graph.json", "_hierarchical.json")
        has_wiki = os.path.exists(wiki_path)
        novels.append({"name": name, "has_graph": True, "has_wiki": has_wiki})
    return novels


@router.get("/graph")
async def get_graph(novel: str = ""):
    """获取知识图谱数据，指定 novel 参数可切换书籍"""
    if not novel:
        novels = await list_novels()
        return {"nodes": [], "edges": [], "novels": novels}

    graph_path = f"data/wiki/{novel}_graph.json"
    if not os.path.exists(graph_path):
        return {"nodes": [], "edges": [], "error": f"图谱未构建，请先上传并编译"}

    with open(graph_path, "r", encoding="utf-8") as f:
        return json.load(f)


@router.post("/index")
async def index_novel(data: dict):
    """
    将已编译的 novels 索引到向量库。
    {"novel": "shaosong"}
    """
    novel = data.get("novel", "shaosong")
    import glob

    cn_map = {"shaosong": "《绍宋》作者：榴弹怕水"}
    name = cn_map.get(novel, novel)
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
async def upload_novel(file: UploadFile = File(...)):
    """
    上传文档，返回章节数供用户选择。

    支持格式: .txt (逐步扩展 .docx .pdf .md)
    """
    from core.document_parser import get_router
    from core.security import FileValidator

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

    # 用 DocumentRouter 解析
    try:
        result = router.parse(raw_path)
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
    with open(processed_path, "w", encoding="utf-8") as f:
        json.dump(novel_data, f, ensure_ascii=False, indent=2)

    # 可选：用分块引擎预分块并保存
    chunker = NovelChunker(chunk_size=512, overlap=128)
    chunks = chunker.chunk_novel(novel_data, novel_key=title or filename)
    chunk_path = f"data/processed/{filename.replace(ext, '_chunks.json')}"
    with open(chunk_path, "w", encoding="utf-8") as f:
        json.dump([c.to_dict() for c in chunks], f, ensure_ascii=False, indent=2)

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
async def build_wiki_api(data: dict):
    """
    开始编译
    {"filename": "xxx.txt", "chapters": 50} 或 {"filename": "xxx.txt", "chapters": -1}（全量）
    """
    filename = data.get("filename")
    chapters = data.get("chapters", -1)

    from core.chapter_parser import build_wiki, build_volume_summaries, build_book_summary, save_hierarchical_wiki
    import json

    ext = os.path.splitext(filename)[1]
    novel_name = filename.replace(ext, "").replace("《", "").replace("》", "")
    processed_path = f"data/processed/{filename.replace(ext, '.json')}"

    with open(processed_path, "r", encoding="utf-8") as f:
        novel = json.load(f)

    # 限制章节数
    if chapters > 0:
        novel["chapters"] = novel["chapters"][:chapters + 1]

    os.makedirs("data/wiki", exist_ok=True)
    wiki_path = f"data/wiki/{novel_name}_wiki.json"
    wiki = build_wiki(novel, batch_size=5, delay=2, checkpoint_path=wiki_path)
    volumes = build_volume_summaries(wiki, volume_size=50)
    book = build_book_summary(wiki, volumes)
    hier_path = f"data/wiki/{novel_name}_hierarchical.json"
    save_hierarchical_wiki(wiki, volumes, book, hier_path)

    return {"message": f"编译完成，共 {len(wiki)} 章", "total": len(wiki)}
