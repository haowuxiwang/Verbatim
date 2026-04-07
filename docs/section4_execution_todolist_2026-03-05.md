# 第4节执行 ToDo（2026-03-05）

目标：落实执行总结第4节，形成可复用的工业化门禁流程。

## P0 连续比对压测（真实链路）

- [x] 新增脚本：真实 PDF 连续比对 30 次（含 OCR on/off 混合场景）
- [x] 执行压测并输出失败分类（timeout/ocr_error/empty_text/diff_error）
- [x] 形成门禁结论（是否通过）
  - 结果：当前 `gate_pass=false`（30 次成功完成但 OCR 路径 15/15 超时，资源门禁通过）。

## P1 资源与失败分类门禁

- [x] 将资源门禁与失败分类统一输出（RSS/句柄/耗时分位）
- [x] 设定默认严格阈值并执行一轮
  - 结果：`scripts/ocr_timeout_resource_gate.py` 通过（`gate_pass=True`）。

## P2 打包后门禁

- [x] 新增 PyInstaller 发布门禁脚本（支持未打包场景）
- [x] 输出打包冒烟矩阵文档（必测项与通过标准）
- [x] 在当前环境执行一次门禁脚本并记录状态
  - 前置检查：通过（skip exe run）。
  - exe 启动冒烟：失败（`early_exit`）。

## P3 覆盖率目标跟踪

- [x] 记录当前覆盖率与未达标模块
- [x] 给出到 >=80% 的增量测试计划
  - 依据：当前 `TOTAL 71%`，`app/main_window.py=61%` 仍是主要缺口。
