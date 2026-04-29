from __future__ import annotations

import unittest

from core.services.ocr_errors import classify_ocr_error


class TestOcrErrors(unittest.TestCase):
    def test_classify_numpy_abi_mismatch(self):
        info = classify_ocr_error("ImportError: numpy.core.multiarray failed to import")
        self.assertEqual("numpy_abi_mismatch", info.code)

    def test_classify_module_missing(self):
        info = classify_ocr_error("ModuleNotFoundError: No module named 'paddleocr'")
        self.assertEqual("module_missing", info.code)


if __name__ == "__main__":
    unittest.main()
