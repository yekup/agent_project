"""
网文层级分块引擎
================
将原始小说文本按照 全书 → 卷 → 章 → 段落 四级结构进行语义分块。

分块策略:
    Level 0 - 全书摘要    (1块)
    Level 1 - 卷摘要      (每卷1块)
    Level 2 - 章节摘要    (每章1块)
    Level 3 - 原文段落     (每章 N 块，滑窗 + 重叠)

核心设计:
    - 窗口大小按 tokens 计算（中英文混合文本 ≈ chars × 1.3）
    - 重叠窗口确保跨段落的语义不丢失
    - 不打断完整的段落（以 \n\n 为自然边界）
    - 每条 chunk 携带完整元数据（来源章节、卷、位置等）

用法:
    chunker = NovelChunker(chunk_size=512, overlap=128)
    chunks = chunker.chunk_chapter("第一章 内容...", chapter_index=1, chapter_title="第一章")

    # 层级分块
    all_chunks = chunker.chunk_novel(novel_data)
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── 常量 ────────────────────────────────────────────────────────────────

# 中文文本 token 估算系数（1 token ≈ 1.3 中文字符）
CHARS_PER_TOKEN = 1.3

# 段落分隔符
PARAGRAPH_SEP = re.compile(r"\n\s*\n")

# 对话分隔符（用于智能分块边界）
DIALOGUE_BOUNDARY = re.compile(r"[「『""][^」』""]+[」』""]")


# ── 数据模型 ────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """
    单个分块。

    Attributes:
        chunk_id:       全局唯一 ID（如 "shaosong_ch_001_3"）
        novel_key:      小说 key
        level:          层级 (book/volume/chapter/paragraph)
        chapter_index:  章节索引（0-based）
        chapter_title:  章节标题
        volume_index:   卷索引（可选）
        volume_title:   卷标题（可选）
        text:           分块文本
        char_count:     字符数
        token_estimate: 预估 token 数
        position:       在该层级中的位置（第几块）
    """
    chunk_id: str
    novel_key: str
    level: str                           # "book" | "volume" | "chapter" | "paragraph"
    chapter_index: int = 0
    chapter_title: str = ""
    volume_index: int = -1
    volume_title: str = ""
    text: str = ""
    char_count: int = 0
    token_estimate: int = 0
    position: int = 0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.char_count = len(self.text)
        self.token_estimate = math.ceil(self.char_count / CHARS_PER_TOKEN)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "novel_key": self.novel_key,
            "level": self.level,
            "chapter_index": self.chapter_index,
            "chapter_title": self.chapter_title,
            "volume_index": self.volume_index,
            "volume_title": self.volume_title,
            "text": self.text,
            "char_count": self.char_count,
            "token_estimate": self.token_estimate,
            "position": self.position,
            **self.metadata,
        }

    def __repr__(self) -> str:
        return (
            f"<Chunk {self.chunk_id} "
            f"[{self.level}] "
            f"ch:{self.chapter_index} "
            f"pos:{self.position} "
            f"{self.char_count}chars "
            f"~{self.token_estimate}tok>"
        )


# ── 工具函数 ────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """估算文本的 token 数（中文 1.3 chars/token，英文 ~4 chars/token）"""
    chinese_chars = len(re.findall(r"[一-鿿]", text))
    other_chars = len(text) - chinese_chars
    return math.ceil(chinese_chars / CHARS_PER_TOKEN + other_chars / 4)


def safe_truncate(text: str, max_chars: int = 3000) -> str:
    """安全截断：在段落边界处截断，不打断完整段落"""
    if len(text) <= max_chars:
        return text
    # 找到最后一个段落分隔符
    truncated = text[:max_chars]
    last_para = PARAGRAPH_SEP.search(truncated[::-1])
    if last_para:
        cut = max_chars - last_para.start()
        if cut > max_chars * 0.7:  # 确保截断点不过于靠前
            return text[:cut]
    # 找不到段落边界，在最后一个句子边界截断
    last_sentence = max(
        truncated.rfind("。"),
        truncated.rfind("！"),
        truncated.rfind("？"),
        truncated.rfind("\n"),
    )
    if last_sentence > max_chars * 0.5:
        return text[: last_sentence + 1]
    return truncated


# ═════════════════════════════════════════════════════════════════════
# 核心分块引擎
# ═════════════════════════════════════════════════════════════════════

class NovelChunker:
    """
    网文层级分块引擎。

    参数:
        chunk_size:   目标块大小（token 数），默认 512
        overlap:      相邻块重叠（token 数），默认 128
        min_chunk:    最小块大小（token 数），小于此值与前一块合并
        respect_para: 是否在段落边界切割，默认 True
    """

    def __init__(
        self,
        chunk_size: int = 512,
        overlap: int = 128,
        min_chunk: int = 64,
        respect_para: bool = True,
    ):
        assert overlap < chunk_size, "重叠必须小于块大小"
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk = min_chunk
        self.respect_para = respect_para
        self._chunk_counter = 0

    # ── 对外接口 ──────────────────────────────────────────────────

    def chunk_novel(self, novel_data: dict, novel_key: str = "") -> list[Chunk]:
        """
        对整本小说进行四级分块。

        输入: novel_data 为 processed/xxx.json 格式（含 chapters 列表）
        输出: list[Chunk]，包含 book/volume/chapter/paragraph 四级
        """
        self._chunk_counter = 0
        all_chunks: list[Chunk] = []
        chapters = novel_data.get("chapters", [])
        title = novel_data.get("title", novel_key)

        # ── 尝试加载 Wiki 层级数据（如果有） ──
        wiki_data = self._try_load_wiki(novel_key)
        if wiki_data:
            book = wiki_data.get("book", {})
            volumes = wiki_data.get("volumes", [])
        else:
            book = {}
            volumes = []

        # Level 0: 全书摘要
        if book and book.get("summary"):
            all_chunks.append(self._make_chunk(
                novel_key=novel_key,
                level="book",
                text=book["summary"],
                chapter_index=0,
                chapter_title=book.get("title", "全书总览"),
                metadata={
                    "main_characters": book.get("main_characters", []),
                    "themes": book.get("themes", []),
                },
            ))

        # Level 1: 卷摘要 + Level 2: 章节摘要（来自 Wiki）
        if wiki_data and volumes:
            for vol in volumes:
                cr = vol.get("chapter_range", [0, 0])
                vol_text = vol.get("summary", "")
                if vol_text:
                    all_chunks.append(self._make_chunk(
                        novel_key=novel_key,
                        level="volume",
                        text=vol_text,
                        volume_index=vol.get("volume_index", 0),
                        volume_title=vol.get("title", ""),
                        chapter_index=cr[0] if cr else 0,
                        chapter_title=f"第{cr[0]}-{cr[1]}章" if cr else "",
                        metadata={"chapter_range": cr},
                    ))

            # 章节摘要
            for ch in wiki_data.get("chapters", []):
                summary = ch.get("summary", "")
                if summary:
                    all_chunks.append(self._make_chunk(
                        novel_key=novel_key,
                        level="chapter",
                        text=summary,
                        chapter_index=ch.get("chapter_index", 0),
                        chapter_title=ch.get("chapter_title", ""),
                        metadata={
                            "characters": [c.get("name") for c in ch.get("characters", [])[:5] if c.get("name")],
                            "events": ch.get("events", [])[:3],
                        },
                    ))

        # Level 3: 原文段落分块（从原始章节文本）
        for i, ch in enumerate(chapters):
            raw_text = ch.get("text", "")
            if not raw_text or len(raw_text) < 20:
                continue

            chapter_chunks = self.chunk_chapter(
                text=raw_text,
                chapter_index=ch.get("chapter_index", i),
                chapter_title=ch.get("title", ch.get("chapter_title", f"第{i+1}章")),
                novel_key=novel_key,
            )
            all_chunks.extend(chapter_chunks)

        logger.info(
            f"[NovelChunker] 《{title}》分块完成: "
            f"{len(all_chunks)} 块 "
            f"(book={sum(1 for c in all_chunks if c.level=='book')}, "
            f"volume={sum(1 for c in all_chunks if c.level=='volume')}, "
            f"chapter={sum(1 for c in all_chunks if c.level=='chapter')}, "
            f"paragraph={sum(1 for c in all_chunks if c.level=='paragraph')})"
        )
        return all_chunks

    def chunk_chapter(
        self,
        text: str,
        chapter_index: int = 0,
        chapter_title: str = "",
        novel_key: str = "",
    ) -> list[Chunk]:
        """
        将单章原文分块（滑窗 + 重叠 + 段落边界感知）。

        分块策略:
            1. 先按段落分割
            2. 合并短段落直到达到 chunk_size
            3. 长段落单独成块（按句号二次分割）
            4. 相邻块之间保留 overlap 字符的重叠
            5. 对话段落作为边界标记（不打断对话）
        """
        if estimate_tokens(text) <= self.chunk_size:
            # 整章作为一块
            return [self._make_chunk(
                novel_key=novel_key,
                level="paragraph",
                text=text,
                chapter_index=chapter_index,
                chapter_title=chapter_title,
                position=0,
            )]

        chunks: list[Chunk] = []
        paragraphs = self._split_paragraphs(text)
        current_text = ""
        current_tokens = 0
        position = 0

        for para in paragraphs:
            para_tokens = estimate_tokens(para)

            # 如果当前段落单独已超过 chunk_size，强制拆分
            if para_tokens >= self.chunk_size:
                # 先把当前累积的保存
                if current_text:
                    chunks.append(self._make_chunk(
                        novel_key=novel_key, level="paragraph",
                        text=current_text,
                        chapter_index=chapter_index, chapter_title=chapter_title,
                        position=position,
                    ))
                    position += 1
                    current_text = ""
                    current_tokens = 0

                # 拆分长段落
                sub_chunks = self._split_long_paragraph(para)
                for sc in sub_chunks:
                    chunks.append(self._make_chunk(
                        novel_key=novel_key, level="paragraph",
                        text=sc,
                        chapter_index=chapter_index, chapter_title=chapter_title,
                        position=position,
                    ))
                    position += 1
                continue

            # 如果加上当前段落超过 chunk_size，保存当前块
            if current_tokens + para_tokens > self.chunk_size:
                if current_text:
                    chunks.append(self._make_chunk(
                        novel_key=novel_key, level="paragraph",
                        text=current_text,
                        chapter_index=chapter_index, chapter_title=chapter_title,
                        position=position,
                    ))
                    position += 1
                    # 重叠：保留当前块的末尾 overlap tokens 的文本
                    overlap_chars = int(self.overlap * CHARS_PER_TOKEN)
                    current_text = self._get_overlap_text(current_text, overlap_chars)
                    current_tokens = estimate_tokens(current_text)
                else:
                    current_text = ""
                    current_tokens = 0

            # 追加当前段落
            if current_text:
                current_text += "\n\n" + para
            else:
                current_text = para
            current_tokens = estimate_tokens(current_text)

        # 保存最后一块
        if current_text and estimate_tokens(current_text) >= self.min_chunk:
            chunks.append(self._make_chunk(
                novel_key=novel_key, level="paragraph",
                text=current_text,
                chapter_index=chapter_index, chapter_title=chapter_title,
                position=position,
            ))

        # 合并尾部过小的块
        chunks = self._merge_tail_chunks(chunks)

        return chunks

    # ── 私有方法 ──────────────────────────────────────────────────

    def _make_chunk(
        self,
        novel_key: str,
        level: str,
        text: str,
        chapter_index: int = 0,
        chapter_title: str = "",
        position: int = 0,
        volume_index: int = -1,
        volume_title: str = "",
        metadata: dict | None = None,
    ) -> Chunk:
        self._chunk_counter += 1
        chunk_id = f"{novel_key or 'novel'}_{level[:1]}_{self._chunk_counter:05d}"
        return Chunk(
            chunk_id=chunk_id,
            novel_key=novel_key or "",
            level=level,
            text=text,
            chapter_index=chapter_index,
            chapter_title=chapter_title,
            position=position,
            volume_index=volume_index,
            volume_title=volume_title,
            metadata=metadata or {},
        )

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        """按段落分割，保留有效段落"""
        paras = PARAGRAPH_SEP.split(text.strip())
        return [p.strip() for p in paras if p.strip()]

    def _split_long_paragraph(self, text: str) -> list[str]:
        """
        拆分超长段落：按句号/问号/感叹号/对话分割。

        对话边界优先保留（不打断对话）。
        """
        # 尝试按句子分割
        sentences = re.split(r"(?<=[。！？\n])", text)
        sentences = [s.strip() for s in sentences if s.strip()]

        chunks: list[str] = []
        current = ""
        for sent in sentences:
            if estimate_tokens(current + sent) > self.chunk_size and current:
                chunks.append(current)
                # 重叠
                overlap_chars = int(self.overlap * CHARS_PER_TOKEN)
                current = current[-overlap_chars:] if len(current) > overlap_chars else ""
            current = (current + "\n" + sent).strip() if current else sent

        if current:
            chunks.append(current)

        return chunks if chunks else [text]

    @staticmethod
    def _get_overlap_text(text: str, overlap_chars: int) -> str:
        """获取文本末尾的 overlap 字符（在段落边界处截断）"""
        if len(text) <= overlap_chars:
            return ""
        tail = text[-overlap_chars:]
        # 尽量在段落边界处开始
        newline_pos = tail.find("\n")
        if 0 < newline_pos < len(tail) // 2:
            return tail[newline_pos:]
        return tail

    def _merge_tail_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """将尾部过小的块合并到前一块"""
        if len(chunks) < 2:
            return chunks

        merged = []
        for ch in chunks:
            if (
                merged
                and ch.token_estimate < self.min_chunk
                and merged[-1].token_estimate + ch.token_estimate <= self.chunk_size * 1.2
            ):
                # 合并到前一块
                merged[-1].text += "\n\n" + ch.text
                merged[-1].char_count = len(merged[-1].text)
                merged[-1].token_estimate = estimate_tokens(merged[-1].text)
            else:
                merged.append(ch)

        return merged

    @staticmethod
    def _try_load_wiki(novel_key: str) -> dict | None:
        """尝试加载 Wiki 层级数据"""
        from pathlib import Path
        base = Path(__file__).resolve().parent.parent
        wiki_path = base / "data" / "wiki" / f"{novel_key}_hierarchical.json"
        if wiki_path.exists():
            try:
                with open(wiki_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None


# ═════════════════════════════════════════════════════════════════════
# 向量库集成
# ═════════════════════════════════════════════════════════════════════

# 向量库 metadata 的 novel_key 使用短名（历史数据如此），
# 而代码内各处通行的是 wiki 文件全名，这里做双向映射。
NOVEL_KEY_TO_SHORT = {
    "绍宋作者：榴弹怕水": "shaosong",
    "斗破苍穹作者：天蚕土豆": "doupo",
    "神印王座作者：唐家三少": "shenyin",
}
NOVEL_SHORT_TO_FULLNAME = {  # 短名 → data/processed/ 下的 JSON 文件名
    "shaosong": "《绍宋》作者：榴弹怕水",
    "doupo": "《斗破苍穹》作者：天蚕土豆",
    "shenyin": "《神印王座》作者：唐家三少",
}


class VectorStoreIndexer:
    """
    将分块结果写入向量数据库（ChromaDB）。

    用法:
        indexer = VectorStoreIndexer()
        indexer.index_novel("shaosong", chunks)
        results = indexer.search("赵玖和岳飞的关系")
    """

    _init_lock = threading.Lock()  # _init_db 双重检查锁（并行检索线程安全）

    def __init__(self, collection_name: str = "novel_chunks"):
        self.collection_name = collection_name
        self._collection = None
        self._embedding_fn = None
    @staticmethod
    def _pick_embedding():
        """
        选择嵌入函数。

        由环境变量 NOVEL_EMBEDDING 控制：
        - "bge": BGE 中文模型（sentence-transformers，需先重建索引到
          novel_chunks_bge collection，见 scripts/vector_recall_eval.py）
        - 其他/未设置（默认）: ChromaDB 默认 ONNX（all-MiniLM-L6-v2），
          使用旧 novel_chunks collection，开箱即用

        bge 模式初始化失败（依赖缺失/模型未下载）时回退默认 ONNX。

        返回: (embedding_fn 或 None, 模型名, collection 后缀)
        """
        if os.environ.get("NOVEL_EMBEDDING", "").lower() != "bge":
            return None, "default-onnx", ""
        try:
            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )
            # 有 CUDA 用 GPU（1650Ti 4G 跑 bge-large 足够），否则 CPU
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
            fn = SentenceTransformerEmbeddingFunction(
                model_name="BAAI/bge-large-zh-v1.5", device=device,
            )
            fn(["预热"])  # 触发一次真实编码，失败则走回退
            logger.info(f"[VectorStoreIndexer] 使用 BGE 中文嵌入: bge-large-zh-v1.5 ({device})")
            return fn, "bge-large-zh-v1.5", "_bge"
        except Exception as e:
            logger.warning(f"[VectorStoreIndexer] BGE 中文嵌入不可用（{e}），回退默认 ONNX 嵌入")
            return None, "default-onnx", ""

    def _init_db(self):
        """延迟初始化 ChromaDB（线程安全：并行检索线程会同时进入，
        onnxruntime 在 Windows 下并发首导入会抛 ImportError 假报"未安装"）"""
        if self._collection is not None:
            return
        with VectorStoreIndexer._init_lock:
            if self._collection is not None:  # 双重检查
                return
            try:
                import chromadb

                client = chromadb.PersistentClient(
                    path=str(Path(__file__).resolve().parent.parent / "data" / "chroma")
                )
                # 嵌入维度随模型不同（BGE 1024 / 默认 ONNX 384），
                # 不同模型必须分 collection 存储，不能混写
                self._embedding_fn, emb_name, suffix = self._pick_embedding()
                self._collection = client.get_or_create_collection(
                    name=f"{self.collection_name}{suffix}",
                    embedding_function=self._embedding_fn,
                    metadata={"hnsw:space": "cosine", "embedding_model": emb_name},
                )
            except ImportError:
                logger.warning("chromadb 未安装，向量索引不可用")

    def index_novel(self, novel_key: str, chunks: list[Chunk]) -> dict:
        """将分块结果写入向量库"""
        self._init_db()
        if self._collection is None:
            return {"status": "skipped", "reason": "chromadb not available"}

        # 只索引原文段落级分块（不索引摘要类块）
        para_chunks = [c for c in chunks if c.level == "paragraph"]

        # 幂等重建：先清掉该书的旧索引，避免重复 ID 报错与陈旧数据残留
        try:
            self._collection.delete(where={"novel_key": novel_key})
        except Exception as e:
            logger.warning(f"[VectorStoreIndexer] 清理旧索引失败（忽略）: {e}")

        # 批量写入（每批 500 条）
        batch_size = 500
        total = 0
        for i in range(0, len(para_chunks), batch_size):
            batch = para_chunks[i : i + batch_size]
            self._collection.add(
                ids=[c.chunk_id for c in batch],
                documents=[c.text for c in batch],
                metadatas=[
                    {
                        "novel_key": c.novel_key,
                        "chapter_index": c.chapter_index,
                        "chapter_title": c.chapter_title,
                        "position": c.position,
                        "level": c.level,
                    }
                    for c in batch
                ],
            )
            total += len(batch)

        logger.info(f"[VectorStoreIndexer] {novel_key}: {total} 块已索引")
        return {"status": "ok", "indexed": total}

    def search(
        self,
        query: str,
        top_k: int = 20,
        novel_key: str | None = None,
        contains: str | list[str] | None = None,
    ) -> list[dict]:
        """
        语义搜索分块。

        Args:
            query: 搜索文本
            top_k: 返回条数
            novel_key: 限定小说（可选）
            contains: 限定分块文本必须包含的子串（可选，实体精确腿用）。
                传 list 时要求全部子串共现（$and，双/多实体共现腿用）
        """
        self._init_db()
        if self._collection is None:
            return [{"text": "向量库未就绪", "metadata": {}}]

        terms = []
        if contains:
            terms = [contains] if isinstance(contains, str) else [t for t in contains if t]

        where = {"novel_key": novel_key} if novel_key else None
        if not terms:
            where_document = None
        elif len(terms) == 1:
            where_document = {"$contains": terms[0]}
        else:
            where_document = {"$and": [{"$contains": t} for t in terms]}
        results = self._collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where,
            where_document=where_document,
        )

        hits = []
        for i in range(len(results["ids"][0])):
            doc = results["documents"][0][i]
            # 实体腿：截取窗口以实体首次出现处为中心，
            # 避免 [:500] 截断导致返回文本里看不到目标实体
            pos = next((doc.find(t) for t in terms if doc.find(t) >= 0), -1)
            if pos >= 0:
                start = max(0, pos - 200)
                doc = doc[start:start + 500]
            else:
                doc = doc[:500]
            hits.append({
                "chunk_id": results["ids"][0][i],
                "text": doc,
                "score": results["distances"][0][i] if results["distances"] else 0,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            })
        return hits


# ── 测试/演示 ────────────────────────────────────────────────────────────

def demo():
    """在绍宋数据上演示分块"""
    import json, sys
    sys.path.insert(0, ".")

    # 加载清洗后的 JSON
    with open("data/processed/《绍宋》作者：榴弹怕水.json", "r", encoding="utf-8") as f:
        novel = json.load(f)

    chunker = NovelChunker(chunk_size=512, overlap=128)

    # 分块整本
    all_chunks = chunker.chunk_novel(novel, novel_key="shaosong")

    # 统计
    levels = {}
    for c in all_chunks:
        levels[c.level] = levels.get(c.level, 0) + 1
    print(f"分块统计: {dict(sorted(levels.items()))}")
    print(f"总块数: {len(all_chunks)}")

    # 展示原文段落块样本
    para_chunks = [c for c in all_chunks if c.level == "paragraph"]
    print(f"\n原文段落块: {len(para_chunks)} 块")
    for c in para_chunks[:3]:
        print(f"  [{c.chapter_title} pos={c.position}] {c.char_count}chars ~{c.token_estimate}tok")
        print(f"    {c.text[:100]}...")

    # 预估向量库用量
    total_tokens = sum(c.token_estimate for c in para_chunks)
    print(f"\n向量库预估:")
    print(f"  总段落块: {len(para_chunks)}")
    print(f"  总 token: {total_tokens:,}")
    print(f"  嵌入维度: 512 (bge-small-zh)")
    print(f"  预估存储: ~{total_tokens * 512 * 4 // 1024 // 1024}MB")


if __name__ == "__main__":
    demo()
