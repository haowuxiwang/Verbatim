# Umi-OCR 评估结论（2026-03-11）

## 结论摘要
- 可借鉴：文本后处理（排版解析）、忽略区域机制、HTTP/CLI 调用方式与插件式 OCR 引擎切换思路。
- 不建议直接依赖：Umi-OCR 以离线 OCR 应用为主，HTTP 服务并发能力较弱且存在偶发连接失败，需要我们自行设计重试与队列；直接引入会增加打包与运维复杂度。
- 合规：Umi-OCR 代码为 MIT License，可引用/改造但需保留版权与许可声明；其内置/可选 OCR 引擎（RapidOCR、PaddleOCR）分别受 Apache 2.0 许可与模型版权约束，需要单独核验与声明。

## 证据摘要（来自公开资料）
- Umi-OCR 为免费、开源、离线 OCR 工具，支持命令行与 HTTP 接口；包含文本后处理（排版解析）与忽略区域功能。  
- Umi-OCR 通过插件切换不同 OCR 引擎（Rapid-OCR / Paddle-OCR）。  
- HTTP 接口文档提示：并发能力较弱，长时间连续调用可能出现连接拒绝，需要重试。  
- Umi-OCR 采用 MIT License。  
- RapidOCR 为 Apache 2.0 授权，且模型版权归百度。  
- PaddleOCR 为 Apache 2.0 授权。

## 可借鉴点
1. 文本后处理：多栏识别与按段落/行策略重排思路，可用于提高 OCR 文本的阅读顺序稳定性。
2. 忽略区域：批量 OCR 中针对水印/页眉页脚的忽略机制，可映射到对比任务的“排除干扰区域”能力。
3. 调用接口：HTTP/CLI 入口设计可作为对外集成方案的参考模板。
4. 插件式引擎：引擎切换/编排思路可用于本地 OCR 与云 OCR 的路由治理。

## 风险与限制
- HTTP 并发能力弱，长时间调用存在 `ECONNREFUSED` 风险，需要重试与隔离机制。
- 直接引入将带来额外打包与部署复杂度，尤其是 OCR 引擎与模型资产。
- 模型版权与分发合规需要单独核验，不能仅凭 Umi-OCR 的 MIT 许可覆盖所有组件。

## 决策建议
- 短期：以“思路参考 + 局部代码参考”为主，避免直接依赖整体工程。
- 中期：若 6.2 PoC 指标通过，再考虑模块化引入（如 OCR 后处理或引擎路由）。

## 参考链接
```
https://github.com/hiroi-sora/Umi-OCR
https://raw.githubusercontent.com/hiroi-sora/Umi-OCR/main/LICENSE
https://raw.githubusercontent.com/hiroi-sora/Umi-OCR/main/README.md
https://raw.githubusercontent.com/hiroi-sora/Umi-OCR/main/docs/http/README.md
https://github.com/RapidAI/RapidOCR
https://github.com/PaddlePaddle/PaddleOCR
```
