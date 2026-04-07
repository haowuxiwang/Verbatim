# Verbatim 稳定性测试 ToDo（2026-03-05）

目标：先完成“低风险自动化回归”，避免 GUI/长时任务导致机器再次卡死，再交给人工做命令行复测。

## P0 自动化（先执行）

- [x] 执行核心模块单测（无 GUI）
  - `python -m pytest tests/test_diff_engine.py tests/test_diff_regions.py tests/test_field_mapper.py tests/test_format_diff.py tests/test_layout_analyzer.py tests/test_region_extractor.py tests/test_region_manager.py tests/test_text_quality_service.py -q`
- [x] 执行 OCR/编排相关单测（无真实外网依赖）
  - `python -m pytest tests/test_ocr_client.py tests/test_ocr_engines.py tests/test_ocr_orchestrator.py tests/test_compare_orchestrator.py tests/test_compare_history.py tests/test_field_orchestrator.py tests/test_observability.py tests/test_pipeline_integration.py -q`
- [x] 执行已知回归用例
  - `python -m pytest tests/test_same_text_bug.py tests/test_auto_match.py tests/test_prealign_service.py -q`
- [x] 执行性能基线（轻量）
  - `python -m pytest tests/test_performance.py -q`

## P1 人工命令行复测（你后续执行）

- [ ] 启动前限制 OCR 阻塞时间
  - PowerShell: `$env:VERBATIM_BG_TASK_TIMEOUT_MS='12000'`
  - PowerShell: `$env:VERBATIM_OCR_BUDGET_SEC='15'`
- [ ] 启动程序并做“连续两次同区域比对”
  - `python main.py`
- [ ] 复测后采集日志末尾
  - `Get-Content logs/verbatim_app.log -Tail 120`

## 通过标准

- 自动化用例全部通过，或失败点可解释且不涉及卡死路径。
- 人工复测可连续完成两次比对，无整机卡死、无长时间无响应。
