# 灰度门禁指标（草案）

## 日级指标
- 误报率（FP）
- 漏报率（FN）
- REVIEW 占比（弃权率）
- OCR 失败分类占比（timeout/auth/network/model/worker/empty_result）

## 触发阈值建议
- FP > 5% 或 FN > 10%：停止灰度，进入回归排查
- REVIEW > 15%：降低自动比对权重，要求人工复核
- OCR timeout 占比持续上升：检查超时门槛与资源占用

## 固定回归样本
- 原始 17 页 vs 摘要 2 页（original.pdf / digest.pdf）
- 低质量缩印件 1 份
- 混排复杂版式 1 份
