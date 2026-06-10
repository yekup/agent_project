"""
测试多 Agent 协作流程
用前 5 章的绍宋数据跑一遍完整链路
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 1. 先构建图谱
from core.knowledge_graph import merge_characters, merge_relationships, build_graph, save_graph
from core.chapter_parser import load_wiki

wiki_data = load_wiki("data/wiki/shaosong_test_5ch.json")
# 兼容新旧格式：如果是 dict 则取 chapters，否则直接作为列表
wiki_chapters = wiki_data["chapters"] if isinstance(wiki_data, dict) else wiki_data
char_map = merge_characters(wiki_chapters)
rels = merge_relationships(wiki_chapters)
G = build_graph(char_map, rels)
save_graph(G, "data/wiki/test_graph.json")

# 2. 初始化三级检索器
from core.retriever import NovelRetriever
novel_path = "data/processed/《绍宋》作者：榴弹怕水.json"
retriever = NovelRetriever(
    wiki_path="data/wiki/shaosong_test_5ch.json",
    graph_path="data/wiki/test_graph.json",
    novel_path=novel_path,
)

# 3. 初始化 4 个 Agent
from core.agents.researcher import Researcher
from core.agents.writer import Writer
from core.agents.reviewer import Reviewer
from core.agents.coordinator import Coordinator

researcher = Researcher(retriever)
writer = Writer()
reviewer = Reviewer()
coordinator = Coordinator(researcher, writer, reviewer)

# 4. 测试
test_query = "描述前五章的人物关系"
print("\n" + "=" * 50)
print(f"用户问题: {test_query}")
print("=" * 50)

result = coordinator.run(test_query)

print("\n" + "=" * 50)
print("最终报告")
print("=" * 50)
print(result["final_report"])
print(f"\n执行轮数: {result['rounds']}")
