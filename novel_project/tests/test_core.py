"""
核心模块单元测试
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import patch


class TestChunker(unittest.TestCase):
    """分块引擎测试"""

    def setUp(self):
        from core.chunker import NovelChunker
        self.chunker = NovelChunker(chunk_size=200, overlap=50)

    def test_short_text_single_chunk(self):
        """短文本应作为一块"""
        text = "第一章 测试\n这是正文。"
        chunks = self.chunker.chunk_chapter(text, chapter_index=1, chapter_title="第一章")
        self.assertGreaterEqual(len(chunks), 1)
        self.assertIn("第一章", chunks[0].chapter_title)

    def test_long_text_multiple_chunks(self):
        """长文本应分成多块"""
        text = "\n\n".join(["测试段落" * 50] * 5)
        chunks = self.chunker.chunk_chapter(text)
        self.assertGreater(len(chunks), 1)

    def test_chunk_metadata(self):
        """每块应包含完整的元数据"""
        text = "第一章 穿越\n正文内容。"
        chunks = self.chunker.chunk_chapter(text, chapter_index=1, chapter_title="第一章", novel_key="shaosong")
        for c in chunks:
            self.assertIsNotNone(c.chunk_id)
            self.assertIsNotNone(c.chapter_title)
            self.assertGreater(c.char_count, 0)
            self.assertGreater(c.token_estimate, 0)


class TestDocumentParser(unittest.TestCase):
    """文档解析测试"""

    def test_extract_chapters(self):
        from core.document_parser import extract_chapters
        text = "第一章 穿越\n正文\n第二章 朝堂\n更多内容"
        chapters = extract_chapters(text)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["title"], "第一章 穿越")

    def test_txt_parse(self):
        from core.document_parser import TxtParser
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("第一章 测试\n内容\n第二章 结论\n结束")
            path = f.name
        parser = TxtParser()
        result = parser.parse(path)
        self.assertIn("chapters", result)
        self.assertGreater(len(result["chapters"]), 0)
        os.unlink(path)


class TestSecurity(unittest.TestCase):
    """安全模块测试"""

    def test_jwt_encode_decode(self):
        from core.security import JWTHandler
        jwt = JWTHandler(secret="test-secret-32-char-minimum!!")
        token = jwt.encode({"sub": "user1", "role": "admin"})
        self.assertIsNotNone(token)
        decoded = jwt.decode(token)
        self.assertEqual(decoded["role"], "admin")
        self.assertIsNone(jwt.decode("fake-token"))

    def test_file_validation(self):
        from core.security import FileValidator
        result = FileValidator.validate("test.txt", b"hello", "text/plain")
        self.assertTrue(result["valid"])
        result = FileValidator.validate("test.exe", b"")
        self.assertFalse(result["valid"])

    def test_token_bucket(self):
        from core.security import TokenBucket
        bucket = TokenBucket(rate=120, burst=3)
        for i in range(3):
            self.assertTrue(bucket.allow("test"))
        self.assertFalse(bucket.allow("test"))


class TestExporter(unittest.TestCase):
    """导出模块测试"""

    def test_csv_export(self):
        from core.exporter import NovelExporter
        import tempfile
        tmpdir = tempfile.mkdtemp()
        exporter = NovelExporter("shaosong")
        ok = exporter.export_csv(os.path.join(tmpdir, "test.csv"))
        self.assertTrue(ok)
        import shutil
        shutil.rmtree(tmpdir)


class TestAuthAPI(unittest.TestCase):
    """Auth API 测试"""

    def test_get_permissions(self):
        from web.routes.auth_routes import get_permissions
        admin_perm = get_permissions("admin")
        viewer_perm = get_permissions("viewer")
        self.assertTrue(admin_perm.get("page:upload"))
        self.assertFalse(viewer_perm.get("page:upload"))
        self.assertTrue(admin_perm.get("action:admin:users"))
        self.assertFalse(viewer_perm.get("action:admin:users"))

    def test_role_hierarchy(self):
        from core.security import ROLE_HIERARCHY
        self.assertGreater(ROLE_HIERARCHY["admin"], ROLE_HIERARCHY["editor"])
        self.assertGreater(ROLE_HIERARCHY["editor"], ROLE_HIERARCHY["viewer"])


class TestChineseNumerals(unittest.TestCase):
    """中文数字解析（修复「十一」→1、「二十」→2 的历史 bug）"""

    def test_basic(self):
        from core.cn_num import chinese_to_int
        self.assertEqual(chinese_to_int("一"), 1)
        self.assertEqual(chinese_to_int("十"), 10)
        self.assertEqual(chinese_to_int("十一"), 11)
        self.assertEqual(chinese_to_int("十五"), 15)
        self.assertEqual(chinese_to_int("二十"), 20)
        self.assertEqual(chinese_to_int("二十一"), 21)
        self.assertEqual(chinese_to_int("一百零五"), 105)
        self.assertEqual(chinese_to_int("三百二十"), 320)
        self.assertEqual(chinese_to_int("15"), 15)

    def test_invalid(self):
        from core.cn_num import chinese_to_int
        self.assertIsNone(chinese_to_int(""))
        self.assertIsNone(chinese_to_int("abc"))
        self.assertIsNone(chinese_to_int(None))

    def test_extract_chapter_number(self):
        from core.cn_num import extract_chapter_number
        self.assertEqual(extract_chapter_number("引自第十一章 明道宫"), 11)
        self.assertEqual(extract_chapter_number("第100章 决战"), 100)
        self.assertIsNone(extract_chapter_number("没有章节号"))

    def test_find_chapter_by_number_with_front_matter(self):
        """有前言占位时仍按标题编号正确定位（不能按位置 off-by-one）"""
        from core.cn_num import find_chapter_by_number
        chapters = [{"title": "前言"}] + [
            {"title": f"第{n}章 内容{n}"} for n in
            ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一"]
        ]
        ch = find_chapter_by_number(chapters, 11)
        self.assertEqual(ch["title"], "第十一章 内容十一")
        # 位置兜底：无标题编号的数据按 1-based 顺序取
        plain = [{"title": f"卷一-{i}"} for i in range(1, 6)]
        self.assertEqual(find_chapter_by_number(plain, 2)["title"], "卷一-2")
        self.assertIsNone(find_chapter_by_number(plain, 99))


class TestTextMatch(unittest.TestCase):
    """中文 n-gram 匹配（修复 split() 对中文失效的问题）"""

    def test_chapter_title_core(self):
        from core.text_match import chapter_title_core
        self.assertEqual(chapter_title_core("第一章 明道宫"), "明道宫")
        self.assertEqual(chapter_title_core("第一百回 决战"), "决战")
        self.assertEqual(chapter_title_core("楔子"), "楔子")

    def test_ngram_hits(self):
        from core.text_match import ngram_hits
        self.assertGreaterEqual(ngram_hits("赵玖在八公山做了什么", "赵玖率军登上八公山"), 2)
        self.assertEqual(ngram_hits("赵玖", "完颜宗弼南侵"), 0)
        self.assertEqual(ngram_hits("", "任意文本"), 0)


class TestCallLLMFailure(unittest.TestCase):
    """call_llm 失败语义：未配置 Key 时必须返回 None（而非伪装成功的回退文案）"""

    def test_no_api_key_returns_none(self):
        from unittest.mock import patch
        import core.llm as llm_mod
        with patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "", "DASHSCOPE_API_KEY": "",
            "LLM_PROVIDER": "", "LLM_BASE_URL": "", "LLM_MODEL": "",
        }):
            result = llm_mod.call_llm([{"role": "user", "content": "你好"}])
        self.assertIsNone(result)


class TestReviewerFallback(unittest.TestCase):
    """Reviewer 解析失败必须默认不通过（质量门禁不能静默洞开）"""

    _LONG_REPORT = "赵玖是小说《绍宋》的主角，原为现代大学生，穿越成为宋高宗赵构。" \
                   "他在八公山上重整旗鼓，任用岳飞、韩世忠等名将，力图改变历史走向。"

    def test_llm_unavailable_defaults_to_not_passed(self):
        from unittest.mock import patch
        from core.agents.reviewer import Reviewer
        with patch("core.agents.reviewer.call_llm", return_value=None):
            result = Reviewer().review(self._LONG_REPORT, "问题", research_materials="材料")
        self.assertFalse(result["passed"])
        self.assertEqual(result["failure_type"], "other")

    def test_unparseable_response_defaults_to_not_passed(self):
        from unittest.mock import patch
        from core.agents.reviewer import Reviewer
        with patch("core.agents.reviewer.call_llm", return_value="这不是 JSON"):
            result = Reviewer().review(self._LONG_REPORT, "问题", research_materials="材料")
        self.assertFalse(result["passed"])

    def test_valid_response_passes_through(self):
        from unittest.mock import patch
        from core.agents.reviewer import Reviewer
        payload = '{"passed": true, "score": 9, "feedback": "好"} 尾随文字'
        with patch("core.agents.reviewer.call_llm", return_value=payload):
            result = Reviewer().review(self._LONG_REPORT, "问题", research_materials="材料")
        self.assertTrue(result["passed"])
        self.assertEqual(result["score"], 9)


class TestPasswordAndPath(unittest.TestCase):
    """密码哈希与路径名校验"""

    def test_bcrypt_roundtrip(self):
        from core.security import hash_password, verify_password, is_legacy_hash
        h = hash_password("secret123")
        self.assertFalse(is_legacy_hash(h))
        self.assertTrue(verify_password("secret123", h))
        self.assertFalse(verify_password("wrong", h))

    def test_legacy_sha256_still_verifies(self):
        import hashlib
        from core.security import verify_password, is_legacy_hash
        legacy = hashlib.sha256("admin123".encode()).hexdigest()
        self.assertTrue(is_legacy_hash(legacy))
        self.assertTrue(verify_password("admin123", legacy))
        self.assertFalse(verify_password("other", legacy))

    def test_validate_path_name(self):
        from core.security import validate_path_name
        for bad in ["../x", "a/b", "a\\b", "..", "", "x" * 201]:
            with self.assertRaises(ValueError, msg=f"应拒绝: {bad!r}"):
                validate_path_name(bad)
        self.assertEqual(validate_path_name("绍宋作者：榴弹怕水"), "绍宋作者：榴弹怕水")


class TestPermissionMiddleware(unittest.TestCase):
    """权限中间件："/" 不能再把所有路径匹配成公开（全站裸奔的历史 bug）"""

    def _make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from core.security import PermissionMiddleware

        app = FastAPI()
        app.add_middleware(PermissionMiddleware)

        @app.get("/")
        def root():
            return {"page": "index"}

        @app.get("/health")
        def health():
            return {"status": "ok"}

        @app.get("/api/novels")
        def novels():
            return []

        @app.post("/api/auth/login")
        def login():
            return {"token": "x"}

        return TestClient(app)

    def test_public_paths_open(self):
        client = self._make_client()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.post("/api/auth/login").status_code, 200)

    def test_api_requires_token(self):
        client = self._make_client()
        self.assertEqual(client.get("/api/novels").status_code, 401)

    def test_valid_token_passes(self):
        from core.security import JWTHandler
        client = self._make_client()
        token = JWTHandler.get_default().encode({"sub": "u1", "role": "admin"})
        resp = client.get("/api/novels", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)


class TestMultiBookResolution(unittest.TestCase):
    """多书参数化：novel 解析、路径安全校验、原文路径定位"""

    def test_resolve_novel_default(self):
        """空参数 → 默认书（向后兼容）"""
        from web.routes.agent_routes import _resolve_novel, DEFAULT_NOVEL
        self.assertEqual(_resolve_novel(""), DEFAULT_NOVEL)
        self.assertEqual(_resolve_novel(None), DEFAULT_NOVEL)

    def test_resolve_novel_passthrough(self):
        from web.routes.agent_routes import _resolve_novel
        self.assertEqual(_resolve_novel("斗破苍穹作者：天蚕土豆"), "斗破苍穹作者：天蚕土豆")

    def test_resolve_novel_rejects_path_traversal(self):
        from fastapi import HTTPException
        from web.routes.agent_routes import _resolve_novel
        with self.assertRaises(HTTPException):
            _resolve_novel("../../etc/passwd")

    def test_novel_json_path_real_book(self):
        """默认书能定位到真实原文 JSON（《书名》作者：xx.json 命名）"""
        import os
        from web.routes.agent_routes import _novel_json_path, DEFAULT_NOVEL
        p = _novel_json_path(DEFAULT_NOVEL)
        self.assertTrue(p.endswith(".json"))
        self.assertNotIn("_chunks", p)
        self.assertTrue(os.path.exists(p), f"原文 JSON 不存在: {p}")

    def test_novel_json_path_unknown_book(self):
        """未知书 → 返回约定路径（不抛异常，由调用方检查存在性）"""
        from web.routes.agent_routes import _novel_json_path
        p = _novel_json_path("不存在的书作者：无名氏")
        self.assertTrue(p.endswith("不存在的书作者：无名氏.json"))


class TestEntryHasContent(unittest.TestCase):
    """编译结果有效性判定（空结果不应写入断点）"""

    def test_empty_placeholder_is_not_content(self):
        from core.chapter_parser import _entry_has_content
        self.assertFalse(_entry_has_content({
            "summary": "第一章", "characters": [], "events": [], "relationships": [],
        }))

    def test_real_entry_is_content(self):
        from core.chapter_parser import _entry_has_content
        self.assertTrue(_entry_has_content({
            "summary": "x", "characters": [{"name": "赵玖"}], "events": [], "relationships": [],
        }))


class TestSemanticSplit(unittest.TestCase):
    """语义分块：按段落边界切割，不打断对话，长段落拆句子"""

    def test_short_text_single_chunk(self):
        from core.chapter_parser import _semantic_split
        chunks = _semantic_split("第一章 穿越", max_chars=2800)
        self.assertEqual(len(chunks), 1)

    def test_paragraph_boundaries(self):
        from core.chapter_parser import _semantic_split
        text = "段落一第一句。段落一第二句。\n\n段落二第一句。\n\n段落三。"
        chunks = _semantic_split(text, max_chars=15)
        self.assertGreaterEqual(len(chunks), 2)

    def test_dialogue_boundary_preserved(self):
        from core.chapter_parser import _semantic_split
        text = "赵玖说：「来者何人？报上名来。」\n\n岳飞答道：「末将岳飞。」"
        chunks = _semantic_split(text, max_chars=80)
        all_text = "".join(chunks)
        self.assertIn("「来者何人？报上名来。」", all_text)
        self.assertIn("「末将岳飞。」", all_text)

    def test_long_paragraph_split_by_sentence(self):
        from core.chapter_parser import _semantic_split
        # 一段超长文本，超过 max_chars 必须拆分
        long = "赵玖率军出征。" * 50
        chunks = _semantic_split(long, max_chars=100)
        self.assertGreater(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(len(c), 300)  # 拆分后每块不能太大

    def test_empty_text(self):
        from core.chapter_parser import _semantic_split
        self.assertEqual(_semantic_split("", max_chars=100), [])


class TestSimpleMerge(unittest.TestCase):
    """回退合并逻辑（LLM 不可用时的规则合并）：别名合并、人物去重、事件去重"""

    def test_character_dedup_across_chunks(self):
        from core.chapter_parser import _simple_merge
        cfg = {"summary_compress_threshold": 500, "summary_compress_target": 200}
        chunks = [
            {"summary": "赵玖出场。",
             "characters": [{"name": "赵玖", "role": "主角", "description": "穿越者"}],
             "events": ["赵玖穿越到南宋"], "relationships": []},
            {"summary": "赵玖又出场。",
             "characters": [{"name": "赵玖", "role": "主角", "description": "御驾亲征"}],
             "events": ["赵玖穿越到南宋"], "relationships": []},
        ]
        merged = _simple_merge(chunks, "测试章", cfg)
        self.assertEqual(len(merged["characters"]), 1)
        self.assertEqual(merged["characters"][0]["name"], "赵玖")
        self.assertEqual(len(merged["events"]), 1)

    def test_alias_merging(self):
        from core.chapter_parser import _simple_merge
        cfg = {"summary_compress_threshold": 500, "summary_compress_target": 200}
        chunks = [
            {"summary": "a",
             "characters": [{"name": "赵玖", "role": "主角",
                              "description": "", "aliases": ["官家"]}],
             "events": [], "relationships": []},
            {"summary": "b",
             "characters": [{"name": "赵玖", "role": "主角",
                              "description": "", "aliases": ["赵构"]}],
             "events": [], "relationships": []},
        ]
        merged = _simple_merge(chunks, "测试章", cfg)
        self.assertEqual(len(merged["characters"]), 1)
        aliases = merged["characters"][0].get("aliases", [])
        self.assertIn("官家", aliases)
        self.assertIn("赵构", aliases)

    def test_relationship_dedup(self):
        from core.chapter_parser import _simple_merge
        cfg = {"summary_compress_threshold": 500, "summary_compress_target": 200}
        chunks = [
            {"summary": "a",
             "characters": [],
             "events": [],
             "relationships": [{"source": "赵玖", "target": "岳飞",
                                 "relation": "君臣"}],
             },
            {"summary": "b",
             "characters": [],
             "events": [],
             "relationships": [{"source": "赵玖", "target": "岳飞",
                                 "relation": "君臣"}],
             },
        ]
        merged = _simple_merge(chunks, "测试章", cfg)
        self.assertEqual(len(merged["relationships"]), 1)

    def test_different_relations_preserved(self):
        from core.chapter_parser import _simple_merge
        cfg = {"summary_compress_threshold": 500, "summary_compress_target": 200}
        chunks = [
            {"summary": "a", "characters": [], "events": [],
             "relationships": [{"source": "赵玖", "target": "岳飞",
                                 "relation": "君臣"}]},
            {"summary": "b", "characters": [], "events": [],
             "relationships": [{"source": "赵玖", "target": "韩世忠",
                                 "relation": "君臣"}]},
        ]
        merged = _simple_merge(chunks, "测试章", cfg)
        self.assertEqual(len(merged["relationships"]), 2)


class TestCheckpointManager(unittest.TestCase):
    """断点管理器：写入、加载、is_completed、reset、阶段隔离"""

    def setUp(self):
        import tempfile, os
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmpdir, ignore_errors=True))

    def test_mark_and_is_completed(self):
        from core.chapter_parser import CheckpointManager
        cpm = CheckpointManager("test_novel", checkpoint_dir=self.tmpdir)
        self.assertFalse(cpm.is_completed(5, "wiki"))
        cpm.mark_completed(5, "wiki")
        self.assertTrue(cpm.is_completed(5, "wiki"))
        self.assertFalse(cpm.is_completed(6, "wiki"))

    def test_persist_across_instances(self):
        from core.chapter_parser import CheckpointManager
        cpm1 = CheckpointManager("test_novel", checkpoint_dir=self.tmpdir)
        cpm1.mark_completed(10, "wiki")
        cpm1.mark_completed(20, "wiki")

        cpm2 = CheckpointManager("test_novel", checkpoint_dir=self.tmpdir)
        self.assertTrue(cpm2.is_completed(10, "wiki"))
        self.assertTrue(cpm2.is_completed(20, "wiki"))
        self.assertFalse(cpm2.is_completed(30, "wiki"))

    def test_phase_isolation(self):
        from core.chapter_parser import CheckpointManager
        cpm = CheckpointManager("test_novel", checkpoint_dir=self.tmpdir)
        cpm.mark_completed(1, "wiki")
        cpm.mark_completed(2, "volume")
        self.assertTrue(cpm.is_completed(1, "wiki"))
        self.assertFalse(cpm.is_completed(1, "volume"))
        self.assertTrue(cpm.is_completed(2, "volume"))

    def test_all_completed(self):
        from core.chapter_parser import CheckpointManager
        cpm = CheckpointManager("test_novel", checkpoint_dir=self.tmpdir)
        cpm.mark_completed(0, "wiki")
        cpm.mark_completed(1, "wiki")
        self.assertTrue(cpm.all_completed({0, 1}, "wiki"))
        self.assertFalse(cpm.all_completed({0, 1, 2}, "wiki"))

    def test_reset_single_phase(self):
        from core.chapter_parser import CheckpointManager
        cpm = CheckpointManager("test_novel", checkpoint_dir=self.tmpdir)
        cpm.mark_completed(1, "wiki")
        self.assertTrue(cpm.is_completed(1, "wiki"))
        cpm.reset("wiki")
        self.assertFalse(cpm.is_completed(1, "wiki"))

    def test_unmark_single_index(self):
        """unmark 只作废指定断点，其余保留（增量编译的末卷失效场景）"""
        from core.chapter_parser import CheckpointManager
        cpm = CheckpointManager("test_novel", checkpoint_dir=self.tmpdir)
        cpm.mark_completed(0, "volume")
        cpm.mark_completed(50, "volume")
        cpm.mark_completed(100, "volume")
        cpm.unmark(100, "volume")
        self.assertTrue(cpm.is_completed(0, "volume"))
        self.assertTrue(cpm.is_completed(50, "volume"))
        self.assertFalse(cpm.is_completed(100, "volume"))
        # 未存在的索引 unmark 是 no-op，且跨实例持久化
        cpm.unmark(999, "volume")
        cpm2 = CheckpointManager("test_novel", checkpoint_dir=self.tmpdir)
        self.assertTrue(cpm2.is_completed(0, "volume"))
        self.assertFalse(cpm2.is_completed(100, "volume"))


class TestSubChunkCache(unittest.TestCase):
    """子块断点缓存：init → mark_done → get_incomplete → cleanup"""

    def setUp(self):
        import tempfile, os
        self.tmpdir = os.path.join(tempfile.mkdtemp(), "subchunks")
        os.makedirs(self.tmpdir, exist_ok=True)

    def _make_cache(self):
        from core.chapter_parser import SubChunkCache
        # 重写路径到临时目录
        cache = SubChunkCache("test_novel")
        cache._dir = self.tmpdir
        return cache

    def test_init_and_mark_done(self):
        from core.chapter_parser import SubChunkCache
        cache = self._make_cache()
        cache.init_chapter(5, 3)
        cache.mark_subchunk_done(5, 0, {"summary": "块0", "characters": [{"name": "赵玖"}]})
        cache.mark_subchunk_done(5, 2, {"summary": "块2", "characters": []})

        incomplete = cache.get_incomplete(5, 3)
        self.assertEqual(incomplete, [1])  # 只有块1 未完成

    def test_all_done_no_incomplete(self):
        cache = self._make_cache()
        cache.init_chapter(5, 3)
        for i in range(3):
            cache.mark_subchunk_done(5, i, {"summary": f"块{i}", "characters": []})
        self.assertEqual(cache.get_incomplete(5, 3), [])

    def test_get_cached_results(self):
        cache = self._make_cache()
        cache.init_chapter(5, 2)
        cache.mark_subchunk_done(5, 0, {"summary": "块0"})
        cache.mark_subchunk_done(5, 1, {"summary": "块1"})
        results = cache.get_cached_results(5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["summary"], "块0")


class TestAtomicWrite(unittest.TestCase):
    """原子写入：os.replace 防止半残文件"""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def test_atomic_write_creates_file(self):
        from core.chapter_parser import _atomic_write
        path = os.path.join(self.tmpdir, "test.json")
        _atomic_write({"key": "value"}, path)
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["key"], "value")

    def test_atomic_write_creates_dirs(self):
        from core.chapter_parser import _atomic_write
        path = os.path.join(self.tmpdir, "a", "b", "c.json")
        _atomic_write([1, 2, 3], path)
        self.assertTrue(os.path.exists(path))


class TestBackup(unittest.TestCase):
    """备份：生成备份 + 只保留 3 份"""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def test_backup_creates_file(self):
        from core.chapter_parser import _backup
        path = os.path.join(self.tmpdir, "test.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("original")
        bak = _backup(path)
        self.assertIsNotNone(bak)
        self.assertTrue(os.path.exists(bak))

    def test_backup_nonexistent_returns_none(self):
        from core.chapter_parser import _backup
        self.assertIsNone(_backup(os.path.join(self.tmpdir, "nope.json")))

    def test_backup_keeps_only_three(self):
        from core.chapter_parser import _backup
        path = os.path.join(self.tmpdir, "test.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("v")
        for _ in range(5):
            _backup(path)
        pattern = os.path.join(self.tmpdir, "test.json.bak.*")
        backups = list(__import__('glob').glob(pattern))
        self.assertLessEqual(len(backups), 3)


class TestMySQLBackend(unittest.TestCase):
    """MySQLBackend mock 测试：环境无 MySQL，patch pymysql.connect 验证 SQL 与语义"""

    def _make_backend(self):
        from unittest.mock import patch, MagicMock
        from core.db.mysql_backend import MySQLBackend

        self.cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = self.cur
        self.conn = conn
        patcher = patch("core.db.mysql_backend.pymysql.connect", return_value=conn)
        self.mock_connect = patcher.start()
        self.addCleanup(patcher.stop)
        return MySQLBackend({
            "host": "dbhost", "port": 3307, "user": "u",
            "password": "p", "database": "testdb",
        })

    _USER_ROW = {
        "id": "u_1", "username": "alice", "password_hash": "hashed",
        "role": "editor", "created_at": "2026-01-01T00:00:00", "is_active": 1,
    }

    def test_lazy_connect_and_params(self):
        """构造时不连接；首次执行才按配置参数建连"""
        from core.db.mysql_backend import MySQLBackend
        self._make_backend()  # 启动 patch（mock_connect / cur）
        backend = MySQLBackend({"host": "dbhost", "port": "3307", "user": "u",
                                "password": "p", "database": "testdb"})
        self.mock_connect.assert_not_called()
        self.cur.fetchone.return_value = None
        backend.get_user("x")
        self.mock_connect.assert_called_once()
        kwargs = self.mock_connect.call_args.kwargs
        self.assertEqual(kwargs["host"], "dbhost")
        self.assertEqual(kwargs["port"], 3307)          # 字符串端口被转为 int
        self.assertEqual(kwargs["database"], "testdb")
        self.assertTrue(kwargs["autocommit"])

    def test_get_user_hit(self):
        backend = self._make_backend()
        self.cur.fetchone.return_value = dict(self._USER_ROW)
        user = backend.get_user("alice")
        self.assertIsNotNone(user)
        self.assertEqual(user.id, "u_1")
        self.assertEqual(user.password_hash, "hashed")
        self.assertEqual(user.role, "editor")
        self.assertTrue(user.is_active)
        sql, params = self.cur.execute.call_args.args
        self.assertIn("FROM users WHERE username = %s", sql)
        self.assertEqual(params, ("alice",))

    def test_get_user_miss_returns_none(self):
        backend = self._make_backend()
        self.cur.fetchone.return_value = None
        self.assertIsNone(backend.get_user("nobody"))

    def test_create_user_insert_params(self):
        from core.db.models import UserModel
        backend = self._make_backend()
        user = UserModel(id="u_9", username="bob", password_hash="ph",
                         role="viewer", created_at="2026-02-02T00:00:00", is_active=True)
        result = backend.create_user(user)
        self.assertIs(result, user)
        sql, params = self.cur.execute.call_args.args
        self.assertTrue(sql.startswith("INSERT INTO users"))
        self.assertIn("%s", sql)
        self.assertNotIn("bob", sql)  # 值必须全部参数化，禁止拼接
        self.assertEqual(params, ("u_9", "bob", "ph", "viewer",
                                  "2026-02-02T00:00:00", True))

    def test_user_exists(self):
        backend = self._make_backend()
        self.cur.fetchone.return_value = {"one": 1}
        self.assertTrue(backend.user_exists("alice"))
        sql, params = self.cur.execute.call_args.args
        self.assertIn("WHERE username = %s", sql)
        self.assertEqual(params, ("alice",))
        self.cur.fetchone.return_value = None
        self.assertFalse(backend.user_exists("ghost"))

    def test_list_users_pagination(self):
        backend = self._make_backend()
        self.cur.fetchone.return_value = {"cnt": 3}
        self.cur.fetchall.return_value = [dict(self._USER_ROW)]
        users, total = backend.list_users(page=2, page_size=2)
        self.assertEqual(total, 3)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0].username, "alice")
        # 第二次 execute 为分页查询：LIMIT page_size OFFSET (page-1)*page_size
        sql, params = self.cur.execute.call_args_list[1].args
        self.assertIn("LIMIT %s OFFSET %s", sql)
        self.assertEqual(params, (2, 2))

    def test_save_audit_log_insert_and_trim(self):
        from core.db.models import AuditLogModel
        backend = self._make_backend()
        log = AuditLogModel(id="log_1", action="login", username="alice",
                            resource="", detail="ok", status="success",
                            ip="127.0.0.1", created_at="2026-03-03T00:00:00")
        result = backend.save_audit_log(log)
        self.assertIs(result, log)
        calls = self.cur.execute.call_args_list
        self.assertEqual(len(calls), 2)
        sql, params = calls[0].args
        self.assertTrue(sql.startswith("INSERT INTO audit_logs"))
        self.assertEqual(params, ("log_1", "login", "alice", "", "ok",
                                  "success", "127.0.0.1", "2026-03-03T00:00:00"))
        trim_sql, trim_params = calls[1].args
        self.assertIn("DELETE FROM audit_logs", trim_sql)
        self.assertEqual(trim_params, (10000,))  # 保留最近 10000 条

    def test_health_check_ok(self):
        backend = self._make_backend()
        self.cur.fetchone.return_value = {"cnt": 5}
        health = backend.health_check()
        self.assertEqual(health["backend"], "mysql")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["users_count"], 5)

    def test_health_check_error(self):
        backend = self._make_backend()
        self.cur.execute.side_effect = Exception("connection lost")
        health = backend.health_check()
        self.assertEqual(health["backend"], "mysql")
        self.assertEqual(health["status"], "error")
        self.assertIn("connection lost", health["error"])


class TestSQLiteBackend(unittest.TestCase):
    """SQLite 后端：CRUD、审计日志、健康检查"""

    def setUp(self):
        import tempfile, os
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        os.environ["ADMIN_PASSWORD"] = "testpass123"  # 避免随机密码
        self.addCleanup(lambda: os.environ.pop("ADMIN_PASSWORD", None))

    def _make_backend(self):
        from core.db.sqlite_backend import SQLiteBackend
        return SQLiteBackend(db_path=self.db_path)

    def test_default_admin_created(self):
        be = self._make_backend()
        user = be.get_user("admin")
        self.assertIsNotNone(user)
        self.assertEqual(user.role, "admin")
        self.assertTrue(user.is_active)

    def test_password_is_bcrypt_not_sha256(self):
        be = self._make_backend()
        user = be.get_user("admin")
        self.assertTrue(user.password_hash.startswith("$2b$"),
                        f"密码应为 bcrypt 格式，实际: {user.password_hash[:20]}")

    def test_create_and_get_user(self):
        from core.db.models import UserModel
        be = self._make_backend()
        u = UserModel(id="u_test", username="testuser", password_hash="hash",
                      role="viewer", created_at="2026-01-01T00:00:00", is_active=True)
        be.create_user(u)
        self.assertTrue(be.user_exists("testuser"))
        fetched = be.get_user("testuser")
        self.assertEqual(fetched.role, "viewer")

    def test_audit_log_roundtrip(self):
        from core.db.models import AuditLogModel
        be = self._make_backend()
        log = AuditLogModel(id="log_1", action="login", username="admin",
                            resource="test", detail="ok", status="success",
                            ip="127.0.0.1", created_at="2026-01-01T00:00:00")
        be.save_audit_log(log)
        logs, total = be.query_audit_logs(action="login", page=1, page_size=10)
        self.assertEqual(total, 1)
        self.assertEqual(logs[0].action, "login")

    def test_health_check(self):
        be = self._make_backend()
        health = be.health_check()
        self.assertEqual(health["backend"], "sqlite")
        self.assertEqual(health["status"], "ok")


class TestDialogueCompiler(unittest.TestCase):
    """对话 Wiki 编译：准入门槛、LLM 失败路径、冲突合并、检索"""

    _VALID_LLM_RESPONSE = json.dumps({
        "topic": "赵玖的角色定位",
        "conclusion": "赵玖是穿越到南宋的现代大学生，在八公山上重整旗鼓。",
        "key_points": ["穿越者身份", "重组抗金力量"],
        "evidence_chapters": ["第一章 明道宫", "第二章 赤心队"],
        "speculative": False,
    }, ensure_ascii=False)

    _MERGE_RESPONSE = json.dumps({
        "action": "keep_both",
        "merged_entry": None,
        "reason": "两个条目讨论不同的主题",
    }, ensure_ascii=False)

    def setUp(self):
        import tempfile, os
        self.tmpdir = tempfile.mkdtemp()
        self._orig_wiki_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "wiki",
        )
        # 不触碰真实文件系统

    def _make_record(self, review_passed=True, entities=None, report=""):
        """构造一条测试用会话记录"""
        if entities is None:
            entities = ["赵玖", "岳飞", "韩世忠"]
        if not report:
            report = "赵玖是小说《绍宋》的主角，原为现代大学生，穿越成为宋高宗赵构。" \
                      "他在八公山上重整旗鼓，任用岳飞、韩世忠等名将，力图改变历史走向。" \
                      "小说描绘了他力挽狂澜抗击金朝逐步统一的过程，是一部优秀的历史小说。" \
                      "故事发生在南宋初年靖康之耻后，具有浓厚的历史色彩和文学价值。"
        return {
            "session_id": "test_session_001",
            "novel": "绍宋作者：榴弹怕水",
            "review_passed": review_passed,
            "turns": [{"query": "赵玖是什么角色？", "report": report, "ts": "2026-07-01T00:00:00"}],
            "entities": entities,
            "created_at": "2026-07-01T00:00:00",
        }

    def test_review_not_passed_returns_none(self):
        """审核未通过 → 返回 None"""
        from core.dialogue_compiler import compile_session
        record = self._make_record(review_passed=False)
        self.assertIsNone(compile_session(record))

    def test_fewer_than_two_entities_returns_none(self):
        """实体数 < 2 → 返回 None"""
        from core.dialogue_compiler import compile_session
        record = self._make_record(entities=["赵玖"])
        self.assertIsNone(compile_session(record))

    def test_report_too_short_returns_none(self):
        """报告 < 100 字 → 返回 None"""
        from core.dialogue_compiler import compile_session
        record = self._make_record(report="赵玖是主角。")
        self.assertIsNone(compile_session(record))

    def test_valid_compile(self):
        """Mock LLM 返回合法 JSON → 产出完整条目"""
        from core.dialogue_compiler import compile_session
        import core.dialogue_compiler as dc_mod

        saved = dc_mod.call_llm
        try:
            dc_mod.call_llm = lambda *a, **kw: self._VALID_LLM_RESPONSE
            entry = compile_session(self._make_record())
        finally:
            dc_mod.call_llm = saved

        self.assertIsNotNone(entry, "compile_session 应返回有效条目")
        self.assertIn("id", entry)
        self.assertTrue(entry["id"].startswith("dlg_"))
        self.assertGreater(len(entry["entities"]), 0)
        self.assertGreater(len(entry["conclusion"]), 0)

    def test_llm_returns_none(self):
        """LLM 返回 None → 返回 None，不落盘"""
        from core.dialogue_compiler import compile_session
        import core.dialogue_compiler as dc_mod

        saved = dc_mod.call_llm
        try:
            dc_mod.call_llm = lambda *a, **kw: None
            entry = compile_session(self._make_record())
        finally:
            dc_mod.call_llm = saved
        self.assertIsNone(entry)

    def test_llm_returns_invalid_json(self):
        """LLM 返回非 JSON → 返回 None"""
        from core.dialogue_compiler import compile_session
        import core.dialogue_compiler as dc_mod

        saved = dc_mod.call_llm
        try:
            dc_mod.call_llm = lambda *a, **kw: "这不是JSON"
            entry = compile_session(self._make_record())
        finally:
            dc_mod.call_llm = saved
        self.assertIsNone(entry)

    def test_save_dialogue_wiki_no_conflict(self):
        """无冲突 → 直接追加，原子写入内容完整"""
        import tempfile, os, json
        from pathlib import Path
        from unittest.mock import patch
        from core.dialogue_compiler import save_dialogue_wiki

        tmp_file = os.path.join(self.tmpdir, "绍宋作者：榴弹怕水_dialogue.json")
        os.makedirs(os.path.dirname(tmp_file), exist_ok=True)

        entry = {
            "id": "dlg_test001", "topic": "测试主题", "conclusion": "测试结论",
            "key_points": ["要点1"], "entities": ["赵玖"],
            "evidence_chapters": [], "speculative": False,
            "source_session": "s1", "created_at": "2026-01-01T00:00:00",
        }

        with patch("core.dialogue_compiler.WIKI_DIR", Path(self.tmpdir)):
            result = save_dialogue_wiki("绍宋作者：榴弹怕水", entry)
        self.assertTrue(result)
        self.assertTrue(os.path.exists(tmp_file))
        with open(tmp_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(len(saved["entries"]), 1)
        self.assertEqual(saved["entries"][0]["id"], "dlg_test001")

    def test_save_dialogue_wiki_merge(self):
        """冲突合并: merge 分支（LLM 返回 action=merge）"""
        import tempfile, os, json
        from pathlib import Path
        from unittest.mock import patch
        from core.dialogue_compiler import save_dialogue_wiki

        tmp_file = os.path.join(self.tmpdir, "绍宋作者：榴弹怕水_dialogue.json")
        os.makedirs(os.path.dirname(tmp_file), exist_ok=True)
        # 预存一条旧条目（共享实体"赵玖"）
        old = {
            "id": "dlg_old001", "topic": "旧主题", "conclusion": "旧结论",
            "key_points": ["旧要点"], "entities": ["赵玖", "岳飞"],
            "evidence_chapters": [], "speculative": False,
            "source_session": "s0", "created_at": "2026-01-01T00:00:00",
        }
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump({"entries": [old]}, f, ensure_ascii=False)

        new_entry = {
            "id": "dlg_new001", "topic": "新主题", "conclusion": "新结论",
            "key_points": ["新要点"], "entities": ["赵玖", "韩世忠"],
            "evidence_chapters": [], "speculative": False,
            "source_session": "s2", "created_at": "2026-02-01T00:00:00",
        }

        merged = {
            "id": "dlg_merged001", "topic": "合并主题", "conclusion": "合并结论",
            "key_points": ["旧要点", "新要点"],
            "entities": ["赵玖", "岳飞", "韩世忠"],
            "evidence_chapters": [], "speculative": False,
            "source_session": "s2", "created_at": "2026-02-01T00:00:00",
        }
        merge_response = json.dumps({
            "action": "merge",
            "merged_entry": merged,
            "reason": "同一主题互补",
        }, ensure_ascii=False)

        with patch("core.dialogue_compiler.WIKI_DIR", Path(self.tmpdir)):
            import core.dialogue_compiler as dc_mod
            saved = dc_mod.call_llm
            try:
                dc_mod.call_llm = lambda *a, **kw: merge_response
                result = save_dialogue_wiki("绍宋作者：榴弹怕水", new_entry)
            finally:
                dc_mod.call_llm = saved
        self.assertTrue(result)
        with open(tmp_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 旧条目应被删除，合并结果应存在
        self.assertEqual(len(saved["entries"]), 1)
        self.assertEqual(saved["entries"][0]["id"], "dlg_merged001")

    def test_search_dialogue_wiki_hit(self):
        """实体命中排序正确"""
        import tempfile, os, json
        from unittest.mock import patch

        tmp_file = os.path.join(self.tmpdir, "绍宋作者：榴弹怕水_dialogue.json")
        os.makedirs(os.path.dirname(tmp_file), exist_ok=True)
        dialogue_data = {
            "entries": [
                {"id": "d1", "topic": "赵玖的角色", "conclusion": "他是穿越者",
                 "entities": ["赵玖"], "key_points": [], "evidence_chapters": []},
                {"id": "d2", "topic": "岳飞的关系", "conclusion": "岳飞的攻防战",
                 "entities": ["岳飞", "韩世忠"], "key_points": [], "evidence_chapters": []},
            ],
        }
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(dialogue_data, f, ensure_ascii=False)

        # Mock retriever to use our tmp file
        from unittest.mock import MagicMock
        retriever = MagicMock()
        retriever._novel_key = "绍宋作者：榴弹怕水"
        retriever.search_dialogue_wiki = lambda query, top_k=3: _search_dialogue_helper(
            tmp_file, query, top_k
        )

        # 查询 "赵玖" → 应命中 d1
        results = retriever.search_dialogue_wiki("赵玖是什么角色", top_k=3)
        self.assertGreater(len(results), 0)

    def test_search_dialogue_wiki_no_hit(self):
        """无命中时返回空列表"""
        import tempfile, os, json
        from unittest.mock import patch

        tmp_file = os.path.join(self.tmpdir, "绍宋作者：榴弹怕水_dialogue.json")
        os.makedirs(os.path.dirname(tmp_file), exist_ok=True)
        dialogue_data = {
            "entries": [
                {"id": "d1", "topic": "赵玖", "conclusion": "结论",
                 "entities": ["赵玖"], "key_points": [], "evidence_chapters": []},
            ],
        }
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(dialogue_data, f, ensure_ascii=False)

        # 用不相关的查询
        from core.retriever import NovelRetriever
        # 直接用搜索辅助函数测试
        from core.text_match import ngram_hits
        # 不相关的查询不应命中
        for e in dialogue_data["entries"]:
            score = 0
            for entity in e.get("entities", []):
                if entity in "完颜宗弼":
                    score += 5
            score += ngram_hits("完颜宗弼", e.get("topic", "") + " " + e.get("conclusion", ""))
            self.assertEqual(score, 0)


def _search_dialogue_helper(filepath, query, top_k=3):
    """辅助函数：从临时文件读取对话 Wiki 并搜索"""
    import json
    from core.text_match import ngram_hits

    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries", [])
    scored = []
    for entry in entries:
        score = 0
        for entity in entry.get("entities", []):
            if entity and entity in query:
                score += 5
        combined = (entry.get("topic", "") + " " + entry.get("conclusion", ""))
        score += ngram_hits(query, combined)
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda x: -x[0])
    return [{
        "source_type": "dialogue", "topic": e[1].get("topic", ""),
        "conclusion": e[1].get("conclusion", ""), "key_points": e[1].get("key_points", []),
        "text": f"【讨论结论】{e[1].get('topic', '')}：{e[1].get('conclusion', '')}",
    } for e in scored[:top_k]]


class TestCommunityDetection(unittest.TestCase):
    """人物社群检测：社区数、标签、摘要生成"""

    def _make_graph(self, n_nodes=15, density=0.3):
        """构造测试用人物关系图"""
        import networkx as nx
        G = nx.Graph()
        for i in range(n_nodes):
            role = "主角" if i == 0 else ("反派" if i < 4 else "配角")
            G.add_node(f"角色{i}", role=role, mention_count=100 - i * 5, chapter_count=20 - i)
        # 构建两个稠密子图 + 跨组边
        for i in range(6):
            for j in range(i + 1, 6):
                if i < 3 <= j:
                    continue
                G.add_edge(f"角色{i}", f"角色{j}", relation="好友", weight=5)
        for i in range(7, 13):
            for j in range(i + 1, 13):
                G.add_edge(f"角色{i}", f"角色{j}", relation="同僚", weight=3)
        # 几条跨组边
        G.add_edge("角色0", "角色7", relation="认识", weight=1)
        G.add_edge("角色2", "角色8", relation="敌对", weight=1)
        return G

    def test_detect_at_least_two_communities(self):
        """稠密子图应被分为 >=2 个社区"""
        from core.graph_community import detect_communities
        G = self._make_graph(15)
        result = detect_communities(G)
        unique = set(result.values())
        self.assertGreaterEqual(len(unique), 2)

    def test_small_graph_single_community(self):
        """节点 <3 的图全部归同一社区"""
        from core.graph_community import detect_communities
        import networkx as nx
        G = nx.Graph()
        G.add_node("A")
        G.add_node("B")
        result = detect_communities(G)
        self.assertEqual(len(set(result.values())), 1)

    def test_generate_summaries(self):
        """社区摘要非空且包含角色名"""
        from core.graph_community import detect_communities, generate_community_summaries
        G = self._make_graph(15)
        communities = detect_communities(G)
        summaries = generate_community_summaries(G, communities)
        self.assertGreater(len(summaries), 0)
        for s in summaries:
            self.assertIn("label", s)
            self.assertGreater(len(s.get("characters", [])), 0)
            self.assertGreater(len(s.get("summary", "")), 0)


class TestCommunitySearch(unittest.TestCase):
    """社区检索：关键词命中，无命中返回空（直接测 NovelRetriever.search_communities 真实实现）"""

    def _make_retriever(self, community_data):
        """绕过 __init__ 构造一个只带社区数据的真实检索器实例"""
        from core.retriever import NovelRetriever
        r = object.__new__(NovelRetriever)
        r._community_data = community_data
        return r

    def test_keyword_hit(self):
        """实体名命中 → 返回对应社区结果"""
        data = {
            "summaries": [
                {"community_id": 0, "label": "主角核心圈", "characters": ["赵玖", "岳飞", "韩世忠"],
                 "member_count": 5, "summary": "以赵玖为首的抗金核心团体"},
                {"community_id": 1, "label": "金朝阵营", "characters": ["完颜兀术", "完颜娄室"],
                 "member_count": 3, "summary": "金朝入侵力量"},
            ],
        }
        r = self._make_retriever(data)
        results = r.search_communities("赵玖和岳飞是什么关系", top_k=2)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["community_id"], 0)
        self.assertIn("赵玖", results[0]["top_characters"])

    def test_no_hit_returns_empty(self):
        """无匹配 → 空列表"""
        data = {"summaries": [
            {"community_id": 0, "label": "主角圈", "characters": ["赵玖"],
             "member_count": 1, "summary": "x"},
        ]}
        r = self._make_retriever(data)
        results = r.search_communities("完全无关的查询", top_k=2)
        self.assertEqual(results, [])

    def test_no_community_data_returns_empty(self):
        """未编译社区数据 → 空列表"""
        r = self._make_retriever(None)
        self.assertEqual(r.search_communities("任意查询"), [])


class TestPPRSearch(unittest.TestCase):
    """PPR 多跳图检索：种子命中 → 扩散排序；无种子 → 空"""

    def _make_retriever(self):
        """绕过 __init__，只挂一个小型人物关系图"""
        import networkx as nx
        from core.retriever import NovelRetriever
        G = nx.DiGraph()
        G.add_edge("赵玖", "岳飞", relation="君臣", weight=10)
        G.add_edge("岳飞", "韩世忠", relation="同袍", weight=5)
        G.add_edge("韩世忠", "梁红玉", relation="夫妻", weight=3)
        G.add_edge("路人甲", "路人乙", relation="同乡", weight=1)
        r = object.__new__(NovelRetriever)
        r.graph = G
        return r

    def test_seed_hit_discovers_bridge_nodes(self):
        """查询「赵玖」→ 岳飞等关联人物被扩散发现，且种子有 is_seed 标记"""
        r = self._make_retriever()
        result = r.search_by_ppr("赵玖的抗金部署", top_k=4)
        self.assertEqual(result["seed_nodes"], ["赵玖"])
        names = [p["name"] for p in result["ppr_nodes"]]
        self.assertIn("岳飞", names)
        self.assertIn("赵玖", names)
        seed_flags = {p["name"]: p["is_seed"] for p in result["ppr_nodes"]}
        self.assertTrue(seed_flags["赵玖"])
        self.assertFalse(seed_flags["岳飞"])
        # 直接相连的岳飞应排在断连的路人之前
        self.assertNotIn("路人甲", names)
        # top 节点之间的关系应非空（赵玖-岳飞 必在）
        pairs = {(rel["source"], rel["target"]) for rel in result["relations"]}
        self.assertIn(("赵玖", "岳飞"), pairs)

    def test_no_seed_returns_empty(self):
        """查询无实体命中 → 三个字段全空"""
        r = self._make_retriever()
        result = r.search_by_ppr("完全不相关的查询")
        self.assertEqual(result["seed_nodes"], [])
        self.assertEqual(result["ppr_nodes"], [])
        self.assertEqual(result["relations"], [])


class TestSelfLoopFilter(unittest.TestCase):
    """自环边（上游实体合并产物）在 build_graph / load_graph 中被过滤"""

    def test_build_graph_drops_self_loops(self):
        from core.knowledge_graph import build_graph
        char_map = {
            "赵玖": {"role": "主角", "mention_count": 10, "chapters": ["第1章"]},
            "岳飞": {"role": "将领", "mention_count": 8, "chapters": ["第1章"]},
        }
        rels = [
            {"source": "赵玖", "target": "岳飞", "relation": "君臣", "weight": 5, "chapters": ["第1章"]},
            {"source": "赵玖", "target": "赵玖", "relation": "任命赵御史", "weight": 3, "chapters": ["第1章"]},
        ]
        G = build_graph(char_map, rels)
        self.assertEqual(G.number_of_edges(), 1)
        self.assertTrue(G.has_edge("赵玖", "岳飞"))
        self.assertFalse(G.has_edge("赵玖", "赵玖"))

    def test_load_graph_drops_self_loops(self):
        import tempfile
        from core.knowledge_graph import load_graph
        data = {
            "nodes": [{"name": "赵玖"}, {"name": "岳飞"}],
            "edges": [
                {"source": "赵玖", "target": "岳飞", "relation": "君臣", "weight": 5},
                {"source": "赵玖", "target": "赵玖", "relation": "自环", "weight": 3},
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            path = f.name
        try:
            G = load_graph(path)
        finally:
            os.unlink(path)
        self.assertEqual(G.number_of_edges(), 1)
        self.assertTrue(G.has_edge("赵玖", "岳飞"))


class TestResearcherGraphAliasFallback(unittest.TestCase):
    """图谱检索别名兜底：直接零命中时走 LLM 实体归一化"""

    def _make_researcher(self):
        import networkx as nx
        from core.retriever import NovelRetriever
        from core.agents.researcher import Researcher
        G = nx.DiGraph()
        G.add_edge("岳飞", "张宪", relation="兄弟兼长官", weight=5)
        r = object.__new__(NovelRetriever)
        r.graph = G
        return Researcher(r)

    def test_alias_fallback_hit(self):
        """「岳王爷」子串不命中 → LLM 归一化为「岳飞」→ 图谱命中"""
        from unittest.mock import patch
        researcher = self._make_researcher()
        with patch("core.llm.call_llm", return_value='["岳飞"]'):
            text = researcher._search_graph("岳王爷最信任的部下是谁", "")
        self.assertIn("岳飞", text)
        self.assertIn("张宪", text)

    def test_llm_failure_returns_not_found(self):
        """LLM 不可用 → 保持原有「未找到」行为"""
        from unittest.mock import patch
        researcher = self._make_researcher()
        with patch("core.llm.call_llm", return_value=None):
            text = researcher._search_graph("岳王爷最信任的部下是谁", "")
        self.assertIn("未找到", text)


class TestCommunitySummaryCache(unittest.TestCase):
    """社区摘要缓存：成员集合不变 → 复用旧摘要，不调 LLM（增量编译场景）"""

    def _make_graph(self, n=12):
        import networkx as nx
        G = nx.DiGraph()
        names = [f"人物{i}" for i in range(n)]
        for name in names:
            G.add_node(name, role="配角", mention_count=5)
        for i in range(len(names)):
            for j in range(len(names)):
                if i != j:
                    G.add_edge(names[i], names[j], relation="同僚", weight=1)
        return G

    def test_unchanged_members_reuse_cache(self):
        from unittest.mock import patch
        from core.graph_community import detect_communities, generate_community_summaries
        G = self._make_graph()
        communities = detect_communities(G)
        with patch("core.graph_community.call_llm", return_value="一群肝胆相照的江湖豪杰") as m1:
            first = generate_community_summaries(G, communities, novel="测试书")
        self.assertGreaterEqual(m1.call_count, 1)
        large = [s for s in first if s["member_count"] >= 10]
        self.assertTrue(large)
        self.assertIn("member_key", large[0])

        # 成员未变 → 复用缓存，零 LLM 调用
        with patch("core.graph_community.call_llm") as m2:
            second = generate_community_summaries(G, communities, novel="测试书",
                                                 cached_summaries=first)
        m2.assert_not_called()
        second_large = [s for s in second if s["member_count"] >= 10]
        self.assertEqual(second_large[0]["summary"], large[0]["summary"])

    def test_changed_members_call_llm(self):
        """成员集合变化 → 缓存失效，重新调 LLM"""
        from unittest.mock import patch
        from core.graph_community import detect_communities, generate_community_summaries
        G = self._make_graph()
        communities = detect_communities(G)
        with patch("core.graph_community.call_llm", return_value="旧摘要"):
            first = generate_community_summaries(G, communities, novel="测试书")

        G.add_node("新人物", role="配角", mention_count=1)
        G.add_edge("新人物", "人物0", relation="同僚", weight=1)
        communities2 = detect_communities(G)
        with patch("core.graph_community.call_llm", return_value="新摘要") as m:
            second = generate_community_summaries(G, communities2, novel="测试书",
                                                  cached_summaries=first)
        grown = [s for s in second if "新人物" in s["characters"]]
        self.assertTrue(grown)
        self.assertEqual(grown[0]["summary"], "新摘要")
        self.assertGreaterEqual(m.call_count, 1)


class TestHybridVectorSearch(unittest.TestCase):
    """混合向量检索：实体精确腿 + RRF 融合排序"""

    class FakeIndexer:
        def __init__(self):
            self.calls = []

        def search(self, query, top_k=20, novel_key=None, contains=None):
            self.calls.append(contains)
            if contains == "岳飞":
                return [{"chunk_id": "e1", "text": "岳飞率兵破敌", "metadata": {}, "score": 0.1}]
            return [
                {"chunk_id": "v1", "text": "无关段落", "metadata": {}, "score": 0.2},
                {"chunk_id": "e1", "text": "岳飞率兵破敌", "metadata": {}, "score": 0.5},
            ]

    def _make_retriever(self):
        import networkx as nx
        from core.retriever import NovelRetriever
        r = object.__new__(NovelRetriever)
        G = nx.DiGraph()
        G.add_node("岳飞", role="将领")
        r.graph = G
        r._vector_indexer = self.FakeIndexer()
        r._vector_key = "shaosong"  # 真实 __init__ 中由 NOVEL_KEY_TO_SHORT 映射得到
        return r

    def test_entity_leg_boosts_entity_chunk(self):
        """查询含图谱实体 → 触发 contains 腿，含实体 chunk 排第一"""
        r = self._make_retriever()
        results = r.search_by_vector("岳飞的事迹", top_k=5)
        self.assertIn("岳飞", r._vector_indexer.calls)
        self.assertEqual(results[0]["chunk_id"], "e1")

    def test_no_entity_keeps_vector_order(self):
        """查询无实体 → 只有纯向量腿，顺序与旧版一致"""
        r = self._make_retriever()
        results = r.search_by_vector("完全没有实体的查询", top_k=5)
        self.assertEqual(r._vector_indexer.calls, [None])
        self.assertEqual(results[0]["chunk_id"], "v1")

    def test_dual_entity_adds_cooccurrence_leg(self):
        """查询含两个图谱实体 → 追加共现腿（contains 为双实体 list），共现块排第一"""
        r = self._make_retriever()
        r.graph.add_node("赵玖", role="主角")
        idx = r._vector_indexer

        orig_search = idx.search
        def search(query, top_k=20, novel_key=None, contains=None):
            if isinstance(contains, list) and len(contains) == 2:
                idx.calls.append(contains)
                return [{"chunk_id": "co1", "text": "岳飞与赵玖同框", "metadata": {}, "score": 0.05}]
            return orig_search(query, top_k=top_k, novel_key=novel_key, contains=contains)
        idx.search = search

        results = r.search_by_vector("岳飞和赵玖的关系", top_k=5)
        self.assertIn(["岳飞", "赵玖"], idx.calls)
        # 共现块经 RRF 融合进入结果（本 fixture 中各腿得分恰好打平，
        # 不断言具体位次，机制验证以真实库的 recall 评估为准）
        ids = [r_["chunk_id"] for r_ in results]
        self.assertIn("co1", ids)


class TestEmbeddingPick(unittest.TestCase):
    """嵌入模型选择：NOVEL_EMBEDDING=bge 时用 BGE 中文模型，失败回退默认 ONNX"""

    def test_default_is_onnx(self):
        """未设置环境变量 → 默认 ONNX，collection 名不变（向后兼容）"""
        from core.chunker import VectorStoreIndexer
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOVEL_EMBEDDING", None)
            fn, name, suffix = VectorStoreIndexer._pick_embedding()
        self.assertIsNone(fn)
        self.assertEqual(name, "default-onnx")
        self.assertEqual(suffix, "")

    def test_bge_preferred_when_available(self):
        import chromadb.utils.embedding_functions as ef
        from core.chunker import VectorStoreIndexer

        class FakeEF:
            def __init__(self, **kw):
                self.kw = kw

            def __call__(self, texts):
                return [[0.1] * 1024 for _ in texts]

        with patch.dict(os.environ, {"NOVEL_EMBEDDING": "bge"}), \
                patch.object(ef, "SentenceTransformerEmbeddingFunction", FakeEF):
            fn, name, suffix = VectorStoreIndexer._pick_embedding()
        self.assertIsInstance(fn, FakeEF)
        self.assertEqual(name, "bge-large-zh-v1.5")
        self.assertEqual(suffix, "_bge")

    def test_fallback_on_init_failure(self):
        """bge 模式下初始化失败（模型未下载等）→ 回退默认 ONNX"""
        import chromadb.utils.embedding_functions as ef
        from core.chunker import VectorStoreIndexer

        class BrokenEF:
            def __init__(self, **kw):
                pass

            def __call__(self, texts):
                raise RuntimeError("模型未下载")

        with patch.dict(os.environ, {"NOVEL_EMBEDDING": "bge"}), \
                patch.object(ef, "SentenceTransformerEmbeddingFunction", BrokenEF):
            fn, name, suffix = VectorStoreIndexer._pick_embedding()
        self.assertIsNone(fn)
        self.assertEqual(name, "default-onnx")
        self.assertEqual(suffix, "")


class TestCoordinatorPrecomputed(unittest.TestCase):
    """Coordinator.run 预计算参数：传入 intent/steps 时跳过对应 LLM 调用"""

    def _make_coordinator(self):
        from core.agents.coordinator import Coordinator

        class FakeResearcher:
            def execute(self, desc, query, intent):
                return f"材料:{desc}"

        class FakeWriter:
            def write(self, query, intent, materials):
                return "最终报告"

        class FakeReviewer:
            def review(self, draft, query, research_materials=""):
                return {"passed": True, "score": 9, "feedback": "", "failure_type": ""}

        return Coordinator(FakeResearcher(), FakeWriter(), FakeReviewer())

    def test_precomputed_skips_intent_and_decompose_llm_calls(self):
        """传入 intent/steps → detect/decompose 节点不再调 LLM"""
        from unittest.mock import patch
        coordinator = self._make_coordinator()
        steps = [{"step": 1, "description": "搜索赵玖的信息"}]
        with patch("core.agents.coordinator.call_llm") as mock_llm:
            result = coordinator.run("赵玖是怎样的人", intent="character", steps=steps)
        mock_llm.assert_not_called()
        self.assertEqual(result["intent"], "character")
        self.assertEqual(result["steps"], steps)
        self.assertEqual(result["final_report"], "最终报告")
        self.assertEqual(result["rounds"], 1)

    def test_without_precomputed_calls_llm(self):
        """不传预计算 → 合并规划一次 LLM 调用同时产出 intent 和 steps"""
        from unittest.mock import patch
        coordinator = self._make_coordinator()

        def fake_llm(messages, **kw):
            prompt = messages[0]["content"]
            if "完成两件事" in prompt:  # PLAN_PROMPT 合并规划
                return '{"intent": "character", "steps": [{"step": 1, "description": "搜索赵玖的信息"}]}'
            return None

        with patch("core.agents.coordinator.call_llm", side_effect=fake_llm) as mock_llm:
            result = coordinator.run("赵玖是怎样的人")
        self.assertEqual(mock_llm.call_count, 1)
        self.assertEqual(result["intent"], "character")
        self.assertEqual(result["steps"][0]["description"], "搜索赵玖的信息")
        self.assertEqual(result["final_report"], "最终报告")

    def test_steps_precomputed_intent_missing_uses_intent_prompt(self):
        """只传 steps 不传 intent → 走单用途意图识别，不覆盖预计算 steps"""
        from unittest.mock import patch
        coordinator = self._make_coordinator()
        steps = [{"step": 1, "description": "搜索赵玖的信息"}]

        def fake_llm(messages, **kw):
            if "判断用户的问题" in messages[0]["content"]:  # INTENT_PROMPT
                return "character"
            return None

        with patch("core.agents.coordinator.call_llm", side_effect=fake_llm) as mock_llm:
            result = coordinator.run("赵玖是怎样的人", steps=steps)
        self.assertEqual(mock_llm.call_count, 1)
        self.assertEqual(result["intent"], "character")
        self.assertEqual(result["steps"], steps)


class TestMaterialPoolDeferredCompress(unittest.TestCase):
    """材料池延迟压缩：首轮不触发 LLM 压缩（单轮流程省 2 次调用/轮），
    第二轮加入时才压缩上一轮；超轮丢弃前补算归档摘要"""

    def _materials(self, tag):
        # 超过 _summarize 的 200 字 LLM 触发下限
        return [{"step": 1, "description": tag, "result": f"{tag}的检索材料。" + "内容" * 150}]

    def test_first_round_no_llm_call(self):
        from core.material_pool import MaterialPool
        with patch("core.material_pool.call_llm") as m:
            pool = MaterialPool(llm_compress=True)
            pool.add_round(self._materials("第一轮"))
        self.assertFalse(m.called, "首轮不应触发压缩 LLM 调用")
        # 单轮 get_effective 返回全量原文（不依赖摘要）
        self.assertIn("第一轮的检索材料", pool.get_effective())

    def test_second_round_compresses_first(self):
        from core.material_pool import MaterialPool
        with patch("core.material_pool.call_llm", side_effect=["摘要", "极简摘要"]) as m:
            pool = MaterialPool(llm_compress=True)
            pool.add_round(self._materials("第一轮"))
            pool.add_round(self._materials("第二轮"))
        self.assertEqual(m.call_count, 2)  # _summarize + _summarize_short，都是第一轮
        self.assertEqual(pool._rounds[0].summary, "摘要")
        self.assertEqual(pool._rounds[1].summary, "")  # 当前轮不压缩
        # 两轮时 get_effective 带第一轮摘要 + 两轮全量
        eff = pool.get_effective()
        self.assertIn("极简摘要", eff)
        self.assertIn("第二轮的检索材料", eff)

    def test_drop_oldest_backfills_archive_summary(self):
        from core.material_pool import MaterialPool
        # max_rounds=2：第三轮加入时第一轮被丢弃，丢弃前补算极简摘要
        with patch("core.material_pool.call_llm", side_effect=lambda *a, **kw: "压缩") as m:
            pool = MaterialPool(llm_compress=True, max_rounds=2)
            pool.add_round(self._materials("一"))
            pool.add_round(self._materials("二"))
            calls_after_2 = m.call_count
            pool.add_round(self._materials("三"))
        self.assertGreater(m.call_count, calls_after_2)
        self.assertTrue(any("第1轮" in a for a in pool._archived))
        self.assertEqual(len(pool._rounds), 2)


class TestPdfParserTiering(unittest.TestCase):
    """PDF 三层引擎策略：扫描件诚实降级 / docling 缺失回退 / 低产出自动升级"""

    def _parser(self):
        from core.document_parser import PdfParser
        return PdfParser()

    def _fake_pdf(self):
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        f.write(b"%PDF-1.4 fake")
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.remove(f.name))
        return f.name

    def test_scanned_pdf_returns_ocr_required(self):
        p = self._parser()
        path = self._fake_pdf()
        with patch.object(PdfParser := type(p), "_has_text_layer", return_value=False):
            result = p.parse(path)
        self.assertEqual(result["chapters"], [])
        self.assertTrue(result["metadata"]["ocr_required"])
        self.assertIn("扫描件", result["metadata"]["error"])

    def test_docling_requested_but_missing_falls_back(self):
        p = self._parser()
        path = self._fake_pdf()
        fake_result = {"title": "t", "chapters": [{"title": "全文", "text": "x" * 300}],
                       "metadata": {"format": "pdf", "engine": "pdfplumber", "chars_per_page": 500}}
        with patch.object(type(p), "_has_text_layer", return_value=True), \
             patch.object(type(p), "_parse_with_pdfplumber", return_value=fake_result), \
             patch.object(type(p), "_parse_with_docling", return_value=None), \
             patch.dict(os.environ, {"NOVEL_PDF_ENGINE": "docling"}):
            result = p.parse(path)
        self.assertEqual(result["metadata"]["engine"], "pdfplumber")

    def test_auto_upgrades_to_docling_on_low_yield(self):
        p = self._parser()
        path = self._fake_pdf()
        low = {"title": "t", "chapters": [], "metadata": {"format": "pdf", "engine": "pdfplumber", "chars_per_page": 30}}
        good = {"title": "t", "chapters": [{"title": "全文", "text": "y" * 500}],
                "metadata": {"format": "pdf", "engine": "docling"}}
        with patch.object(type(p), "_has_text_layer", return_value=True), \
             patch.object(type(p), "_parse_with_pdfplumber", return_value=low), \
             patch.object(type(p), "_parse_with_docling", return_value=good) as m_doc, \
             patch.dict(os.environ, {"NOVEL_PDF_ENGINE": "auto"}):
            result = p.parse(path)
        m_doc.assert_called_once()
        self.assertEqual(result["metadata"]["engine"], "docling")


class TestBookRegistry(unittest.TestCase):
    """书籍注册表：零登记多书支持（core/books.py）"""

    def test_display_name_derivation(self):
        from core.books import display_name_for
        self.assertEqual(display_name_for("绍宋作者：榴弹怕水"), "绍宋")
        self.assertEqual(display_name_for("《斗破苍穹》作者：天蚕土豆"), "斗破苍穹")
        self.assertEqual(display_name_for("神印王座作者：唐家三少"), "神印王座")
        self.assertEqual(display_name_for("诡秘之主"), "诡秘之主")  # 无作者后缀的新书

    def test_vector_key_legacy_and_fallback(self):
        from core.books import vector_key_for
        # 历史三书保持短名（兼容已建索引）
        self.assertEqual(vector_key_for("绍宋作者：榴弹怕水"), "shaosong")
        self.assertEqual(vector_key_for("斗破苍穹作者：天蚕土豆"), "doupo")
        # 新书回退全名：索引与检索走同一函数，两侧天然一致
        self.assertEqual(vector_key_for("新书作者：某某"), "新书作者：某某")

    def test_resolve_name(self):
        from core.books import resolve_name
        self.assertEqual(resolve_name("shaosong"), "绍宋作者：榴弹怕水")
        self.assertEqual(resolve_name("新书作者：某某"), "新书作者：某某")

    def test_list_books_discovers_without_registration(self):
        """未登记的新书（只有编译产物文件）应自动出现在列表中"""
        import tempfile
        from core.books import list_books
        with tempfile.TemporaryDirectory() as d:
            for fname in ["新书甲作者：A_graph.json", "新书甲作者：A_hierarchical.json",
                          "新书乙_hierarchical.json", "test_graph.json"]:
                open(os.path.join(d, fname), "w", encoding="utf-8").write("{}")
            books = list_books(wiki_dir=d)
        names = [b.name for b in books]
        self.assertIn("新书甲作者：A", names)
        self.assertIn("新书乙", names)
        self.assertNotIn("test", names)  # test 数据排除
        book_a = next(b for b in books if b.name == "新书甲作者：A")
        self.assertEqual(book_a.display_name, "新书甲")
        self.assertEqual(book_a.vector_key, "新书甲作者：A")  # 未登记 → 回退全名
        self.assertTrue(book_a.has_graph and book_a.has_wiki)
        book_b = next(b for b in books if b.name == "新书乙")
        self.assertFalse(book_b.has_graph)
        self.assertTrue(book_b.has_wiki)


class TestSearchAllCompleteness(unittest.TestCase):
    """_search_all 必须覆盖图谱（含 PPR 的 _search_graph）与向量腿——
    回归点：历史上的内联图谱版没有 PPR，"全量搜索"反而弱于单项图谱搜索"""

    def test_all_legs_invoked(self):
        from core.agents.researcher import Researcher
        r = object.__new__(Researcher)
        calls = []
        r._search_wiki = lambda q, d: calls.append("wiki") or ""
        r._search_graph = lambda q, d: calls.append("graph") or "【知识图谱】岳飞"
        r._search_vector = lambda q: calls.append("vector") or "【向量】段落"
        r._search_communities = lambda q: calls.append("community") or ""
        r._search_dialogue = lambda q: calls.append("dialogue") or ""

        out = r._search_all("岳飞的事迹")
        self.assertEqual(calls, ["wiki", "graph", "vector", "community", "dialogue"])
        self.assertIn("【知识图谱】", out)
        self.assertIn("【向量】", out)

    def test_miss_placeholders_filtered(self):
        """各腿"未找到"占位文案不应出现在聚合结果里"""
        from core.agents.researcher import Researcher
        r = object.__new__(Researcher)
        r._search_wiki = lambda q, d: "Wiki 中未找到相关章节。"
        r._search_graph = lambda q, d: "知识图谱中未找到相关信息。"
        r._search_vector = lambda q: ""
        r._search_communities = lambda q: ""
        r._search_dialogue = lambda q: ""
        self.assertEqual(r._search_all("x"), "")


class TestIrrelevantFailureHandling(unittest.TestCase):
    """irrelevant 失败必须产出确定性补救步骤，不能落入无引导的 _refine_plan"""

    def test_irrelevant_generates_steps_without_llm(self):
        from core.agents.coordinator import Coordinator
        c = object.__new__(Coordinator)
        review = {"failure_type": "irrelevant", "feedback": "答非所问"}
        with patch("core.agents.coordinator.call_llm") as m:
            new_steps = c._handle_review_failure(
                [{"step": 1, "description": "初次检索"}], review, "赵玖是谁？")
        self.assertFalse(m.called, "irrelevant 不应触发 _refine_plan 的 LLM 兜底")
        descs = [s["description"] for s in new_steps]
        self.assertTrue(any("全量综合检索" in d and "赵玖是谁？" in d for d in descs))
        self.assertTrue(any("扣题重写" in d for d in descs))


class TestWriterStreaming(unittest.TestCase):
    """Writer 流式生成：stream_cb 逐 token 回调，返回值仍是完整校验后文本"""

    def test_stream_cb_receives_tokens_and_full_text_returned(self):
        from core.agents.writer import Writer
        tokens = []

        def fake_stream(messages, **kw):
            yield from ["赵玖", "与岳飞", "是君臣关系"]

        with patch("core.agents.writer.call_llm_stream", side_effect=fake_stream), \
             patch("core.agents.writer._resolve_provider", return_value=("k", None, "m")):
            report = Writer().write(
                "赵玖和岳飞的关系", "relationship",
                [{"step": 1, "description": "检索", "result": "无章节标题的材料"}],
                stream_cb=tokens.append)

        self.assertEqual(tokens, ["赵玖", "与岳飞", "是君臣关系"])
        self.assertEqual(report, "赵玖与岳飞是君臣关系")

    def test_no_key_falls_back_to_call_llm(self):
        """未配置 key 时不走流式（call_llm_stream 会 yield 回退文案污染报告）"""
        from core.agents.writer import Writer
        with patch("core.agents.writer._resolve_provider", return_value=("", None, "m")), \
             patch("core.agents.writer.call_llm", return_value="普通报告") as m_llm, \
             patch("core.agents.writer.call_llm_stream") as m_stream:
            report = Writer().write("q", "other",
                                    [{"step": 1, "description": "d", "result": "x"}],
                                    stream_cb=lambda t: None)
        self.assertTrue(m_llm.called)
        self.assertFalse(m_stream.called)
        self.assertEqual(report, "普通报告")

    def test_stream_failure_marker_returns_unavailable_message(self):
        """流式路径产出失败标记 → 与非流式 None 语义一致"""
        from core.agents.writer import Writer
        with patch("core.agents.writer.call_llm_stream",
                   side_effect=lambda m, **kw: iter(["[LLM 调用失败: boom"])), \
             patch("core.agents.writer._resolve_provider", return_value=("k", None, "m")):
            report = Writer().write("q", "other",
                                    [{"step": 1, "description": "d", "result": "x"}],
                                    stream_cb=lambda t: None)
        self.assertIn("暂时不可用", report)


class TestCoordinatorEvents(unittest.TestCase):
    """Coordinator event_cb：图节点推送 progress 事件；无 stream_cb 的替身不报错"""

    def _make_coordinator(self):
        from core.agents.coordinator import Coordinator

        class FakeResearcher:
            def execute(self, desc, query, intent):
                return "材料"

        class FakeWriter:
            def write(self, query, intent, materials):  # 无 stream_cb 参数
                return "最终报告"

        class FakeReviewer:
            def review(self, draft, query, research_materials=""):
                return {"passed": True, "score": 9, "feedback": "", "failure_type": ""}

        return Coordinator(FakeResearcher(), FakeWriter(), FakeReviewer())

    def test_events_emitted(self):
        events = []
        coordinator = self._make_coordinator()
        steps = [{"step": 1, "description": "搜索赵玖的信息"}]
        result = coordinator.run("赵玖是怎样的人", intent="character",
                                 steps=steps, event_cb=events.append)
        kinds = [e["event"] for e in events]
        self.assertIn("progress", kinds)
        self.assertEqual(result["final_report"], "最终报告")
        # 替身 Writer 无 stream_cb → 不应推送 token/draft_start 事件
        self.assertNotIn("token", kinds)
        self.assertNotIn("draft_start", kinds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
