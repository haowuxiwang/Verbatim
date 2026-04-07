# Verbatim 仓库正式审查报告
日期：2026-03-25  
审查范围：`D:\learn\codex\Verbatim_dev` 全仓库静态审查、测试基线核验、与 `20260319sum.md` 对照  
说明：本次未修改代码，仅审查

## 0. 编码与乱码说明
- `20260319sum.md` 以 `UTF-8` 读取正常，参考内容可信。
- 仓库内部分源码存在真实乱码，不只是终端显示问题。
- 典型位置：
  - `app/main_window.py:636`
  - `app/main_window.py:920`
  - `app/main_window.py:1178`
- 初步判断：历史上发生过错误编码保存，导致部分中文注释/文本已被污染。

## 1. 总体结论
当前版本已具备基本业务链路：

- PDF 加载
- 页面渲染
- 选区比对
- OCR 回退
- diff 列表展示
- 历史记录

但不具备对外发布条件。主要原因不是“功能缺失”，而是以下四类问题同时存在：

- 稳定性风险未关闭
- 打包态与源码态行为不一致
- UI 主线程仍承担重操作
- 测试基线已破坏

综合判定：

- 功能完整性：通过
- 工程稳定性：不通过
- 发布一致性：不通过
- 测试基线健康度：不通过
- 可维护性：部分通过

---

## 2. 审查摘要
本次审查确认：

- `20260319sum.md` 中的主要 P0/P1 判断大体成立
- 仓库中已存在可以直接复现的回归，不再只是“潜在风险”
- 当前 `pytest` 基线失败，说明主干状态已不稳定
- 打包链路和 spec 管理存在明显漂移

---

## 3. P0 级问题
### P0-1 冻结态与源码态 OCR 隔离默认行为反转
证据：
- `core/services/ocr_engines.py:68`
- `core/services/ocr_engines.py:69`

现象：
- 源码运行默认隔离开启
- 冻结运行默认隔离关闭

影响：
- 同一版本在源码态和 exe 态行为不同
- 命令行稳定不代表打包稳定
- 本地 OCR 卡死时更容易直接伤害 UI 体验

结论：
- 发布阻断

---

### P0-2 冻结态 OCR 子进程方案仍依赖外部 Python
证据：
- `core/services/ocr_engines.py:234`
- `core/services/ocr_engines.py:236`
- `core/services/ocr_engines.py:237`
- `core/services/ocr_engines.py:270`

现象：
- 冻结态若未配置 `VERBATIM_WORKER_PYTHON`，子进程 worker 默认调用 `python`
- 目标机器若无正确 Python 环境，OCR worker 可能启动失败

影响：
- 即使启用隔离，发布包仍不自洽
- 用户现场环境不可控，容易出现“研发正常、现场失败”

结论：
- 发布阻断
- 严重程度高于文档中的抽象描述

---

### P0-3 后台任务超时后并未真正回收
证据：
- `app/main_window.py:3389`
- `app/main_window.py:3404`
- `app/main_window.py:3410`
- `app/main_window.py:3413`

现象：
- 当前后台任务模型为 daemon thread + `QApplication.processEvents()`
- 超时后直接抛异常返回 UI
- 未见取消、join、子进程回收或任务熔断机制

影响：
- 连续 compare/OCR 后，后台任务可能继续跑
- CPU/内存占用可能累积
- UI 会逐步变慢，接近假死

结论：
- 发布阻断

---

### P0-4 UI 主线程仍承担重操作
证据：
- `app/main_window.py:1770`
- `app/main_window.py:1785`
- `app/main_window.py:1786`
- `app/main_window.py:2783`
- `app/main_window.py:2840`
- `app/main_window.py:2888`

现象：
- 页面渲染在主线程
- 页面解析在主线程
- 预对齐画像构建、候选检索、页解析也在 UI 路径

影响：
- 大文档、扫描件、低性能机器上更容易卡顿
- 当前依赖 `processEvents()` 保持“看起来没死”，不是稳定异步架构

结论：
- 发布阻断

---

### P0-5 当前测试基线已失败
证据：
- 已执行：`python -m pytest -q`
- 结果：`169 passed, 7 failed`

失败类型：
- 真实功能回归
- 测试与实现脱节
- 新增接口未同步测试

影响：
- 当前主干不可作为发布基线
- 所有“已经稳定”的结论都需要谨慎对待

结论：
- 发布阻断

---

## 4. P1 级问题
### P1-1 “保存选区”存在状态恢复 bug
证据：
- `app/main_window.py:5066`
- `app/main_window.py:2311`
- `tests/test_zoom_gui.py:624`

现象：
- 比对成功后代码显式启用“保存选区”
- 但 `_unlock_compare_inputs()` 会把它恢复成比对前状态
- 测试已直接失败

影响：
- 用户完成有效比对后仍无法保存选区
- 属于直接可见的产品缺陷

---

### P1-2 OCR 缓存 key 粒度偏粗
证据：
- `app/main_window.py:3364`
- `app/main_window.py:3366`

现象：
- bbox 缓存按整数像素取整

影响：
- 轻微拖动可能命中旧缓存
- 容易出现“看起来换了选区，结果却像旧结果”

---

### P1-3 打包入口与 spec 管理漂移
证据：
- 仓库中存在多个 spec：
  - `Verbatim.spec`
  - `VerbatimDbg.spec`
  - `VerbatimDiag.spec`
  - `VerbatimDir.spec`
  - `Verbatim-win64-20260311.spec`
  - `Verbatim-win64-20260319.spec`
- 但 `build.py` 当前直接走 `pyinstaller main.py`

影响：
- spec 可能长期失真
- 文档、构建、测试和产物之间可能不对应同一条链路

---

### P1-4 发布门禁过弱
证据：
- `scripts/pyinstaller_release_gate.py`

现象：
- 仅检查必需文件存在
- 仅做 exe 拉起 smoke test
- 不覆盖真实 compare/OCR/恢复路径

影响：
- “能启动”不等于“可交付”
- 不能发现连续比对、超时恢复、资源峰值等问题

---

### P1-5 源码存在编码污染
证据：
- `app/main_window.py:636`
- `app/main_window.py:920`
- `app/main_window.py:1178`

影响：
- 可读性下降
- 后续维护和再修改时容易继续扩散乱码
- UI 文案、注释和测试断言可能受到间接影响

---

## 5. P2 级问题
### P2-1 `pyproject.toml` 中 mypy 配置互相抵消
证据：
- `pyproject.toml`

现象：
- 先对若干模块设置 `ignore_errors = false`
- 后面对 `core.*`、`app.*` 又统一 `ignore_errors = true`

影响：
- 类型检查价值被显著削弱
- 容易给出“配置了 mypy”但实际没在管控的假象

---

### P2-2 大量能力堆叠在 `app/main_window.py`
证据：
- `app/main_window.py` 文件体量极大，混合 UI、流程编排、OCR、缓存、历史记录、预对齐、状态管理

影响：
- 回归容易
- 单点修改外溢风险高
- 测试隔离难度大

---

## 6. 与 `20260319sum.md` 的对照结论
### 一致项
以下判断我确认成立：

- 冻结态与源码态 OCR 行为不一致
- 后台超时不等于后台任务回收
- UI 主线程仍有重操作
- 打包门禁不足
- OCR 场景中定位与可解释性存在断裂
- OCR 缓存粒度偏粗

### 新增项
本次审查补充了文档中未充分落地的证据：

- 当前测试基线已实际失败
- “保存选区”存在可复现回归
- 冻结态子进程 OCR 仍依赖外部 Python
- 部分源码存在真实编码污染
- 构建脚本与多个 spec 已出现明显漂移

---

## 7. 当前仓库可发布性结论
不建议当前版本对外发布。

理由：

- 测试基线非绿
- 发布态 OCR 行为不稳定
- 线程/任务模型不具备强回收保证
- 打包门禁不能覆盖真实业务路径
- 存在直接可见的 UI 功能回归

---

# 最小修复清单
目标：先把仓库恢复到“可作为下一轮稳定化基线”的状态，不追求一次性美化全部架构

## A. 先修 7 个测试失败
### A1 修复 `保存选区` 状态恢复回归
问题：
- 成功比对后按钮被启用
- 随后被 `_unlock_compare_inputs()` 恢复掉

涉及：
- `app/main_window.py:5066`
- `app/main_window.py:2311`

建议：
- 将“比对后应启用”的最终状态写入 `_compare_input_state`
- 或在 `_end_compare_feedback()` 之后再统一设置最终启用状态

优先级：
- 最高

---

### A2 修复 `render_pdf_region_png` 测试漂移
问题：
- 多个测试仍在 patch `app.main_window.render_pdf_region_png`
- 实际实现已改为 `render_pdf_region_png_with_meta`

失败位置：
- `tests/test_zoom_gui.py:899`
- `tests/test_zoom_gui.py:910`
- `tests/test_zoom_gui.py:929`
- `tests/test_zoom_gui.py:954`
- `tests/test_zoom_gui.py:972`

建议：
- 统一修改测试桩，mock `render_pdf_region_png_with_meta`
- 返回值应模拟带 `image_bytes`、`clip_bbox`、`zoom` 的对象
- 同步核对缓存相关断言是否仍成立

优先级：
- 最高

---

### A3 修复 `CompareHistoryManager.add_record()` 接口变更引发的测试失败
问题：
- 实现新增了 `ocr_state`、`ocr_state_reason`
- 旧测试未补参数

失败位置：
- `tests/test_zoom_gui.py:994`
- 实现：`core/compare_history.py:130`

建议：
- 若这是正式接口升级，则补齐测试
- 若希望兼容旧调用，可给这两个参数提供默认值

优先级：
- 高

---

## B. 关闭发布阻断点
### B1 统一 OCR 隔离默认策略
问题：
- 源码态与冻结态默认值反转

涉及：
- `core/services/ocr_engines.py:68`

建议：
- 无论 `sys.frozen` 与否，默认都保持同一策略
- 推荐默认开启隔离，再通过显式环境变量关闭
- 不允许“源码稳定、exe 不稳定”的双轨行为继续存在

优先级：
- 最高

---

### B2 去掉冻结态对外部 Python 的隐性依赖
问题：
- 冻结态 worker 默认调用 `python`

涉及：
- `core/services/ocr_engines.py:234`
- `core/services/ocr_engines.py:237`

建议：
- 明确设计：
  - 方案1：发布包内提供可用 worker 解释器/入口
  - 方案2：冻结态禁用该 worker 路线并采用受控替代方案
- 不应把“用户机器有 python”当作前提

优先级：
- 最高

---

### B3 重做后台任务超时后的回收模型
问题：
- 现在只是 UI 放弃等待，不是真取消

涉及：
- `app/main_window.py:3389`

建议：
- OCR/渲染等高风险任务优先走子进程隔离
- 超时必须对应真实终止和资源回收
- 避免继续依赖 `daemon thread + processEvents` 作为核心执行模型

优先级：
- 最高

---

### B4 减少 UI 主线程重操作
问题：
- 页面渲染、解析、预对齐计算仍在主线程

涉及：
- `app/main_window.py:1770`
- `app/main_window.py:1785`
- `app/main_window.py:2840`

建议：
- 先迁移最重的三类任务：
  - PDF 页面渲染
  - `parse_page`
  - 预对齐候选计算
- UI 线程只负责状态切换与结果落盘

优先级：
- 高

---

## C. 修复工程基线
### C1 统一唯一构建入口
问题：
- 多个 spec 与 `build.py` 漂移

建议：
- 确定唯一受支持构建方式：
  - 要么统一走某一个 spec
  - 要么删除过期 spec，仅保留脚本化入口
- 文档、CI、测试、发版必须指向同一条构建链路

优先级：
- 高

---

### C2 强化 release gate
现有不足：
- 只检查“存在”和“能启动”

建议增加：
- 连续 compare 压力测试
- OCR on/off 两条主路径
- 超时后恢复能力校验
- 资源峰值阈值校验
- 打包产物上的真实业务 smoke test

优先级：
- 高

---

### C3 清理源码乱码
问题：
- 真实编码污染已经进入源码

建议：
- 先只修正文案、注释、常量文本
- 统一全部文本文件编码为 `UTF-8`
- 在修复时避免再次用系统默认编码写回

优先级：
- 中

---

### C4 恢复类型检查有效性
问题：
- `mypy` 配置基本被全局忽略覆盖

建议：
- 取消对 `core.*`、`app.*` 的全局 `ignore_errors = true`
- 先只对最关键模块恢复约束：
  - `core/services/ocr_engines.py`
  - `core/compare_history.py`
  - `app/view_models.py`

优先级：
- 中

---

# 推荐执行顺序
1. 修复 7 个测试失败，恢复绿线
2. 统一 OCR 隔离默认策略
3. 去除冻结态对外部 Python 的依赖
4. 重做后台任务超时回收模型
5. 统一构建入口并加强 release gate
6. 清理乱码与类型检查配置
7. 再进入 UI 可读性和交互语义优化

# 一句话结论
当前版本可以继续内部整改，但不应作为发布候选；优先把“测试基线、OCR 隔离一致性、任务回收、打包自洽性”四件事做实。
