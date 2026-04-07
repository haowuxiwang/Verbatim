# OCR Runtime 打包与命令行测试（执行说明）

更新时间：2026-03-05

## 1. OCR 路由策略（当前实现）

- `VERBATIM_OCR_ROUTE=local_first`（默认）
  - 顺序：本地 Paddle -> 云端 Paddle -> 人工确认
- `VERBATIM_OCR_ROUTE=local_only`
  - 顺序：本地 Paddle -> 人工确认
- `VERBATIM_OCR_ROUTE=cloud_only`
  - 顺序：云端 Paddle -> 人工确认

低可信场景已强制人工确认（不可跳过）。

## 2. 命令行可用性/可信任性测试

脚本：`scripts/ocr_route_smoke_test.py`

### 示例命令

```powershell
python scripts/ocr_route_smoke_test.py `
  --pdf digest.pdf `
  --page 0 `
  --bbox "50,120,500,220" `
  --repeat 3 `
  --route local_first `
  --ocr-mode sync `
  --timeout-ms 15000
```

### 输出含义（JSON）

- `availability`: 成功轮次 / 总轮次
- `trustworthy`: 当前是否达到基础可信（脚本内阈值：可用性 >= 0.8 且无失败）
- `fallback_count`: 发生引擎回退次数（如 local -> cloud）
- `runs[].attempts[]`: 每次尝试的引擎、模式、质量和错误信息

## 3. OCR Runtime 打包

脚本：`scripts/package_ocr_runtime.py`

### 示例命令

```powershell
python scripts/package_ocr_runtime.py `
  --runtime-dir ".\\ocr_runtime" `
  --out "dist\\ocr_runtime.zip"
```

### 打包结果

- `dist/ocr_runtime.zip`
- zip 内包含：
  - `manifest.json`
  - `ocr_runtime/`（你的依赖、模型、运行时文件）

## 4. 主程序使用 runtime

设置环境变量：

```powershell
$env:VERBATIM_OCR_RUNTIME_DIR="D:\\path\\to\\ocr_runtime"
$env:VERBATIM_OCR_ROUTE="local_first"
```

说明：未配置云端 token 时，`local_first/local_only` 仍可启用 OCR。

## 5. 无管理员权限的离线本地方案（推荐）

先初始化本地 runtime 目录骨架：

```powershell
python scripts/init_offline_runtime.py --runtime-dir .\ocr_runtime
```

然后把以下资源放入该目录（不需要管理员权限）：
- `ocr_runtime/assets/fonts/simfang.ttf`
- `ocr_runtime/models/PP-OCRv5_mobile_det/*`
- `ocr_runtime/models/PP-OCRv5_mobile_rec/*`

最后设置严格离线模式并验证：

```powershell
$env:VERBATIM_OCR_RUNTIME_DIR="$PWD\\ocr_runtime"
$env:VERBATIM_OCR_ROUTE="local_only"
$env:VERBATIM_OCR_OFFLINE_STRICT="1"
python scripts/ocr_route_smoke_test.py --pdf digest.pdf --page 0 --bbox "50,120,500,220" --repeat 2 --route local_only
```

若缺字体/模型，程序会直接报“runtime 缺失”而不是尝试联网下载。
