"""
夜间全量编译脚本
依次处理斗破苍穹和神印王座，带断点续传
"""
import json, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.chapter_parser import build_wiki, build_volume_summaries, build_book_summary, save_hierarchical_wiki

novels = [
    ("斗破苍穹", "data/processed/《斗破苍穹》作者：天蚕土豆.json"),
    ("神印王座", "data/processed/《神印王座》作者：唐家三少.json"),
]

for name, path in novels:
    print(f"\n{'='*60}")
    print(f"开始处理《{name}》")
    print(f"{'='*60}")

    with open(path, "r", encoding="utf-8") as f:
        novel = json.load(f)

    os.makedirs("data/wiki", exist_ok=True)
    wiki_path = f"data/wiki/{name}_wiki.json"
    hier_path = f"data/wiki/{name}_hierarchical.json"

    # 编译章节（断点续传）
    wiki = build_wiki(novel, batch_size=5, delay=2, checkpoint_path=wiki_path)
    print(f"  ✅ 《{name}》章节编译完成：{len(wiki)} 章")

    # 卷摘要
    volumes = build_volume_summaries(wiki, volume_size=50)
    print(f"  ✅ 《{name}》卷摘要完成：{len(volumes)} 卷")

    # 全书摘要
    book = build_book_summary(wiki, volumes)
    print(f"  ✅ 《{name}》全书摘要完成")

    # 保存三层结构
    save_hierarchical_wiki(wiki, volumes, book, hier_path)

    print(f"  ✅ 《{name}》全部完成，已保存到 {hier_path}")

print(f"\n{'='*60}")
print("两部小说全部编译完成！")
print(f"{'='*60}")
