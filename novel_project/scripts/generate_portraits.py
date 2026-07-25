"""
批量生成 2D 角色立绘
====================
从知识图谱读取人物数据，调用阿里云通义万相批量生成简单的 2D 立绘。

用法:
    # 为绍宋生成前 20 个主要人物的立绘
    python scripts/generate_portraits.py --novel shaosong --top 20

    # 只生成指定角色
    python scripts/generate_portraits.py --novel shaosong --characters "赵玖,岳飞,韩世忠"

    # 生成所有人物（可能有数百个）
    python scripts/generate_portraits.py --novel shaosong --all

    # 使用写实风格
    python scripts/generate_portraits.py --novel shaosong --top 10 --style realistic

依赖:
    DASHSCOPE_API_KEY 环境变量（或 --api-key 参数）
"""
import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR))

from interfaces.portrait_generator import (
    AliyunPortraitGenerator,
    GenerationRequest,
    get_portrait_generator,
)


def load_novel_characters(novel_key: str) -> list[dict]:
    """
    从知识图谱加载人物数据。

    返回按 mention_count 降序排列的节点列表:
    [{name, role, mention_count, chapter_count, descriptions}, ...]
    """
    graph_path = os.path.join(
        BASE_DIR, "data", "wiki", f"{novel_key}_graph.json"
    )
    wiki_path = os.path.join(
        BASE_DIR, "data", "wiki", f"{novel_key}_hierarchical.json"
    )

    if not os.path.exists(graph_path):
        print(f"❌ 图谱文件不存在: {graph_path}")
        return []

    with open(graph_path, "r", encoding="utf-8") as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])

    # 按出场次数排序
    nodes.sort(key=lambda n: -n.get("mention_count", 0))

    # 尝试加载 Wiki 中的角色描述
    char_descriptions: dict[str, list[str]] = {}
    if os.path.exists(wiki_path):
        with open(wiki_path, "r", encoding="utf-8") as f:
            wiki = json.load(f)
        chapters = wiki.get("chapters", [])
        for ch in chapters:
            for c in ch.get("characters", []):
                name = c.get("name", "")
                desc = c.get("description", "")
                if name and desc:
                    char_descriptions.setdefault(name, []).append(desc)

    # 组装结果
    characters = []
    for n in nodes:
        name = n.get("name", "")
        if not name:
            continue
        # 去重（图谱中可能有重名？）
        if any(c["name"] == name for c in characters):
            continue

        # 收集该人物的所有描述（去重 + 合并）
        descriptions = char_descriptions.get(name, [])
        # 去重
        seen = set()
        unique_descs = []
        for d in descriptions:
            if d not in seen:
                seen.add(d)
                unique_descs.append(d)

        # 如果描述太少，从角色属性补充
        if len(unique_descs) < 2:
            role = n.get("role", "")
            if role:
                unique_descs.append(f"角色定位：{role}")

        characters.append({
            "name": name,
            "role": n.get("role", ""),
            "mention_count": n.get("mention_count", 0),
            "chapter_count": n.get("chapter_count", 0),
            "descriptions": unique_descs[:10],  # 最多保留 10 条
        })

    return characters


def build_role_type(role: str) -> str:
    """将图谱中的角色分类映射为立绘的角色类型"""
    role_lower = role.lower()
    if any(kw in role_lower for kw in ["主角", "主人公", "男主", "女主"]):
        return "protagonist"
    if any(kw in role_lower for kw in ["反派", "反角", "敌人"]):
        return "antagonist"
    return "supporting"


def main():
    parser = argparse.ArgumentParser(
        description="批量生成 2D 角色立绘（阿里云通义万相）"
    )
    parser.add_argument("--novel", default="shaosong", help="小说 key")
    parser.add_argument("--top", type=int, default=20, help="取前 N 个主要人物")
    parser.add_argument("--all", action="store_true", help="生成所有人物")
    parser.add_argument("--characters", help="指定角色名，逗号分隔")
    parser.add_argument("--style", default="anime",
                        choices=["anime", "realistic"], help="画风")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="每次 API 调用的间隔秒数（避免限流）")
    parser.add_argument("--api-key", help="DashScope API Key（默认读环境变量）")
    parser.add_argument("--output", help="输出目录（默认 data/portraits/{novel}）")
    args = parser.parse_args()

    # 显示书目名称
    cn_names = {
        "shaosong": "《绍宋》",
        "斗破苍穹": "《斗破苍穹》",
        "神印王座": "《神印王座》",
    }
    display_name = cn_names.get(args.novel, args.novel)
    print(f"\n📖 书籍: {display_name} ({args.novel})")
    print(f"🎨 风格: {args.style}")
    print()

    # 加载人物数据
    characters = load_novel_characters(args.novel)
    if not characters:
        print("❌ 未找到人物数据，请先编译 Wiki 和知识图谱。")
        sys.exit(1)

    print(f"📊 知识图谱中共 {len(characters)} 个人物")

    # 筛选要生成的
    if args.characters:
        names = [n.strip() for n in args.characters.split(",")]
        selected = [c for c in characters if c["name"] in names]
        not_found = [n for n in names if n not in {c["name"] for c in characters}]
        if not_found:
            print(f"⚠️  以下角色在知识图谱中未找到: {', '.join(not_found)}")
        if not selected:
            print("❌ 没有匹配的角色，退出。")
            sys.exit(1)
    elif args.all:
        selected = characters
    else:
        selected = characters[:args.top]

    print(f"🎯 目标生成: {len(selected)} 个人物\n")

    # 构建生成请求
    requests = []
    for c in selected:
        req = GenerationRequest(
            character_name=c["name"],
            appearance_descriptions=c["descriptions"],
            role_type=build_role_type(c["role"]),
            style=args.style,
        )
        requests.append(req)
        desc_preview = (c["descriptions"][0] if c["descriptions"] else "无描述")[:60]
        print(f"  [{c['mention_count']:4d}次] {c['name']:8s} ({c['role']:4s}) → {desc_preview}...")

    print(f"\n{'='*50}")
    print(f"开始生成 ({len(requests)} 个人物, 间隔 {args.delay}s)...")
    print(f"{'='*50}\n")

    # 初始化生成器
    gen_kwargs = {"novel_key": args.novel}
    if args.api_key:
        gen_kwargs["api_key"] = args.api_key
    if args.output:
        gen_kwargs["output_dir"] = args.output

    gen: AliyunPortraitGenerator = get_portrait_generator("aliyun", **gen_kwargs)

    # 逐一生成
    success = 0
    fail = 0
    for i, req in enumerate(requests):
        if i > 0 and args.delay > 0:
            import time
            time.sleep(args.delay)

        print(f"[{i+1}/{len(requests)}] {req.character_name}... ", end="", flush=True)

        try:
            portrait = gen.generate(req)
            if portrait.confidence > 0 and portrait.image_url:
                print(f"✅ {portrait.image_url}")
                success += 1
            else:
                print(f"⚠️ 占位图（API 未配置或调用失败）")
                fail += 1
        except Exception as e:
            print(f"❌ 失败: {e}")
            fail += 1

    print(f"\n{'='*50}")
    print(f"  完成: {success} 成功, {fail} 失败 / 共 {len(requests)}")
    if fail > 0:
        print(f"  💡 提示: 失败可能是因为 DASHSCOPE_API_KEY 未设置或额度不足")
        print(f"     设置: set DASHSCOPE_API_KEY=your-key")
    print(f"  输出: {gen._novel_dir}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
