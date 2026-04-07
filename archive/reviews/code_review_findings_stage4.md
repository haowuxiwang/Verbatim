# Stage 4 Code Review Findings

## P0 Findings (High)

1. 缺 Token 强提醒逻辑不可达（高风险）
- 位置：
  - [main.py:2245](/D:/learn/codex/Verbatim_dev/main.py:2245)
  - [main.py:2256](/D:/learn/codex/Verbatim_dev/main.py:2256)
- 问题：
  - `use_ocr = bool(self._auto_ocr_enabled)` 在无 token 时为 `False`；
  - 强提醒条件是 `if use_ocr and ocr_recommended and self._ocr_cfg is None:`，因此无 token 时分支不会进入。
- 影响：
  - 用户在最需要提醒（低质量文本 + 未配置 token）时看不到强提醒，直接进入低精度比对，噪音飙升且缺少明确告知。
- 建议：
  - 将条件改为基于 `ocr_recommended` 和 `self._ocr_cfg is None`，不要依赖 `use_ocr`。

2. OCR 文本抽取可能重复计入父子节点文本（高风险）
- 位置：
  - [ocr_client.py:484](/D:/learn/codex/Verbatim_dev/core/ocr_client.py:484)
  - [ocr_client.py:502](/D:/learn/codex/Verbatim_dev/core/ocr_client.py:502)
  - [ocr_client.py:505](/D:/learn/codex/Verbatim_dev/core/ocr_client.py:505)
- 问题：
  - `_walk_positioned_text_values` 在命中当前 dict 的 `text+bbox` 后仍递归全部子节点；
  - 若返回结构包含“行节点文本 + 词节点文本”，会重复拼接。
- 影响：
  - 结果文本膨胀、重复段增加，直接放大差异噪音并造成错判。
- 建议：
  - 命中“可用文本层级”后按层级策略停止下钻，或引入 node-type 白名单，避免父子重复采集。

## P1 Findings (Medium)

3. 格式 badge 过滤条件与实际 badge 文本不一致
- 位置：
  - [main.py:216](/D:/learn/codex/Verbatim_dev/main.py:216)
  - [main.py:2844](/D:/learn/codex/Verbatim_dev/main.py:2844)
- 问题：
  - 绘制时隐藏条件判断 `badge_text == "格式"`；
  - 生成 badge 时实际使用 `"格"`。
- 影响：
  - “隐藏格式差异”时格式 badge 仍可能显示，造成 UI 表达不一致。
- 建议：
  - 统一 badge 语义（建议改为基于 `diff_type` 过滤，不依赖文案字符串）。

4. OCR 变体评分对“基线相似度”权重偏高，可能偏向噪音文本
- 位置：
  - [main.py:1900](/D:/learn/codex/Verbatim_dev/main.py:1900)
  - [main.py:1902](/D:/learn/codex/Verbatim_dev/main.py:1902)
  - [main.py:1903](/D:/learn/codex/Verbatim_dev/main.py:1903)
- 问题：
  - `sim_bonus` 直接奖励“与 baseline_text 相似”；
  - baseline 本身可能是低质量文本层（噪音来源），导致“像噪音”的 OCR 变体得分更高。
- 影响：
  - OCR 已触发但选出的“best”不一定更接近真实内容，抑制降噪目标达成。
- 建议：
  - 降低或移除 baseline 相似度奖励；
  - 引入更稳定的内部质量特征（异常字符密度、结构完整性、语言模型约束等）。

5. 文本质量置信度对某些严重乱码场景不敏感
- 位置：
  - [main.py:1984](/D:/learn/codex/Verbatim_dev/main.py:1984)
  - [main.py:2021](/D:/learn/codex/Verbatim_dev/main.py:2021)
- 问题：
  - 置信度主要按规则项数量扣分，若乱码没触发规则，可能仍给出高置信（如日志中的 `conf=100`）。
- 影响：
  - 错误的高置信会影响 OCR 决策、变体评分和用户认知，造成“明明很差却显示高质量”。
- 建议：
  - 将 `garble_signal_score` 直接并入置信度；
  - 对中英文碎片、非法 token 形态增加更强惩罚并纳入质量等级。

## 覆盖性缺口

1. 缺少“无 token + OCR 推荐场景必须弹强提醒”的自动化测试
- 建议增加 GUI 逻辑测试或可测试函数拆分。

2. 缺少“父子层级文本重复采集”的 OCR 解析回归用例
- 建议增加嵌套 response 样本，校验不会重复拼接。

