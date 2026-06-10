"""
逐章编译Wiki
每章用LLM提取：人物、事件、关系变化、摘要
输出结构化Wiki条目
"""

import json
import os
import re
import sys
import time

#把项目根目录和agent_project 都加入路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(BASE_DIR)) #找到agent_project的父目录

from agent_project.core.llm import call_llm
#LLM编译prompt
CHAPTER_WIKI_PROMPT = """你是一个网络小说分析专家。请分析以下章节内容，提取结构化信息。

章节标题：{chapter_title}

章节内容：
{chapter_text}

请严格按照以下JSON返回（不要加其他文字）：
{{
    "summary": "本章摘要（100-200字）",
    "characters": [
        {{"name": "人物名", "role": "主角/配角/路人", "description": "本章中该人物的表现"}}
    ],
    "events": ["关键事件1", "关键事件2"],
    "relationships": [
        {{"source": "人物A", "target": "人物B", "relation": "关系描述"}}
    ]
}}

要求：
1. characters 只列出本章确实出场或被提及的人物
2. relationships 只列出本章中出现或变化的人物关系
3. events 按时间顺序排列
4. summary 要包含本章最关键的情节推进

"""

def parse_chapter(chapter_title,chapter_text,max_retries=5):
    """用LLM将单章编译为Wiki条目"""
    prompt = CHAPTER_WIKI_PROMPT.format(
        chapter_title=chapter_title,
        chapter_text=chapter_text[:3000], #每章只取前3000字，避免过长导致LLM处理失败
    )

    for attempt in range(max_retries):
        try:
            response = call_llm([
                {"role":"user","content":prompt}
            ])

            #尝试解析JSON
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                #验证必要字段
                if "summary" in data and "characters" in data:
                    return data
                
            raise ValueError("LLM返回的格式不正确，无法解析为JSON")
        
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  重试 {attempt + 1}/{max_retries}: {e}")
                time.sleep(2) #稍等再试        
            else:
                print(f"  解析失败: {e}")
                return {
                    "summary": chapter_title,
                    "characters": [],
                    "events": [],
                    "relationships": []
                }

def build_wiki(novel_data, batch_size=5, delay=1, checkpoint_path=None):
    """将整本小说逐章编译为 Wiki

    参数:
        novel_data: 清洗后的 JSON 数据（含 chapters 列表）
        batch_size: 每批处理章数（避免 API 限流）
        delay: 每批间隔秒数
        checkpoint_path: 断点续传文件路径，如 "data/wiki/shaosong_wiki.json"
                        已存在时自动从断点恢复

    返回:
        list of dict，每章一条 Wiki 条目
    """
    chapters = novel_data["chapters"]
    title = novel_data["title"]

    # 尝试从断点恢复
    wiki_entries = []
    start_idx = 1 if chapters[0]["title"] == "前言" else 0

    if checkpoint_path and os.path.exists(checkpoint_path):
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            wiki_entries = json.load(f)
        resume_from = len(wiki_entries) + start_idx
        print(f"发现断点：已编译 {len(wiki_entries)} 章，从第 {resume_from + 1} 章继续...")
    else:
        resume_from = start_idx

    total = len(chapters) - start_idx
    print(f"《{title}》共 {total} 章，从第 {resume_from - start_idx + 1} 章开始...")

    for i in range(resume_from, len(chapters)):
        ch = chapters[i]
        print(f"  [{i}/{len(chapters)}] {ch['title']}...", flush=True)

        entry = parse_chapter(ch["title"], ch["text"])
        entry["chapter_index"] = i
        entry["chapter_title"] = ch["title"]
        wiki_entries.append(entry)

        # 每处理一章就保存一次断点
        if checkpoint_path:
            save_wiki(wiki_entries, checkpoint_path)

        # 每批处理完暂停
        if (i - resume_from + 1) % batch_size == 0 and i < len(chapters) - 1:
            print(f"  已处理 {i - start_idx + 1}/{total} 章，暂停 {delay} 秒...")
            time.sleep(delay)

    print(f"《{title}》Wiki 编译完成，共 {len(wiki_entries)} 条")
    return wiki_entries

def _detect_natural_volumes(wiki_entries):
    """
    检测章节标题中是否有自然卷划分（如"第X卷""第X部"）

    扫描前 30 章的标题，如果连续出现卷编号，则判定为有自然卷

    返回:
        list of (volume_name, start_index, end_index) 或 None（如果没有自然卷）
    """
    import re
    volume_pattern = re.compile(r'(第[一二三四五六七八九十百千\d]+[卷部])')

    detected = []
    current_vol = None
    vol_start = 0

    for i, entry in enumerate(wiki_entries[:200]):  # 扫前 200 章
        title = entry.get("chapter_title", "") or ""
        match = volume_pattern.search(title)
        if match:
            vol_name = match.group(1)
            if vol_name != current_vol:
                if current_vol is not None:
                    detected.append((current_vol, vol_start, i - 1))
                current_vol = vol_name
                vol_start = i

    # 收尾最后一卷（只要有 1 章就算）
    if current_vol is not None:
        detected.append((current_vol, vol_start, len(wiki_entries) - 1))

    # 必须有至少 2 个自然卷，且覆盖了大部分章节，才算有效
    if len(detected) >= 2:
        total_covered = sum(end - start + 1 for _, start, end in detected)
        if total_covered >= len(wiki_entries) * 0.5:
            return detected

    return None


def build_volume_summaries(wiki_entries, volume_size=50):
    """
    构建卷摘要（自动检测自然卷，没有则人工分组）

    参数:
        wiki_entries: 章节级 Wiki 条目列表
        volume_size: 没有自然卷时，每卷包含的章数

    返回:
        list of dict，每卷一条摘要
    """
    # 先尝试检测自然卷
    natural_volumes = _detect_natural_volumes(wiki_entries)

    if natural_volumes:
        print(f"检测到自然卷划分：共 {len(natural_volumes)} 卷", flush=True)
        vol_groups = natural_volumes
    else:
        print(f"未检测到自然卷，按每 {volume_size} 章人工分组...", flush=True)
        vol_groups = []
        for i in range(0, len(wiki_entries), volume_size):
            end = min(i + volume_size - 1, len(wiki_entries) - 1)
            vol_groups.append((f"第{i+1}-{end+1}章", i, end))

    volumes = []
    for vol_name, start_idx, end_idx in vol_groups:
        batch = wiki_entries[start_idx:end_idx + 1]
        start_ch = batch[0].get("chapter_index", start_idx + 1)
        end_ch = batch[-1].get("chapter_index", end_idx + 1)

        chapters_text = "\n".join([
            f"{w.get('chapter_title', '')}: {w.get('summary', '')}"
            for w in batch
        ])

        prompt = f"""以下是小说第{start_ch}章到第{end_ch}章的章节摘要。
请生成一个 200 字左右的卷摘要，提炼这一卷的主要情节线、核心人物和关键事件。

{chapters_text}

以 JSON 格式返回：
{{"summary": "卷摘要", "main_characters": ["人物1", "人物2"]}}
"""
        print(f"  生成卷摘要（{vol_name}，第{start_ch}-{end_ch}章）...", flush=True)
        response = call_llm([{"role": "user", "content": prompt}])

        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        data = {"summary": response, "main_characters": []}
        if json_match:
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        volumes.append({
            "type": "volume",
            "volume_index": len(volumes) + 1,
            "title": vol_name,
            "chapter_range": [start_ch, end_ch],
            "summary": data.get("summary", response),
            "main_characters": data.get("main_characters", []),
        })

    print(f"卷摘要生成完成：共 {len(volumes)} 卷")
    return volumes


def build_book_summary(wiki_entries, volume_summaries=None):
    """
    用 LLM 生成全书摘要
    如果有卷摘要则基于卷摘要生成，否则基于章节摘要生成
    """
    if volume_summaries:
        source = "\n".join([f"卷{v['volume_index']}: {v['summary'][:200]}" for v in volume_summaries])
    else:
        source = "\n".join([w.get("summary", "")[:200] for w in wiki_entries[:20]])

    prompt = f"""以下是这部小说的摘要信息，请生成一个 300 字左右的全书摘要，概括整体故事脉络：

{source}

以 JSON 格式返回：
{{"summary": "全书摘要", "main_characters": ["人物1", "人物2"], "themes": ["主题1", "主题2"]}}
"""
    print("  生成全书摘要...", flush=True)
    response = call_llm([{"role": "user", "content": prompt}])

    import re
    json_match = re.search(r'\{.*\}', response, re.DOTALL)
    data = {"summary": response, "main_characters": [], "themes": []}
    if json_match:
        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    return {
        "type": "book",
        "title": "全书总览",
        "summary": data.get("summary", response),
        "main_characters": data.get("main_characters", []),
        "themes": data.get("themes", []),
    }


def save_wiki(wiki_entries, filepath):
    """保存 Wiki 到 JSON 文件（兼容旧格式：纯章节列表）"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(wiki_entries, f, ensure_ascii=False, indent=2)
    print(f"Wiki 已保存: {filepath}")


def save_hierarchical_wiki(chapter_entries, volumes, book, filepath):
    """
    保存三层 Wiki（全书 + 卷 + 章节）到一个文件

    文件结构：
    {
        "book": {...},
        "volumes": [...],
        "chapters": [...]
    }
    """
    data = {
        "book": book,
        "volumes": volumes,
        "chapters": chapter_entries,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"三层 Wiki 已保存: {filepath}")
    print(f"  全书摘要: 1 条")
    print(f"  卷摘要: {len(volumes)} 条")
    print(f"  章节摘要: {len(chapter_entries)} 条")


def load_wiki(filepath):
    """从 JSON 文件加载 Wiki（兼容新旧格式）"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 判断是新格式（dict 含 book/volumes/chapters）还是旧格式（list）
    if isinstance(data, dict) and "chapters" in data:
        return data
    elif isinstance(data, list):
        # 旧格式：自动包装
        return {
            "book": {"type": "book", "title": "全书总览", "summary": "", "main_characters": []},
            "volumes": [],
            "chapters": data,
        }
    return data

    
