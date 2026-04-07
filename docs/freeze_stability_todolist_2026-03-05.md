# 死机风险排查与修复 ToDo（2026-03-05）

目标：优先消除“连续比对后卡顿/死机”风险，再评估是否可对外分发。

## P0 根因与修复

- [x] 复核日志并确认高风险链路  
  - 结论：本地 OCR 走线程超时后，底层推理任务可能继续运行，重复触发会累积僵死线程。
- [x] 实施本地 OCR 隔离执行  
  - 将本地 OCR 改为子进程执行，超时由 `subprocess` 终止，避免线程残留。
  - 新增：`core/services/local_ocr_worker.py`
  - 变更：`core/services/ocr_engines.py`
- [x] 保留兼容回退路径  
  - 可通过 `VERBATIM_LOCAL_OCR_ISOLATE=0` 退回旧路径（仅调试用，不建议生产）。

## P0 自动化验证（已执行）

- [x] OCR 与 GUI关键回归  
  - `python -m pytest tests/test_ocr_engines.py tests/test_ocr_orchestrator.py tests/test_zoom_gui.py -q`  
  - 结果：`60 passed`
- [x] 非GUI核心回归  
  - `python -m pytest tests/test_diff_engine.py tests/test_diff_regions.py tests/test_field_mapper.py tests/test_format_diff.py tests/test_layout_analyzer.py tests/test_region_extractor.py tests/test_region_manager.py tests/test_text_quality_service.py tests/test_ocr_client.py tests/test_compare_orchestrator.py tests/test_compare_history.py tests/test_field_orchestrator.py tests/test_observability.py tests/test_pipeline_integration.py tests/test_same_text_bug.py tests/test_auto_match.py tests/test_prealign_service.py tests/test_performance.py -q`  
  - 结果：`104 passed`
- [x] 覆盖率统计  
  - `python -m pytest tests -q --cov=app --cov=core --cov-report=term`  
  - 结果：`164 passed`，`TOTAL 70%`

## P1 发布前必做

- [ ] 为 `local_ocr_worker.py` 增加直接单测（当前该文件覆盖率 0%）
- [ ] 增加“连续多次比对（>=30次）”稳定性压力脚本与阈值告警
- [ ] 增加 OCR 子进程失败分类与熔断策略（连续失败降级禁用本地OCR）
- [ ] 增加打包后（PyInstaller）端到端冒烟与资源占用基线（CPU/内存/句柄）
