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


# ---- 上传与编译 ----

@router.post("/upload")
async def upload_novel(file: UploadFile = File(...)):
    """上传 TXT，返回章节数供用户选择"""
    if not file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 文件")

    from scripts.clean_novel import process_file
    raw_path = f"data/raw/{file.filename}"
    os.makedirs("data/raw", exist_ok=True)
    content = await file.read()
    with open(raw_path, "wb") as f:
        f.write(content)

    # 清洗
    result = process_file(file.filename)
    total_chapters = len(result.get("chapters", []))

    return {
        "filename": file.filename,
        "total_chapters": total_chapters,
        "message": f"清洗完成，共 {total_chapters} 章",
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

    novel_name = filename.replace(".txt", "").replace("《", "").replace("》", "")
    processed_path = f"data/processed/{filename.replace('.txt', '.json')}"

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
