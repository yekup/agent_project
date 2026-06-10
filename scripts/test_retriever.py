"""
测试三级检索
用真实的 5 章 Wiki 数据验证三级检索全流程
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 1. 先构建图谱
from core.knowledge_graph import merge_characters, merge_relationships, build_graph, save_graph
from core.chapter_parser import load_wiki

wiki = load_wiki("data/wiki/shaosong_test_5ch.json")
print(f"加载 Wiki: {len(wiki)} 章")

char_map = merge_characters(wiki)
print(f"合并人物: {len(char_map)} 个")

rels = merge_relationships(wiki)
print(f"合并关系: {len(rels)} 条")

G = build_graph(char_map, rels)
save_graph(G, "data/wiki/test_graph.json")

# 2. 测试三级检索
from core.retriever import NovelRetriever, format_search_result

novel_path = "data/processed/《绍宋》作者：榴弹怕水.json"
retriever = NovelRetriever(
    wiki_path="data/wiki/shaosong_test_5ch.json",
    graph_path="data/wiki/test_graph.json",
    novel_path=novel_path,
)

# 测试几个查询
for query in ["赵构", "岳飞", "金国"]:
    print(f"\n{'='*50}")
    result = retriever.search(query)
    print(format_search_result(result))
