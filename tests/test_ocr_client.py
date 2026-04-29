import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import request

from core.ocr_client import OcrConfig, PaddleOcrClient, _dpapi_is_available


class TestOcrClientHelpers(unittest.TestCase):
    def test_config_from_env_requires_token(self):
        old = os.environ.pop("VERBATIM_OCR_TOKEN", None)
        try:
            cfg = OcrConfig.from_env()
            self.assertIsNone(cfg)
        finally:
            if old is not None:
                os.environ["VERBATIM_OCR_TOKEN"] = old

    def test_config_from_env_marks_storage_mode(self):
        with mock.patch.dict("os.environ", {"VERBATIM_OCR_TOKEN": "t1"}, clear=False):
            cfg = OcrConfig.from_env()
        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(cfg.token_storage, "env")

    def test_extract_text_from_nested_response(self):
        sample = {
            "data": {
                "items": [
                    {"text": "line1"},
                    {"rec_text": "line2"},
                    {"other": {"ocr_text": "line3"}},
                ]
            }
        }
        text = PaddleOcrClient._extract_text(sample)
        self.assertIn("line1", text)
        self.assertIn("line2", text)
        self.assertIn("line3", text)

    def test_extract_text_keeps_duplicate_lines(self):
        sample = {
            "data": {
                "items": [
                    {"text": "甲方"},
                    {"text": "乙方"},
                    {"text": "甲方"},
                ]
            }
        }
        text = PaddleOcrClient._extract_text(sample)
        self.assertEqual(text.splitlines(), ["甲方", "乙方", "甲方"])

    def test_extract_text_prefers_primary_keys_over_label_value(self):
        sample = {
            "result": [
                {"label": "姓名", "value": "张三"},
                {"text": "合同编号 ABC123"},
                {"rec_text": "签署日期 2026-03-03"},
            ]
        }
        text = PaddleOcrClient._extract_text(sample)
        self.assertIn("合同编号 ABC123", text)
        self.assertIn("签署日期 2026-03-03", text)
        self.assertNotIn("姓名", text)
        self.assertNotIn("张三", text)

    def test_extract_text_uses_bbox_order_for_reading_sequence(self):
        sample = {
            "data": [
                {"text": "第二行", "bbox": [10, 40, 80, 58]},
                {"text": "第一行", "bbox": [10, 10, 80, 28]},
            ]
        }
        text = PaddleOcrClient._extract_text(sample)
        self.assertEqual(text.splitlines(), ["第一行", "第二行"])

    def test_extract_text_avoids_parent_child_duplicate_collection(self):
        sample = {
            "data": [
                {
                    "text": "第一行",
                    "bbox": [10, 10, 80, 28],
                    "words": [
                        {"text": "第一", "bbox": [10, 10, 40, 28]},
                        {"text": "行", "bbox": [42, 10, 52, 28]},
                    ],
                },
                {
                    "text": "第二行",
                    "bbox": [10, 40, 80, 58],
                    "words": [
                        {"text": "第二", "bbox": [10, 40, 40, 58]},
                        {"text": "行", "bbox": [42, 40, 52, 58]},
                    ],
                },
            ]
        }
        text = PaddleOcrClient._extract_text(sample)
        self.assertEqual(text.splitlines(), ["第一行", "第二行"])

    def test_config_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ocr.json"
            cfg = OcrConfig(
                token="t1",
                sync_url="http://sync",
                job_url="http://job",
                model="m1",
                retry_count=3,
                retry_backoff_sec=1.5,
                proxy_url="http://127.0.0.1:7890",
                source="file",
            )
            cfg.save_to_file(path)
            loaded = OcrConfig.from_file(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.token, "t1")
            self.assertEqual(loaded.sync_url, "http://sync")
            self.assertEqual(loaded.retry_count, 3)
            self.assertAlmostEqual(loaded.retry_backoff_sec, 1.5)
            self.assertEqual(loaded.proxy_url, "http://127.0.0.1:7890")
            self.assertEqual(loaded.source, "file")
            expected_storage = "dpapi" if '"token_enc"' in path.read_text(encoding="utf-8") else "plain"
            self.assertEqual(loaded.token_storage, expected_storage)

    @unittest.skipUnless(os.name == "nt", "DPAPI encryption is Windows-only")
    def test_config_file_uses_encrypted_token_on_windows(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ocr.json"
            cfg = OcrConfig(token="sensitive-token", source="file")
            cfg.save_to_file(path)
            raw = path.read_text(encoding="utf-8")
            if _dpapi_is_available():
                self.assertIn('"token_enc"', raw)
                self.assertNotIn('"token": "sensitive-token"', raw)
            else:
                self.assertIn('"token_storage": "plain"', raw)
                self.assertIn('"token": "sensitive-token"', raw)

    @unittest.skipUnless(os.name == "nt", "DPAPI encryption migration is Windows-only")
    def test_from_file_migrates_plain_token_config_on_windows(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ocr.json"
            path.write_text(
                '{"token":"legacy","sync_url":"http://sync","job_url":"http://job","model":"m"}',
                encoding="utf-8",
            )
            loaded = OcrConfig.from_file(path)
            self.assertIsNotNone(loaded)
            raw = path.read_text(encoding="utf-8")
            if _dpapi_is_available():
                self.assertIn('"token_enc"', raw)
            else:
                self.assertIn('"token_storage": "plain"', raw)
                self.assertIn('"token": "legacy"', raw)

    def test_network_retry_for_timeout(self):
        client = PaddleOcrClient(OcrConfig(token="t1", retry_count=2, retry_backoff_sec=0.1))
        req = request.Request("https://example.com", method="GET")
        calls: list[bool] = []

        def fake_open(_req, verify_ssl):
            calls.append(verify_ssl)
            if len(calls) == 1:
                raise TimeoutError("timed out")
            return b'{"ok": true}'

        with mock.patch.object(client, "_open_request", side_effect=fake_open):
            out = client._urlopen_with_fallback(req)
            self.assertEqual(out, b'{"ok": true}')
            self.assertEqual(calls, [True, True])

    def test_ssl_error_can_fallback_to_insecure(self):
        client = PaddleOcrClient(OcrConfig(token="t1", retry_count=1, insecure_fallback=True))
        req = request.Request("https://example.com", method="GET")
        calls: list[bool] = []

        def fake_open(_req, verify_ssl):
            calls.append(verify_ssl)
            if verify_ssl:
                raise ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")
            return b'{"ok": true}'

        with mock.patch.object(client, "_open_request", side_effect=fake_open):
            out = client._urlopen_with_fallback(req)
            self.assertEqual(out, b'{"ok": true}')
            self.assertEqual(calls, [True, False])

    def test_recognize_sync_tries_json_when_multipart_fails(self):
        client = PaddleOcrClient(OcrConfig(token="t1"))
        calls = {"count": 0}

        def fake_post(_url, body, headers):
            calls["count"] += 1
            if calls["count"] == 1:
                self.assertIn("multipart/form-data", headers.get("Content-Type", ""))
                raise RuntimeError("Remote end closed connection without response")
            self.assertEqual(headers.get("Content-Type"), "application/json")
            self.assertIn(b"image", body)
            return {"data": [{"text": "ok"}]}

        with mock.patch.object(client, "_post_json", side_effect=fake_post):
            result = client.recognize_sync(b"fake-image", filename="region.png")
            self.assertEqual(result.text, "ok")
            self.assertEqual(result.source, "sync")

    def test_extract_text_ignores_path_like_aux_values(self):
        sample = {
            "result": [
                {
                    "value": "/c/Users/WuSiTan/AppData/Roaming/LarkShell/sdk_storage/abc/resources/images/img_v3_01_abcd.jpg"
                }
            ]
        }
        text = PaddleOcrClient._extract_text(sample)
        self.assertEqual(text, "")

    def test_extract_text_ignores_path_like_primary_text_values(self):
        sample = {
            "data": [
                {
                    "text": "/c/Users/WuSiTan/AppData/Roaming/LarkShell/sdk_storage/abc/resources/images/img_v3_01_abcd.jpg"
                }
            ]
        }
        text = PaddleOcrClient._extract_text(sample)
        self.assertEqual(text, "")

    def test_extract_text_ignores_url_like_aux_values(self):
        sample = {"result": [{"label": "preview", "value": "https://example.com/resources/images/demo.png"}]}
        text = PaddleOcrClient._extract_text(sample)
        self.assertEqual(text, "")

    def test_submit_async_extracts_job_id(self):
        client = PaddleOcrClient(OcrConfig(token="t1"))
        with mock.patch.object(client, "_post_json", return_value={"data": {"job_id": "job-1"}}):
            jid = client.submit_async(b"img")
        self.assertEqual("job-1", jid)

    def test_submit_async_raises_when_no_job_id(self):
        client = PaddleOcrClient(OcrConfig(token="t1"))
        with mock.patch.object(client, "_post_json", return_value={"ok": True}):
            with self.assertRaises(RuntimeError):
                client.submit_async(b"img")

    def test_poll_async_result_success(self):
        client = PaddleOcrClient(OcrConfig(token="t1", poll_timeout_sec=1, poll_interval_sec=0.01))
        seq = [
            {"status": "running"},
            {"status": "succeeded", "data": [{"text": "done"}]},
        ]
        with mock.patch.object(client, "_get_json", side_effect=seq):
            with mock.patch("core.ocr_client.time.sleep", return_value=None):
                out = client.poll_async_result("jid")
        self.assertEqual("done", out.text)
        self.assertEqual("async", out.source)

    def test_poll_async_result_failed(self):
        client = PaddleOcrClient(OcrConfig(token="t1", poll_timeout_sec=1, poll_interval_sec=0.01))
        with mock.patch.object(client, "_get_json", return_value={"status": "failed"}):
            with self.assertRaises(RuntimeError):
                client.poll_async_result("jid")

    def test_poll_async_result_timeout(self):
        client = PaddleOcrClient(OcrConfig(token="t1", poll_timeout_sec=0, poll_interval_sec=0.01))
        with mock.patch.object(client, "_get_json", return_value={"status": "running"}):
            with self.assertRaises(TimeoutError):
                client.poll_async_result("jid")

    def test_safe_json_loads_and_find_first_value(self):
        obj = PaddleOcrClient._safe_json_loads(b'["a", "b"]')
        self.assertIn("data", obj)
        bad = PaddleOcrClient._safe_json_loads(b"{not-json")
        self.assertIn("raw", bad)
        v = PaddleOcrClient._find_first_value({"a": {"task_id": "x1"}}, {"task_id"})
        self.assertEqual("x1", v)

    def test_bbox_from_any_variants(self):
        bb1 = PaddleOcrClient._bbox_from_any({"left": 1, "top": 2, "right": 5, "bottom": 6})
        self.assertEqual((1.0, 2.0, 5.0, 6.0), bb1)
        bb2 = PaddleOcrClient._bbox_from_any([1, 2, 5, 6])
        self.assertEqual((1.0, 2.0, 5.0, 6.0), bb2)
        bb3 = PaddleOcrClient._bbox_from_any([[1, 2], [5, 2], [5, 6], [1, 6]])
        self.assertEqual((1.0, 2.0, 5.0, 6.0), bb3)
        self.assertIsNone(PaddleOcrClient._bbox_from_any("bad"))

    def test_looks_like_non_text_value(self):
        self.assertTrue(PaddleOcrClient._looks_like_non_text_value("/sdk_storage/a/resources/images/x.jpg"))
        self.assertTrue(PaddleOcrClient._looks_like_non_text_value("https://example.com/a.png"))
        self.assertFalse(PaddleOcrClient._looks_like_non_text_value("药品说明书"))


if __name__ == "__main__":
    unittest.main()
