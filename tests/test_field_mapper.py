import unittest

from core.field_mapper import (
    compare_by_fields,
    extract_key_values,
    normalize_field_name,
    should_enable_field_mapping,
)


class TestFieldMapper(unittest.TestCase):
    def test_normalize_field_name_synonym(self):
        self.assertEqual(normalize_field_name("地址:"), "生产地址")
        self.assertEqual(normalize_field_name("生产地址："), "生产地址")

    def test_extract_key_values_inline_and_section(self):
        text = "【药品名称】 艾曲泊帕乙醇胺片\n生产地址：浙江省台州市\n传真:\n0576-88827887"
        kvs = extract_key_values(text)
        by_key = {k.canonical_key: k.value for k in kvs}
        self.assertEqual(by_key.get("产品名称"), "艾曲泊帕乙醇胺片")
        self.assertEqual(by_key.get("生产地址"), "浙江省台州市")
        self.assertEqual(by_key.get("传真"), "0576-88827887")

    def test_compare_by_fields_synonym_match(self):
        left = "地址：浙江省台州市椒江区外沙路46号"
        right = "生产地址：浙江省台州市椒江区外沙路46号"
        diffs = compare_by_fields(left, right)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].field_name, "生产地址")
        self.assertEqual(diffs[0].diff_type, "match")

    def test_noisy_compressed_field_block_should_not_create_ttp_key(self):
        left = (
            "【生产企业】 名称：浙江海正药业股份有限公司 地址：浙江省台州市椒江区外沙路46号 "
            "邮政编码：318000 电话号码：4001180618 传真号码：0576-88827887 网址：http://www.hisunpharm.com/"
        )
        right = (
            "【生产企业】企业名称浙江海正药业股份有限公司生产地址浙江省台州市椒江区外沙路46号"
            "邮政编码318000 电话号码4001180618 传真号码0576-88827887 网址wwwhisunpharm com"
        )
        left_kvs = extract_key_values(left)
        right_kvs = extract_key_values(right)
        all_keys = {k.canonical_key for k in left_kvs} | {k.canonical_key for k in right_kvs}
        self.assertNotIn("ttp", all_keys)
        self.assertIn("传真", all_keys)
        self.assertIn("电话", all_keys)

    def test_container_field_should_be_suppressed_when_structured_fields_exist(self):
        left = (
            "【生产企业】 名称：浙江海正药业股份有限公司 地址：浙江省台州市椒江区外沙路46号 "
            "邮政编码：318000 电话号码：4001180618 传真号码：0576-88827887"
        )
        right = (
            "【生产企业】企业名称浙江海正药业股份有限公司生产地址浙江省台州市椒江区外沙路46号"
            "邮政编码318000 电话号码4001180618 传真号码0576-88827887"
        )
        diffs = compare_by_fields(left, right)
        keys = {d.field_name for d in diffs}
        self.assertNotIn("生产企业", keys)
        self.assertIn("企业名称", keys)
        self.assertIn("生产地址", keys)
        self.assertIn("电话", keys)

    def test_url_field_tolerates_compressed_punctuation_noise(self):
        left = "网址：http://www.hisunpharm.com/"
        right = "网址wwwhisunpharmcom"
        diffs = compare_by_fields(left, right)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].field_name, "网址")
        self.assertEqual(diffs[0].diff_type, "match")

    def test_field_mapping_should_skip_for_narrative_paragraph(self):
        left = "在小鼠和大鼠14天试验中，在与致病和死亡率相关的暴露量时观察到肾小管毒性。"
        right = "在小鼠和大鼠14天试验中，在与致病和死亡率相关的县露量时观察到肾小管毒性。"
        enable, _ = should_enable_field_mapping(left, right)
        self.assertFalse(enable)

    def test_merged_phone_value_should_still_match(self):
        left = "电话号码：4001180618"
        right = "电话号码4001180618传真号码057688827887"
        diffs = compare_by_fields(left, right)
        phone_diff = next((d for d in diffs if d.field_name == "电话"), None)
        self.assertIsNotNone(phone_diff)
        self.assertEqual(phone_diff.diff_type, "match")


if __name__ == "__main__":
    unittest.main()
