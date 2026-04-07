# 测试矩阵（执行指引）

## 单元测试
```powershell
python -m pytest tests\test_diff_engine.py
python -m pytest tests\test_text_quality_service.py
python -m pytest tests\test_ocr_client.py
python -m pytest tests\test_prealign_service.py
python -m pytest tests\test_zoom_gui.py
```

## 集成测试（对比主流程）
```powershell
python -m pytest tests\test_pipeline_integration.py
python -m pytest tests\test_compare_orchestrator.py
python -m pytest tests\test_compare_history.py
```

## GUI 冒烟（手工）
1. 加载左右 PDF
2. 框选区域并对比
3. 查看内容/格式差异列表
4. 查看差异详情与定位
5. 导出/回放历史记录
