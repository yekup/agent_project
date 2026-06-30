"""
核心模块单元测试
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
