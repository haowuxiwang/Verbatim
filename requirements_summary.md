# Verbatim 项目需求汇总

> 创建日期: 2026-02-26
> 下一阶段: 代码实现阶段

## 项目概述

构建一个**本地**、**单文件可分发（exe）**的 PDF 对比工具，支持字符级文本差异与视觉格式差异，采用**人机协作**的区域匹配方式。

---

## 🎯 核心功能流程

### 用户操作流程
1. **文件加载**
   - 加载左侧原版PDF（17页）
   - 加载右侧设计稿PDF（2页）

2. **区域映射建立**
   - 左侧拖拽框选（蓝色半透明框）
   - 切换到右侧
   - 右侧拖拽框选对应区域
   - 点击「建立映射」确认
   - 自动生成映射名称：`{关键词} {字数}字`
   - 系统生成唯一 `mapping_id`
   - SVG 曲线连接两个框的中心点

3. **差异查看**
   - 点击映射的「查看差异」
   - 显示文本差异（红色高亮）
   - 显示格式差异（绿色虚线框）

---

## 🎨 UI/UX 详细设计

### 框选交互
- **方式**：鼠标拖拽（从左上到右下）
- **反馈**：实时显示蓝色半透明框
- **框选时**：显示字符数量（实时预览）
- **取消**：点击其他区域或 ESC 键

### 连接线设计
- **类型**：SVG 曲线（贝塞尔曲线）
- **连接点**：bbox 中心点
- **样式**：
  - 颜色：红色 RGB(255,0,0)
  - 线宽：3px
  - 透明度：0.8
  - 线帽：圆角 (round)
- **交互**：自动跟随 bbox 移动，不支持手动拖动

### 映射列表
- **显示内容**：
  - 映射名称（可编辑）
  - 左侧/右侧页码
  - 字符数量
  - 状态（未分析/已分析）
  - 操作按钮：删除、重命名、查看差异

### 差异角标规则
- **位置**：固定在 bbox 右上角
- **缩放**：不随页面缩放改变比例
- **最小尺寸**：12px
- **悬浮**：显示差异详情（type: add/del/replace/format_change, count）

---

## ⚠️ 错误处理策略

### 致命错误（阻断流程）
- **场景**：
  - PDF 解析失败
  - 文件损坏
  - 非 PDF 格式
- **UI**：中央红色错误卡片
- **按钮**：「重新选择文件」
- **处理**：必须修复后才能继续

### 可恢复错误（允许继续）
- **场景**：单页解析失败、某页无文本（扫描版 PDF）
- **UI**：页面角落黄色提示
- **处理**：允许用户继续操作，页面会跳过无法解析的内容

### 不支持场景（明确提示）
- **功能**：跨页映射（V1 禁止）
- **UI**：弹出 toast 提示
- **文案**："V1 版本暂不支持跨页映射"
- **处理**：不允许用户建立跨页映射

---

## 📊 性能优化策略

### 文件加载
- **懒加载**：只加载当前页和相邻页
- **缓存**：PDF 文本解析结果缓存
- **进度**：显示加载进度条

### 差异计算
- **触发时机**：只有点击「查看差异」时才执行
- **增量计算**：已计算的结果缓存
- **优化目标**：单次 diff < 300ms（2000字符）

---

## 🔧 技术约束

### 必须使用的技术栈
- Python 3.11
- PyMuPDF（fitz）- PDF 解析
- PySide6 - GUI 框架
- rapidfuzz - 字符相似度计算
- PyInstaller - 单文件打包

### 禁止使用
- 在线 API 或网络依赖
- OCR（保证结构化字符级解析）
- 跨页选区（V1）

---

## 📝 数据结构

### 核心接口
```python
# PDF 解析
parse_page(file_path, page_number) -> PageData
# PageData 包含：text_chars: List[CharData]
# CharData = {char, index, bbox, font, size, color, style_flags}

# 区域提取
extract_region(page_data, bbox) -> RegionData

# 差异计算
diff_regions(region_left, region_right, options) -> DiffResult
# DiffResult = {ops: List[DiffOp]}
# DiffOp = {type: add|del|replace|format_change,
#           left_indices, right_indices,
#           left_bboxes, right_bboxes, meta}
```

### 映射数据结构
```json
{
  "mappings": {
    "mapping_1709012345_abc123": {
      "id": "mapping_1709012345_abc123",
      "name": "功能描述 150字",
      "left": {
        "page": 3,
        "bbox": [100, 200, 500, 300],
        "char_count": 150
      },
      "right": {
        "page": 1,
        "bbox": [50, 100, 400, 200],
        "char_count": 135
      },
      "status": "pending",
      "created_at": "2025-02-26T10:30:00Z"
    }
  }
}
```

---

## ⏱️ 开发里程碑（V1）

### Week 1：基础架构
- [ ] 数据模型定义
- [ ] PDF 解析模块
- [ ] 单元测试

### Week 2：核心功能
- [ ] 区域抽取
- [ ] 映射保存/加载
- [ ] 单元测试

### Week 3：差异引擎
- [ ] 字符级 diff
- [ ] 格式差异检测
- [ ] 性能优化

### Week 4：UI 原型
- [ ] PySide6 基础 UI
- [ ] 区域选择交互
- [ ] SVG 连接线

### Week 5：整合与打包
- [ ] 功能整合
- [ ] 错误处理
- [ ] PyInstaller 打包

---

## 📁 文件结构（规划）

```
Verbatim/
├── core/
│   ├── __init__.py
│   ├── pdf_parser.py      # PDF 解析
│   ├── region_extractor.py # 区域提取
│   ├── diff_engine.py      # 差异计算
│   └── format_diff.py     # 格式差异
├── ui/
│   ├── __init__.py
│   ├── main_window.py     # 主窗口
│   ├── pdf_viewer.py      # PDF 查看器
│   └── svg_overlay.py     # SVG 覆盖层
├── config/
│   └── thresholds.yaml    # 阈值配置
├── mappings/
│   └── *.json            # 映射文件
├── tests/
│   ├── test_parser.py
│   ├── test_diff.py
│   └── test_integration.py
├── build/
│   └── build.sh          # 构建脚本
└── requirements.txt      # 依赖
```

---

## 🎯 预留功能（未来迭代）

### 报告导出（V2+）
- JSON 报告（差异 ops 列表）
- PDF 报告（带截图和标注）
- 格式：结构化数据 + 可视化

### 跨页映射（V2+）
- 支持跨页段落
- 自动检测跨页内容
- 智能分段建议

### 高级功能（V3+）
- 批量映射建议
- 相似度自动匹配
- 版本历史对比

---

## 📋 验收标准

### 用户故事
1. **加载并浏览 PDF**
   - 首屏渲染 < 2s
   - 缩略图生成 < 1s

2. **建立区域映射**
   - 拖拽框选响应流畅
   - 映射保存/加载正常
   - SVG 曲线实时更新

3. **查看差异**
   - 点击立即显示差异
   - 角标定位准确
   - 颜色区分清晰

4. **错误处理**
   - 致命错误明确提示
   - 可恢复错误可继续操作

---

## 🤝 协作伙伴

- WuSiTan：需求定义、验收测试
- Claude Code：代码实现、测试驱动

---

*最后更新: 2026-02-26*
*准备进入: 代码实现阶段*
