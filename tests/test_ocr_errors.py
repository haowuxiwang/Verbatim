import unittest

from core.services.ocr_errors import classify_ocr_error


class TestOcrErrorClassification(unittest.TestCase):
    def test_timeout(self):
        info = classify_ocr_error(TimeoutError("OCR timed out"))
        self.assertEqual(info.code, "timeout")

    def test_auth(self):
        info = classify_ocr_error("OCR HTTP 401: invalid token")
        self.assertEqual(info.code, "auth")

    def test_model(self):
        info = classify_ocr_error("offline runtime missing OCR models: det=None, rec=None")
        self.assertEqual(info.code, "model")

    def test_worker(self):
        info = classify_ocr_error("Local OCR worker bootstrap failed. Set VERBATIM_WORKER_PYTHON")
        self.assertEqual(info.code, "worker")

    def test_runtime(self):
        info = classify_ocr_error("offline runtime dir not configured; set VERBATIM_OCR_RUNTIME_DIR")
        self.assertEqual(info.code, "runtime")

    def test_network(self):
        info = classify_ocr_error("OCR request failed: [Errno 11001] getaddrinfo failed")
        self.assertEqual(info.code, "network")

    def test_empty_result(self):
        info = classify_ocr_error("空文件")
        self.assertEqual(info.code, "empty_result")

    def test_unknown(self):
        info = classify_ocr_error("unexpected ocr failure")
        self.assertEqual(info.code, "unknown")


if __name__ == "__main__":
    unittest.main()
