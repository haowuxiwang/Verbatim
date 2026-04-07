# P1 GUI自动化测试用例矩阵（可执行版）

更新时间: 2026-03-04  
目标: 建立 GUI 交互自动化门禁，覆盖高风险状态转换与回归路径。

## 1. 执行策略

1. 测试分层
- L1: 状态机单测（无真实网络）
- L2: GUI流程测试（QApplication + MainWindow）
- L3: 视觉回归（截图基线，后续接入）

2. 超时策略
- 单条命令 >120s 立即中断并定位。

3. 当前运行命令
```bash
python -m pytest tests/test_zoom_gui.py tests/test_ocr_client.py tests/test_region_extractor.py tests/test_region_manager.py tests/test_diff_engine.py tests/test_auto_match.py -q
python -m pytest --cov=core --cov=app --cov-report=term -q
```

## 2. P1测试矩阵

| ID | 级别 | 场景 | 前置 | 断言 | 优先级 | 状态 |
|---|---|---|---|---|---|---|
| GUI-SM-001 | L1 | Compare门禁状态机 | 左右页/选区逐步就绪 | 仅双侧选区齐备时可点击 | P0 | 已有 |
| GUI-SM-002 | L1 | 模式切换（框选/平移） | 窗口初始化 | 模式文本、状态一致 | P1 | 已有 |
| GUI-SM-003 | L1 | 缩放边界 | 初始化 | 最小/最大缩放按钮状态正确 | P1 | 已有 |
| GUI-SM-004 | L1 | 比对前置校验 | 无页或无选区 | 输出明确提示，不进入比对 | P0 | 已有 |
| GUI-SM-005 | L1 | 缩放后焦点保持 | 已有diff与焦点 | 焦点不退化为整块选区 | P0 | 已有 |
| GUI-SM-006 | L1 | 翻页导致结果失效 | 已有diff缓存 | diff缓存清空，避免旧高亮 | P0 | 已完成 |
| GUI-SM-007 | L1 | 换文档导致结果失效 | 已有diff缓存 | diff缓存清空 | P0 | 已完成 |
| GUI-SM-008 | L1 | 纯内容与格式差异互斥 | 两开关都开 | 自动协调并提示 | P1 | 已有 |
| GUI-SM-009 | L1 | OCR配置状态条 | 有/无token | 状态文案与按钮使能一致 | P1 | 已有 |
| GUI-FLOW-001 | L2 | 导入->框选->比对 | mock页面数据 | 生成diff并可点击定位 | P0 | 已完成 |
| GUI-FLOW-002 | L2 | 预对齐建议->应用->比对 | mock候选 | compare启用且记录修正步数 | P1 | 已有 |
| GUI-FLOW-003 | L2 | OCR回退路径 | 右侧低质量 | OCR触发且结果可解释 | P1 | 已完成 |
| GUI-VIS-001 | L3 | 1366宽布局基线 | 小屏尺寸 | 控件不重叠 | P1 | 已完成（自动化可见性基线） |
| GUI-VIS-002 | L3 | 1920宽布局基线 | 常规尺寸 | 主路径可见 | P1 | 已完成（自动化可见性基线） |

## 3. P1执行TodoList（详细）

## P1-A 状态机一致性（本周）
- [x] 完成 `GUI-SM-006` 翻页失效测试
- [x] 完成 `GUI-SM-007` 换文档失效测试
- [x] 增加“失效后点击旧差异项无效”测试

## P1-B 流程可靠性（本周）
- [x] 完成 `GUI-FLOW-001` 全链路测试（mock parse/diff）
- [x] 完成 `GUI-FLOW-003` OCR回退主链路测试（mock OCR client）
- [x] 增加“1分钟无响应保护”测试桩

## P1-C 视觉回归（下周）
- [x] 建立 1366 / 1920 基础自动化可见性基线（非截图）
- [x] 增加关键控件可点击热区检查（可见性+尺寸断言）

## P1-D 发布门禁（持续）
- [x] 将 `GUI-SM-*` 纳入每次提交回归
- [x] 设置失败即阻断打包（Beta除外，`VERBATIM_BETA_BUILD=1` 可跳过）

## 4. 当前量化目标

1. `app/main_window.py` 覆盖率: 57%（阶段目标达成）
2. 总覆盖率: 69%（阶段目标达成并超出 68%）
3. GUI高风险缺陷回归用例: >= 12 条（已达成）
