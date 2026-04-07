## 角色说明

Agent 名称：**pdf-compare-builder**

职责：作为 Claude Code（或其他代码生成代理）的指导规范，用于分阶段生成可运行代码。Agent 负责分解任务、生成小模块、提供测试用例、并逐步集成。

## 工作流程（严格）

1. **数据结构定义阶段**
   * 只输出数据模型（`<span>PageData</span>`, `<span>CharData</span>`, `<span>RegionData</span>`, `<span>DiffOp</span>`）和 JSON 示例
   * 必须包含 field 类型与示例
2. **解析模块实现阶段**
   * 实现 `<span>core/pdf_parser.py</span>`：基于 **entity["software","PyMuPDF","python pdf library"]** 的解析函数
   * 提供 3 个单元测试（不同字体/字号/颜色/加粗组合）
3. **区域抽取 + 保存阶段**
   * 实现 `<span>core/region_extractor.py</span>`：输入 page_data 与 bbox，输出 region_data
   * 提供保存/加载 `<span>mappings/*.json</span>` 的工具
4. **diff 引擎实现阶段**
   * 实现 `<span>core/diff_engine.py</span>`：字符级 LCS + 编辑操作映射
   * 实现 `<span>core/format_diff.py</span>`：基于阈值判断格式变更并产出 `<span>format_change</span>` ops
   * 提供性能测试（单次 region 对比时间 < 300ms，视大小而定）
5. **UI 最小可用原型阶段**
   * 实现 PySide6 的最小 viewer：渲染 page screenshot、允许用户框选 bbox、回显 bbox
   * 能加载 `<span>mappings/*.json</span>` 并在左右页面上渲染高亮与连接线
6. **打包阶段**
   * 提供 `<span>build.sh</span>` 或 `<span>build.bat</span>`，并提供 `<span>pyinstaller</span>` 指令示例

## 输出规则（Agent 与 Claude Code 的交互约束）

* 每次请求 Agent 生成代码，必须限制为一个模块（文件）及对应测试
* 生成代码时必须包含依赖注释和 `<span>requirements.txt</span>` 行
* 每个文件末尾必须附带运行/测试命令示例
* 禁止一次性生成整个项目
