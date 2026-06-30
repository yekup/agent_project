"""
网文 TXT 清洗工具
去除广告、作者的话、打赏名单等杂质
检测章节边界，输出结构化 JSON

注意:
    章节检测逻辑已统一移至 core/document_parser.py 中的 extract_chapters()
    清洗逻辑 (clean_text) 保留在此，因为它包含打赏名单等网文专属规则
"""
import json
import os
import re
import sys

# 从统一解析模块复用公用函数
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from core.document_parser import extract_chapters, detect_encoding, CHAPTER_PATTERN

RAW_DIR = "data/raw"
PROCESSED_DIR = "data/processed"
os.makedirs(PROCESSED_DIR, exist_ok=True)

# 杂质模式（正则匹配，大小写不敏感）
IMPURITY_PATTERNS = [
    # 网站广告
    r"起点中文网.*(?:阅读|网址|地址)",
    r"最新章节.*(?:请收藏|网址)",
    r"(?:手机|电脑)阅读.*(?:网址)",
    r"无广告.*阅读",
    r"下载.*(?:客户端|app).*阅读",
    r"一秒记住|记住网址|请大家收藏",
    r"www\..*\.(?:com|cn|net)",
    r"http[s]?://",
    # 作者求票/求收藏
    r"(?:新书|本书).*(?:求收藏|求推荐|求票|求订阅)",
    r"(?:求收藏|求推荐|求月票|求订阅|求打赏)[.。！!]",
    r"支持.*正版.*阅读",
    r"投.*推荐票|月票.*投",
    # 作者的话/PS
    r"^PS[：:]|^P\.S[：:]",
    r"作者.*(?:话|说|按|注)",
    r"（.*(?:未完待续|本章完|新书|加更|明天|更新).*）",
    r"(?:未完待续|本章完|新书推荐)",
    # 打赏/投票名单
    r"感谢.*(?:打赏|投票|支持)",
    r"(?:打赏|投票|月票).*名单",
    r"书友\d+",
    # QQ群/公众号
    r"QQ群|微信群|公众号",
    r"微信搜索",
    # 防盗
    r"防盗.*章节|章节.*防盗",
]


def is_impurity(line):
    """判断一行是否为杂质"""
    stripped = line.strip()
    if not stripped:
        return False
    if len(stripped) < 120:
        for pattern in IMPURITY_PATTERNS:
            if re.search(pattern, stripped, re.IGNORECASE):
                return True
    return False


def clean_text(raw_text):
    """清洗文本：去除杂质行、空行归一化"""
    lines = raw_text.split("\n")
    cleaned = []
    in_donation_list = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            continue

        # 检测是否进入打赏名单模式
        if re.search(r"(?:打赏|投票|月票).*名单", stripped, re.IGNORECASE):
            in_donation_list = True
            continue
        if in_donation_list:
            if re.match(r"^[：:、，,\s]*$", stripped):
                continue
            if re.match(r"^[0-9a-zA-Z一-鿿_]+$", stripped) and len(stripped) < 20:
                continue
            if re.match(CHAPTER_PATTERN, stripped) or len(stripped) > 30:
                in_donation_list = False
            else:
                continue

        if is_impurity(line):
            continue

        cleaned.append(stripped)

    return "\n".join(cleaned)


def process_file(filename):
    """处理单个文件"""
    filepath = os.path.join(RAW_DIR, filename)
    print(f"\n处理: {filename}")

    encoding = detect_encoding(filepath)
    print(f"  编码: {encoding}")
    with open(filepath, "r", encoding=encoding, errors="ignore") as f:
        raw_text = f.read()

    print(f"  原始大小: {len(raw_text)} 字符")

    # 清洗
    clean = clean_text(raw_text)
    removed = len(raw_text) - len(clean)
    print(f"  清洗后: {len(clean)} 字符 (移除 {removed} 字符)")

    # 提取章节（复用 document_parser 的统一逻辑）
    chapters = extract_chapters(clean)
    print(f"  章节数: {len(chapters)}")

    # 统计
    total_words = sum(len(ch["text"]) for ch in chapters)
    for ch in chapters:
        if len(ch["text"]) < 100:
            print(f"    警告: 「{ch['title']}」只有 {len(ch['text'])} 字，可能是残留杂质")
    avg_words = total_words / len(chapters) if chapters else 0
    print(f"  总字数: {total_words}, 平均每章: {avg_words:.0f} 字")

    # 输出
    base_name = os.path.splitext(filename)[0]

    clean_txt_path = os.path.join(PROCESSED_DIR, f"{base_name}_clean.txt")
    with open(clean_txt_path, "w", encoding="utf-8") as f:
        for ch in chapters:
            f.write(ch["title"] + "\n\n")
            f.write(ch["text"] + "\n\n")
    print(f"  清洗文本: {clean_txt_path}")

    json_path = os.path.join(PROCESSED_DIR, f"{base_name}.json")
    output = {
        "title": base_name.replace("《", "").replace("》", "").replace("作者：", " ").strip(),
        "chapters": chapters,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  结构化JSON: {json_path}")

    return output


if __name__ == "__main__":
    print("=" * 50)
    print("网文 TXT 清洗工具")
    print("=" * 50)

    # 自动查找 raw 目录下的所有 .txt 文件
    txt_files = [f for f in os.listdir(RAW_DIR) if f.endswith(".txt")]
    if not txt_files:
        print(f"在 {RAW_DIR} 中未找到 .txt 文件")
        exit()

    print(f"找到 {len(txt_files)} 个文件")

    for txt_file in txt_files:
        try:
            process_file(txt_file)
        except Exception as e:
            print(f"  错误: {e}")

    print("\n" + "=" * 50)
    print("清洗完成")
    print("=" * 50)
