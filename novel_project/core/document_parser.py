"""
文档格式解析引擎 (Document Router)
====================================
统一文档解析入口，支持 TXT / Word / PDF / Markdown 等多格式。
采用策略模式，新增格式只需实现 DocumentParser 接口并注册。

用法:
    router = DocumentRouter()
    result = router.parse("upload/xxx.docx")
    # result = {
    #     "title": "第一章 穿越",
    #     "chapters": [{"title": "第一章", "text": "..."}, ...],
    #     "metadata": {"format": "docx", "pages": 42, ...}
    # }

架构:
                    ┌─────────────┐
                    │DocumentRouter│  ← 入口：检测格式，分发解析器
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   ┌──────────┐    ┌──────────┐     ┌──────────┐
   │ TxtParser │    │DocxParser│     │PdfParser │  ← 可扩展
   └──────────┘    └──────────┘     └──────────┘
          │                │                │
          ▼                ▼                ▼
   ┌──────────────────────────────────────────┐
   │    统一输出: {"chapters": [...]}          │
   └──────────────────────────────────────────┘
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── 异常定义 ────────────────────────────────────────────────────────────

class UnsupportedFormatError(ValueError):
    """不支持的文件格式"""
    pass

class CorruptedFileError(ValueError):
    """文件已损坏"""
    pass

class EncodingError(ValueError):
    """编码检测失败"""
    pass


# ── 统一输出模型 ───────────────────────────────────────────────────────

DEFAULT_CHAPTER_OUTPUT = {
    "title": "",
    "chapters": [],
    "metadata": {"format": "unknown"},
}


# ── 章节检测公用函数 ───────────────────────────────────────────────────

# 章节标题模式（与 clean_novel.py 保持一致）
CHAPTER_PATTERN = re.compile(
    r"^(?:第[一-鿿\d]+[章回节部集]"
    r"|[一二三四五六七八九十百千万]+[章回节部集]"
    r"|楔子|序章|尾声|后记|番外"
    r"|Chapter\s+\d+|CHAPTER\s+\d+)",
)

# 增强模式（允许前后空格）
CHAPTER_PATTERN_LOOSE = re.compile(
    r"^[\s　]*(第[一-鿿\d]+[章回节部集]"
    r"|[一二三四五六七八九十百千万]+[章回节部集]"
    r"|楔子|序章|尾声|后记|番外"
    r"|Chapter\s+\d+|CHAPTER\s+\d+)",
)


def extract_chapters(text: str) -> list[dict]:
    """
    从纯文本中提取章节边界。

    支持:
        - 中文数字章节: "第一章" "第100章" "卷三"
        - 英文章节: "Chapter 1" "CHAPTER 42"
        - 特殊标记: "楔子" "序章" "尾声" "后记" "番外"

    返回:
        [{"title": str, "text": str}, ...]
    """
    lines = text.split("\n")
    chapters = []
    current_title = None
    current_content = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_title:
                current_content.append("")
            continue

        if CHAPTER_PATTERN_LOOSE.match(stripped):
            # 保存上一章
            if current_title is not None:
                chapters.append({
                    "title": current_title,
                    "text": "\n".join(current_content).strip(),
                })
                current_content = []
            current_title = stripped
        else:
            if current_title is None:
                current_title = "前言"
            # 跳过明显的广告行
            if _is_impurity_line(stripped):
                continue
            current_content.append(stripped)

    # 保存最后一章
    if current_title is not None and current_content:
        chapters.append({
            "title": current_title,
            "text": "\n".join(current_content).strip(),
        })

    # 如果没有任何章节被检测到，整个文本作为一章
    if not chapters and current_content:
        chapters.append({
            "title": "全文",
            "text": "\n".join(current_content).strip(),
        })

    return chapters


def _is_impurity_line(line: str) -> bool:
    """判断是否为广告/杂质行（与 clean_novel.py 保持同步）"""
    impurity_patterns = [
        r"起点中文网.*(?:阅读|网址|地址)",
        r"最新章节.*(?:请收藏|网址)",
        r"(?:手机|电脑)阅读.*(?:网址)",
        r"下载.*(?:客户端|app).*阅读",
        r"一秒记住|记住网址|请大家收藏",
        r"www\..*\.(?:com|cn|net)",
        r"http[s]?://",
        r"(?:新书|本书).*(?:求收藏|求推荐|求票|求订阅)",
        r"(?:求收藏|求推荐|求月票|求订阅|求打赏)",
        r"作者.*(?:话|说|按|注)",
        r"感谢.*(?:打赏|投票|支持)",
        r"(?:打赏|投票|月票).*名单",
        r"QQ群|微信群|公众号",
        r"书友\d+",
    ]
    if len(line) >= 120:
        return False
    for pattern in impurity_patterns:
        if re.search(pattern, line, re.IGNORECASE):
            return True
    return False


def detect_encoding(filepath: str | Path) -> str:
    """检测文件编码"""
    with open(filepath, "rb") as f:
        raw = f.read(4096)
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        raw.decode("gbk")
        return "gbk"
    except UnicodeDecodeError:
        return "gb18030"


# ── Parser 抽象接口 ─────────────────────────────────────────────────────

class DocumentParser(ABC):
    """文档解析器基类"""

    # 支持的文件扩展名（小写）
    extensions: list[str] = []

    # 文件魔数（用于格式验证，可选）
    magic_bytes: list[bytes] = []

    # 格式名称（用于显示）
    format_name: str = "unknown"

    @abstractmethod
    def parse(self, filepath: str | Path) -> dict:
        """
        解析文档，返回统一格式:

        {
            "title": str,                    # 文档标题
            "chapters": [                     # 章节列表
                {"title": str, "text": str},
                ...
            ],
            "metadata": {                    # 元信息
                "format": str,               # 文档格式
                "pages": int,                # 页数（可选）
                "author": str,               # 作者（可选）
                "encoding": str,             # 编码（可选）
                ...
            }
        }
        """
        ...

    def validate(self, filepath: str | Path) -> bool:
        """验证文件格式是否合法（可选覆盖）"""
        return True

    def get_size_info(self, filepath: str | Path) -> dict:
        """获取文件大小信息"""
        stat = os.stat(filepath)
        return {
            "size_bytes": stat.st_size,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
        }


# ── TXT 解析器 ─────────────────────────────────────────────────────────

class TxtParser(DocumentParser):
    """
    纯文本解析器。

    支持:
        - UTF-8 / GBK / GB18030 自动检测
        - 章节边界自动检测（中文数字/英文 Chapter）
        - 内置杂质过滤（广告、打赏名单、作者话）
    """

    extensions = [".txt"]
    format_name = "纯文本"

    def parse(self, filepath: str | Path) -> dict:
        filepath = Path(filepath)
        if not filepath.exists():
            raise CorruptedFileError(f"文件不存在: {filepath}")

        # 1. 检测编码
        encoding = detect_encoding(filepath)
        try:
            with open(filepath, "r", encoding=encoding, errors="replace") as f:
                raw_text = f.read()
        except Exception as e:
            # 兜底：尝试用 UTF-8
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    raw_text = f.read()
                encoding = "utf-8"
            except Exception as e2:
                raise EncodingError(f"无法解析文件编码: {e2}")

        # 2. 提取章节
        chapters = extract_chapters(raw_text)

        # 3. 构建标题
        title = filepath.stem

        return {
            "title": title,
            "chapters": chapters,
            "metadata": {
                "format": "txt",
                "encoding": encoding,
                "chars_total": len(raw_text),
                "chars_cleaned": sum(len(c["text"]) for c in chapters),
                **self.get_size_info(filepath),
            },
        }

    def validate(self, filepath: str | Path) -> bool:
        """验证是合法的文本文件（非二进制）"""
        filepath = Path(filepath)
        try:
            with open(filepath, "rb") as f:
                chunk = f.read(1024)
            # 检查是否包含大量空字节（二进制文件特征）
            null_ratio = chunk.count(b"\x00") / max(len(chunk), 1)
            return null_ratio < 0.1
        except Exception:
            return False


# ── 文档路由器 ─────────────────────────────────────────────────────────

class DocumentRouter:
    """
    文件格式检测与路由。

    用法:
        router = DocumentRouter()
        # 自动检测格式并解析
        result = router.parse("upload/小说.docx")

        # 或手动指定格式
        result = router.parse("upload/未知后缀文件", force_format="txt")
    """

    def __init__(self):
        self._parsers: dict[str, type[DocumentParser]] = {}

        # 注册内置解析器
        self._register_builtin()

    def _register_builtin(self):
        """注册内置解析器"""
        self.register(TxtParser)

    def register(self, parser_cls: type[DocumentParser]):
        """
        注册新的解析器。

        用法:
            router.register(DocxParser)   # Phase 2
            router.register(PdfParser)    # Phase 3
        """
        for ext in parser_cls.extensions:
            ext_lower = ext.lower()
            if ext_lower in self._parsers:
                logger.warning(f"扩展名 {ext} 的解析器被覆盖: {parser_cls.__name__}")
            self._parsers[ext_lower] = parser_cls
        logger.info(f"[DocumentRouter] 注册解析器: {parser_cls.__name__} → {parser_cls.extensions}")

    def get_parser(self, filepath: str | Path) -> type[DocumentParser] | None:
        """根据文件扩展名获取解析器类"""
        ext = Path(filepath).suffix.lower()
        return self._parsers.get(ext)

    def supported_extensions(self) -> list[str]:
        """获取所有支持的扩展名"""
        return list(self._parsers.keys())

    def supported_formats_display(self) -> str:
        """获取用户可读的支持格式说明"""
        names = []
        for cls in set(self._parsers.values()):
            exts = ", ".join(cls.extensions)
            names.append(f"{cls.format_name} ({exts})")
        return " / ".join(names)

    def parse(
        self,
        filepath: str | Path,
        force_format: str | None = None,
    ) -> dict:
        """
        解析文档入口。

        Args:
            filepath: 文件路径
            force_format: 强制指定格式（如 "txt"），跳过扩展名检测

        Returns:
            dict: {title, chapters, metadata}

        Raises:
            UnsupportedFormatError: 不支持的文件格式
            CorruptedFileError: 文件损坏
            EncodingError: 编码问题
        """
        filepath = Path(filepath)

        if not filepath.exists():
            raise FileNotFoundError(f"文件不存在: {filepath}")

        # 确定解析器
        if force_format:
            parser_cls = self._parsers.get(force_format.lower())
            if not parser_cls:
                raise UnsupportedFormatError(
                    f"不支持的格式: {force_format}。支持的格式: {self.supported_formats_display()}"
                )
        else:
            parser_cls = self.get_parser(filepath)
            if not parser_cls:
                raise UnsupportedFormatError(
                    f"不支持的文件格式: {filepath.suffix}。支持的格式: {self.supported_formats_display()}"
                )

        # 实例化并解析
        parser = parser_cls()
        try:
            result = parser.parse(filepath)
            logger.info(
                f"[DocumentRouter] 解析完成: {filepath.name} "
                f"→ {len(result.get('chapters', []))} 章, "
                f"格式: {result.get('metadata', {}).get('format', '?')}"
            )
            return result
        except Exception as e:
            logger.error(f"[DocumentRouter] 解析失败: {filepath.name} - {e}")
            raise


# ── 全局默认实例 ───────────────────────────────────────────────────────

_router: DocumentRouter | None = None


def get_router() -> DocumentRouter:
    """获取全局 DocumentRouter 实例（单例）"""
    global _router
    if _router is None:
        _router = DocumentRouter()
    return _router


def parse_document(
    filepath: str | Path,
    force_format: str | None = None,
) -> dict:
    """
    快捷函数：解析文档。

    用法:
        result = parse_document("小说.txt")
    """
    return get_router().parse(filepath, force_format=force_format)


# ── 演示 / 测试 ────────────────────────────────────────────────────────

def demo():
    """在测试文本上演示解析器"""
    import tempfile

    test_text = """前言
这是一本关于穿越的故事。

第一章 穿越
赵玖睁开眼睛，发现自己躺在一张陌生的床上。
「这是哪里？」他喃喃道。

第二章 朝堂
赵玖走进大殿，文武百官分列两侧。
「吾皇万岁万岁万万岁！」

第三章 出征
「点兵！」
三军集结，旗帜飘扬。
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(test_text)
        tmp_path = f.name

    try:
        result = parse_document(tmp_path)
        print(f"标题: {result['title']}")
        print(f"章节数: {len(result['chapters'])}")
        for ch in result["chapters"]:
            print(f"  [{ch['title']}] {ch['text'][:40]}...")
        print(f"元信息: {json.dumps(result['metadata'], ensure_ascii=False)}")
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    demo()
