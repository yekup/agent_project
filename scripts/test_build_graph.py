"""
测试知识图谱构建
用调好的测试数据验证 merge_characters → merge_relationships → build_graph 全流程
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.chapter_parser import load_wiki
from core.knowledge_graph import merge_characters, merge_relationships, build_graph, save_graph

# 用之前保存的 Wiki 数据（如果有的话）
# 没有就手动构造 3 章的测试数据
test_wiki = [
    {
        "chapter_index": 1,
        "chapter_title": "第一章 测试",
        "summary": "主角登场",
        "characters": [
            {"name": "萧炎", "role": "主角", "description": "天才少年"},
            {"name": "萧薰儿", "role": "配角", "description": "青梅竹马"}
        ],
        "events": ["萧炎测试"],
        "relationships": [
            {"source": "萧炎", "target": "萧薰儿", "relation": "青梅竹马"}
        ]
    },
    {
        "chapter_index": 2,
        "chapter_title": "第二章 冲突",
        "summary": "冲突升级",
        "characters": [
            {"name": "萧炎", "role": "主角", "description": "不甘示弱"},
            {"name": "纳兰嫣然", "role": "配角", "description": "前来退婚"}
        ],
        "events": ["纳兰嫣然退婚"],
        "relationships": [
            {"source": "萧炎", "target": "纳兰嫣然", "relation": "退婚"}
        ]
    },
    {
        "chapter_index": 3,
        "chapter_title": "第三章 三年之约",
        "summary": "定下赌约",
        "characters": [
            {"name": "萧炎", "role": "主角", "description": "立下誓言"},
            {"name": "萧薰儿", "role": "配角", "description": "默默支持"},
            {"name": "纳兰嫣然", "role": "配角", "description": "高傲离去"}
        ],
        "events": ["萧炎立下三年之约"],
        "relationships": [
            {"source": "萧炎", "target": "纳兰嫣然", "relation": "三年之约"},
            {"source": "萧炎", "target": "萧薰儿", "relation": "温情"}
        ]
    }
]

print("=" * 50)
print("测试知识图谱构建")
print("=" * 50)

# 1. 合并人物
print("\n1. 合并人物...")
char_map = merge_characters(test_wiki)
print(f"  共 {len(char_map)} 个人物:")
for name, info in char_map.items():
    print(f"    - {name} ({info['role']}): 出场 {info['mention_count']} 次")

# 2. 合并关系
print("\n2. 合并关系...")
relationships = merge_relationships(test_wiki)
print(f"  共 {len(relationships)} 条关系:")
for r in relationships:
    print(f"    {r['source']} --[{r['relation']}]--> {r['target']} (权重: {r['weight']})")

# 3. 构建图
print("\n3. 构建 NetworkX 图...")
G = build_graph(char_map, relationships)

# 4. 保存
print("\n4. 保存图谱...")
save_graph(G, "data/wiki/test_graph.json")

print(f"\n✅ 图谱构建测试完成！人物关系图包含 {G.number_of_nodes()} 个节点, {G.number_of_edges()} 条边")
