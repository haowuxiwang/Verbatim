# 工业级差距执行清单（详细版，2026-03-05）

## A. 稳定性防护（死机风险）

- [x] A1. 本地 OCR 改为子进程隔离执行（超时可强制终止）
  - 代码：`core/services/ocr_engines.py`
  - 新增：`core/services/local_ocr_worker.py`
- [x] A2. 本地 OCR 连续失败熔断（避免重复超时拖死）
  - 代码：`app/main_window.py`
  - 机制：连续失败达到阈值后，冷却期跳过本地 OCR。
- [x] A3. 增加本地 OCR 超时压力脚本
  - 新增：`scripts/ocr_timeout_stress.py`
  - 执行：`python scripts/ocr_timeout_stress.py --iterations 5 --timeout-ms 700 --worker-sleep-sec 2.5`
  - 结果：5/5 次按预期超时，平均 1.03s，无异常成功。

## B. 测试与覆盖率

- [x] B1. 新增 worker 直接单测（补齐覆盖空洞）
  - 新增：`tests/test_local_ocr_worker.py`
- [x] B2. 补充 OCR 引擎隔离/超时单测
  - 更新：`tests/test_ocr_engines.py`
- [x] B3. 补充 GUI 熔断策略单测
  - 更新：`tests/test_zoom_gui.py`
- [x] B4. 执行关键回归
  - `python -m pytest tests/test_local_ocr_worker.py tests/test_ocr_engines.py tests/test_zoom_gui.py -q`
  - 结果：`61 passed`
- [x] B5. 执行全量+覆盖率统计
  - `python -m pytest tests -q --cov=app --cov=core --cov-report=term`
  - 结果：`168 passed`，`TOTAL 71%`
  - 关键提升：`core/services/local_ocr_worker.py` 从 `0%` 提升到 `94%`

## C. 发布门禁（当前未完成）

- [ ] C1. 建立“连续 30~100 次真实比对”自动化场景（含 OCR on/off）
- [ ] C2. 增加“资源基线门禁”：CPU/内存/句柄峰值阈值
- [ ] C3. 打包后（PyInstaller）冒烟矩阵：冷启动、连续比对、日志落盘、异常恢复
- [ ] C4. 崩溃诊断包：一键导出日志、环境变量、配置、最近历史记录
- [ ] C5. 覆盖率门槛：核心模块（OCR/GUI编排）提升到 >=80%

## D. 建议默认参数（当前阶段）

- [x] D1. 推荐运行参数
  - `VERBATIM_LOCAL_OCR_ISOLATE=1`
  - `VERBATIM_BG_TASK_TIMEOUT_MS=12000`
  - `VERBATIM_OCR_BUDGET_SEC=15`
  - `VERBATIM_LOCAL_OCR_FAIL_THRESHOLD=3`
  - `VERBATIM_LOCAL_OCR_COOLDOWN_SEC=180`
