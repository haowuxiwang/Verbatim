# Verbatim 本地 Paddle OCR 迁移 TodoList（执行版）

更新时间：2026-03-05  
决策冻结：
- 本地 Paddle 为首选 OCR 路由
- 云端 OCR 为可选补充（降级路径）
- 低可信场景必须人工确认后才能进入最终比对

## 0. 目标与成功标准

### 目标
- 解决“首次可用、二次 OCR 失败即不可用”的流程断点。
- 将 OCR 能力从“强依赖网络”改为“本地可稳定运行，云端可补充”。
- 保持 `.exe` 轻量，OCR 运行时可独立分发。

### 成功标准（必须全部满足）
- 同一选区连续比对 3 次，流程不中断，且都能产出可解释结果（`PASS/REVIEW`）。
- 低可信场景均进入“人工确认”步骤，不能直接产出硬结论。
- 未安装 OCR runtime 时主程序可运行，且给出明确引导，不崩溃。
- 安装 OCR runtime 后，CPU 设备可完成本地推理（无 GPU 依赖）。

## 1. 架构与流程改造（P0）

- [x] 新增 OCR 路由策略开关：`local_first`（默认）、`cloud_only`、`local_only`
- [x] 将 OCR 执行抽象为统一接口：`OcrEngine`（local/cloud 同签名）
- [ ] 固化主流程：`Local Paddle -> Cloud Paddle(可选) -> REVIEW(人工确认)`
- [ ] OCR 失败分类标准化：超时、空结果、网络、鉴权、模型异常
- [x] 二次比对失败时禁止“静默回退空文本”，必须进入人工确认

验收：
- [ ] 日志能明确看到每次比对的路由选择与失败分类
- [ ] 任一路由失败不会中断主界面交互

## 2. 人工确认强制门（P0）

- [x] 新增“人工确认”状态机节点（低可信必经，不可跳过）
- [x] 展示 OCR 文本预览（左/右）、质量分、失败原因
- [x] 提供“确认使用该文本并继续比对”按钮
- [ ] 提供“重新 OCR（本地/云端）”按钮
- [x] 支持人工修订文本后继续比对（保留审计）

验收：
- [x] 低可信触发时，不能直接进入最终差异发布
- [ ] 历史记录包含：是否人工确认、确认前文本、确认后文本摘要

## 3. 本地 PaddleOCR 引入（P1）

- [x] 采用 PP-OCRv5 CPU 路线，优先 `mobile_det + mobile_rec`
- [ ] 默认关闭非必要模块（方向分类/文档矫正）以降低时延
- [ ] 增加本地 OCR 健康检查（模型可加载、可推理）
- [ ] 增加本地 OCR 首次加载提示（避免“首次慢”被误判卡死）

验收：
- [ ] 无 GPU 环境可稳定运行
- [ ] 同一区域重复调用结果稳定，噪声可控

## 4. 轻量化分发（P1）

- [ ] 设计双包分发：
- 主程序包：仅核心功能，不内置 Paddle runtime
- OCR 扩展包：`ocr_runtime.zip`（venv + paddle 依赖 + 模型）
- [ ] 主程序启动时按相对路径探测 OCR runtime
- [ ] 未探测到 runtime 时提示安装路径与指引
- [ ] 保持现有 `Verbatim.spec` 轻量策略，不把重依赖打进主 exe

验收：
- [ ] 主程序体积与当前版本同量级
- [ ] OCR 扩展包可独立升级，不影响主程序发布节奏

## 5. 质量门禁与测试（P1）

- [ ] 新增“连续两次比对”回归测试（复现当前瓶颈）
- [ ] 新增“本地成功/云端失败”与“本地失败/云端成功”组合测试
- [ ] 新增“低可信强制人工确认”流程测试
- [ ] 新增“未安装 OCR runtime”降级可用性测试

验收：
- [ ] 关键新增测试全部通过
- [ ] 不回归现有 compare/history 核心行为

## 6. 运维与观测（P2）

- [ ] 指标埋点：本地成功率、云端补充率、人工确认率、最终 REVIEW 率
- [ ] 日志补充：request_id、engine、model、耗时、失败码、人工确认动作
- [ ] 形成最小周报模板（是否达到可灰度标准）

## 7. 里程碑建议

- M1（1-2 天）：流程与状态机改造 + 强制人工确认打通（可无本地引擎）
- M2（2-3 天）：本地 Paddle 引擎接入 + 路由稳定 + 基础回归
- M3（1-2 天）：双包分发打包验证 + 文档补齐 + 灰度准备

## 8. 风险与回滚

- 风险：本地模型首次加载慢导致用户误判“卡死”
- 对策：首轮加载提示 + 超时可中断 + 进度反馈

- 风险：CPU 机型差异导致耗时波动
- 对策：设置预算时间，超时自动进入人工确认，不输出硬结论

- 风险：OCR 扩展包路径混乱导致不可发现
- 对策：固定相对目录规范 + 启动自检 + 一键诊断

- 回滚策略：
- 如本地引擎稳定性不达标，临时切回 `cloud_only + 强制人工确认`
- 保持接口抽象不变，避免再次大改调用链

## 9. 本次执行记录（2026-03-05）

- 已完成：低可信场景强制人工确认门禁接入主流程
- 已完成：人工确认弹窗支持左右文本编辑后继续比对
- 已完成：取消人工确认时中止本次比对并给出明确提示
- 已完成：新增/更新 GUI 测试，`tests/test_zoom_gui.py` 全量通过（47 passed）
- 已完成：`OcrEngine` 抽象层与 `local_first/cloud_only/local_only` 路由接入
- 已完成：本地 Paddle 引擎骨架（CPU、PP-OCRv5_mobile）接入流程
- 已完成：新增 `tests/test_ocr_engines.py`，覆盖云端/本地引擎基础行为
- 待执行下一批：OCR runtime 双包分发 + 本地模型目录规范 + 健康检查与首轮加载提示

## 10. P3 Progress (2026-03-05)

- [x] Added route-level CLI smoke test: `scripts/ocr_route_smoke_test.py`
- [x] Added OCR runtime zip packager: `scripts/package_ocr_runtime.py`
- [x] Added runtime and CLI execution guide doc: `docs/ocr_runtime_packaging_and_cli_test_2026-03-05.md`
- [x] Built main executable: `dist/Verbatim.exe`
- [x] Built OCR runtime zip sample: `dist/ocr_runtime.zip`
- [ ] Add first-load UI hint for local model warmup
- [ ] Add explicit runtime-missing install guidance dialog
- [ ] Expand coverage for `build.py`, `scripts/*`, and route/failure classification branches

## 11. Offline Strict Update (No Admin) - 2026-03-05

- [x] Local engine now enforces offline strict preflight (`VERBATIM_OCR_OFFLINE_STRICT=1` default)
- [x] Missing font/model fails fast with explicit runtime error (no implicit network download)
- [x] Added runtime scaffold initializer: `scripts/init_offline_runtime.py`
- [x] Added no-admin offline setup guide in `docs/ocr_runtime_packaging_and_cli_test_2026-03-05.md`
