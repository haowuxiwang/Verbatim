# P0/P1 执行记录（2026-03-04）

## 本轮目标
- P0: 提升打包前质量门禁与关键回归稳定性
- P1: 提升机器预对齐在极端样本（17页 vs 2页）下的可用性

## 已完成

1. 预对齐候选检索增强（P1）
- 文件: `core/services/prealign.py`
- 改动:
  - `retrieve_page_candidates` 增加位置先验排序（page-order prior）
  - 增加候选多样性兜底：在低分场景尽量保留不同右页候选
- 价值:
  - 避免极端场景下候选过度集中到单一右页，给用户更多可验证窗口

2. 预对齐测试补齐（P0/P1）
- 文件: `tests/test_prealign_service.py`
- 新增用例:
  - `test_retrieve_page_candidates_keeps_diversity_when_scores_are_low`
  - `test_retrieve_page_candidates_uses_position_prior_for_ordering`

3. 打包前告警清理（P1）
- 文件: `core/diff_engine.py`, `core/pdf_parser.py`
- 改动:
  - 模块 docstring 改为 raw string，消除无效转义告警
  - `_remove_punctuation` 正则模式修正，清理 `DeprecationWarning`

## 验证结果（全部在 2 分钟门限内）

1. 定向测试
- `python -m pytest tests/test_prealign_service.py -q`
  - 结果: `7 passed in 1.13s`

2. 全量回归
- `python -m pytest -q`
  - 结果: `76 passed in 2.47s`

3. 覆盖率
- `python -m pytest --cov=core --cov=app --cov-report=term -q`
  - 结果: `TOTAL 52%`
  - 结论: 尚未达到工业化目标 `80%+`

4. 真实样本回放（`original.pdf` vs `digest.pdf`）
- 结果: 每个左页可返回两个候选（含扫描页兜底候选），候选解释性增强
- 结论: 可用性提升，但 Top-1 仍高度集中于右页 2，说明“可用但仍不稳”

## 未完成/残余风险

1. P0 未完成: 覆盖率门禁未达标（当前 52%）
2. P0 未完成: GUI 主流程自动化覆盖仍偏低（`app/main_window.py` 33%）
3. P1 风险: 17 vs 2 极端场景仍需更强锚点/结构先验，Top-1 稳定性不足

