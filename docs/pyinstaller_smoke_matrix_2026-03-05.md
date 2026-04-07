# PyInstaller 冒烟矩阵（2026-03-05）

## 必测项

1. 冷启动（首次启动不崩溃）
2. 加载左右 PDF（各 1 份）
3. 连续比对（至少 10 次，包含 OCR on/off）
4. OCR 异常恢复（超时后可继续下一次比对）
5. 日志落盘（`logs/verbatim_app.log` 有关键事件）
6. 关闭重启后历史回放可用

## 发布门禁脚本

- 脚本：`scripts/pyinstaller_release_gate.py`
- 检查内容：
  - 必需文件：`Verbatim.spec`、`main.py`、OCR runtime 模型/字体
  - 可执行文件：`dist/Verbatim.exe`
  - 启动冒烟：启动后存活到超时窗口

## 通过标准

- 门禁脚本 `gate_pass=true`
- 连续比对场景无卡死
- OCR 异常后应用可继续交互
