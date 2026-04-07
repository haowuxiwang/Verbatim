from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalized_similarity(left_text: str, right_text: str) -> float:
    def _norm(s: str) -> str:
        t = (s or "").lower()
        t = re.sub(r"\s+", "", t)
        t = re.sub(r"[，,。．\.；;：:!！\?？、（）()【】\[\]“”\"'‘’\-—_]", "", t)
        return t

    ln = _norm(left_text)
    rn = _norm(right_text)
    if not ln and not rn:
        return 1.0
    return SequenceMatcher(None, ln, rn).ratio()


def garble_signal_score(text: str) -> tuple[int, list[str]]:
    t = text or ""
    reasons: list[str] = []
    score = 0

    if len(t) >= 80:
        bridge = re.findall(r"(?<=[\u4e00-\u9fff])[A-Za-z0-9]{1,2}(?=[\u4e00-\u9fff])", t)
        if len(bridge) >= 4:
            score += 2
            reasons.append(f"中英文/数字碎片插入过多({len(bridge)})")

        short_ascii = re.findall(r"[A-Za-z0-9]{1,2}", t)
        if short_ascii:
            ratio = len(short_ascii) / max(1, len(t))
            if ratio >= 0.08:
                score += 1
                reasons.append(f"短ASCII碎片占比偏高({ratio:.1%})")

    if "\ufffd" in t:
        score += 3
        reasons.append("存在不可解码字符")

    return score, reasons


def check_text_quality(text: str) -> dict:
    text = text or ""
    stripped = text.strip()
    char_count = len(stripped)

    severe_issues: list[str] = []
    warning_issues: list[str] = []

    if char_count < 10:
        severe_issues.append("提取文本过少，疑似无文本层或选区无效。")

    punct_chars = "，。！？、；：‘’“”（）【】<>.,!?;:'\"()"
    punct_count = sum(1 for c in text if c in punct_chars)
    punct_ratio = punct_count / len(text) if text else 0.0

    # For short/region-level snippets, punctuation can naturally be very sparse.
    # Keep this as warning unless the text is long enough to be statistically meaningful.
    if punct_ratio < 0.003 and char_count > 120:
        severe_issues.append(f"标点密度极低({punct_ratio:.1%})，文本层可信度不足。")
    elif punct_ratio < 0.01 and char_count > 40:
        warning_issues.append(f"标点密度偏低({punct_ratio:.1%})，可能存在提取噪声。")

    abnormal_patterns = [r"[a-zA-Z]{20,}", r"\d{15,}"]
    for pattern in abnormal_patterns:
        if re.search(pattern, text):
            warning_issues.append("检测到异常长连续字符，可能存在乱码或错序。")
            break

    cjk_count = sum(1 for c in stripped if "\u4e00" <= c <= "\u9fff")
    latin_count = sum(1 for c in stripped if ("a" <= c.lower() <= "z"))
    digit_count = sum(1 for c in stripped if c.isdigit())
    denom = max(1, char_count)
    cjk_ratio = cjk_count / denom
    if char_count > 40 and cjk_ratio < 0.20:
        warning_issues.append(f"中文字符占比偏低({cjk_ratio:.1%})，文本层可能错位。")

    replacement_count = text.count("\ufffd")
    if replacement_count > 0:
        severe_issues.append("检测到不可解码字符，文本层已损坏。")

    garble_score, garble_reasons = garble_signal_score(text)
    if garble_score >= 3:
        severe_issues.append(f"乱码信号强({garble_score})：{'；'.join(garble_reasons)}")
    elif garble_score >= 2:
        warning_issues.append(f"乱码信号偏高({garble_score})：{'；'.join(garble_reasons)}")

    quality = "good"
    if severe_issues:
        quality = "bad"
    elif warning_issues:
        quality = "warning"

    confidence = max(
        0,
        min(
            100,
            100 - len(severe_issues) * 35 - len(warning_issues) * 12 - garble_score * 10,
        ),
    )

    return {
        "quality": quality,
        "issues": [*severe_issues, *warning_issues],
        "severe_issues": severe_issues,
        "warning_issues": warning_issues,
        "punct_ratio": punct_ratio,
        "char_count": char_count,
        "cjk_ratio": cjk_ratio,
        "latin_count": latin_count,
        "digit_count": digit_count,
        "confidence": confidence,
    }


def should_try_ocr_side(text: str, quality: dict) -> tuple[bool, str]:
    q = str(quality.get("quality", "good"))
    if q in {"bad", "warning"}:
        return True, f"质量等级={q}"

    score, reasons = garble_signal_score(text)
    if score >= 2:
        return True, "；".join(reasons)
    return False, "质量正常且无明显乱码信号"


def is_weak_confusable_pair(left_seg: str, right_seg: str) -> bool:
    l = (left_seg or "").strip().lower()
    r = (right_seg or "").strip().lower()
    if not l or not r:
        return False
    if l == r:
        return False
    if max(len(l), len(r)) > 3:
        return False

    pairs = {
        ("0", "o"),
        ("o", "0"),
        ("1", "l"),
        ("l", "1"),
        ("1", "i"),
        ("i", "1"),
        ("5", "s"),
        ("s", "5"),
        ("2", "z"),
        ("z", "2"),
        ("8", "b"),
        ("b", "8"),
        ("c", "g"),
        ("g", "c"),
        ("u", "v"),
        ("v", "u"),
    }
    if len(l) == 1 and len(r) == 1 and (l, r) in pairs:
        return True

    if len(l) == len(r):
        weak = 0
        for a, b in zip(l, r):
            if a == b:
                continue
            if (a, b) in pairs:
                weak += 1
            else:
                return False
        return weak > 0
    return False
