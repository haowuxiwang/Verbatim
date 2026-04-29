from __future__ import annotations

import unittest

from core.pdf_parser import _looks_like_path_noise_line


class TestPdfParserNoiseFilter(unittest.TestCase):
    def test_detects_larkshell_cached_image_path_line(self):
        line = (
            "/c/Users/WuSiTan/AppData/Roaming/LarkShell/sdk_storage/"
            "cb7a99da7821e034d4d9e8c0d8971a0a/resources/images/"
            "img_v3_0210i_63f1ddb3-265f-4962-a24a-1f79db64572g.jpg"
        )
        self.assertTrue(_looks_like_path_noise_line(line))

    def test_detects_http_image_url_line(self):
        self.assertTrue(_looks_like_path_noise_line("https://example.com/resources/images/demo.png"))

    def test_keeps_normal_business_text(self):
        self.assertFalse(_looks_like_path_noise_line("药品名称：阿司匹林肠溶片"))


if __name__ == "__main__":
    unittest.main()
