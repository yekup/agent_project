# interfaces/ — 扩展接口层

本文档为需要 **GPU / 商业合作 / 远期规划** 的功能预留抽象接口。

## 接口清单

| 模块 | 文件 | 状态 | 需要什么 |
|------|------|------|----------|
| 🎨 角色立绘 | [portrait_generator.py](portrait_generator.py) | ❌ 待实现 | GPU + IP-Adapter / Stable Diffusion |
| 🔄 RLHF 微调 | [rlhf_pipeline.py](rlhf_pipeline.py) | ⚠️ 部分实现 | GPU + TRL / LLaMA-Factory |
| 🧠 本地 LLM | [llm_provider.py](llm_provider.py) | ✅ 已实现 `DeepSeekProvider`<br>❌ 待实现 `LocalModelProvider` | GPU + Qwen-7B / ChatGLM |
| ⚖️ 版权校验 | [copyright_verifier.py](copyright_verifier.py) | ✅ 已实现 `PermissiveVerifier`<br>❌ 待实现 `QidianVerifier` | 起点/晋江 API 商务合作 |
| 🕸️ 图存储 | [graph_storage.py](graph_storage.py) | ✅ 已实现 `Neo4jBackend`<br>🟠 待实现 `ColdStorageBackend` | Neo4j 社区版 |
| 📊 评估指标 | [eval_metrics.py](eval_metrics.py) | ✅ 已实现 `FaithfulnessMetric`<br>🟠 不建议实现 `PowerLevelConsistencyMetric` | — |

## 接入指南

每个接口都提供了：

1. **抽象基类**（`abc.ABC`）—— 定义方法签名
2. **默认实现** —— 功能降级时的兜底行为
3. **数据模型**（`@dataclass`）—— 输入输出格式
4. **异常信息** —— `NotImplementedError` 的错误消息包含环境配置指引
5. **注册/工厂机制** —— 通过配置切换具体实现

## 使用原则

- 调用方 always 依赖接口（抽象基类），never 依赖具体实现
- 当具体实现缺失时通过 `NotImplementedError` 引导用户完成配置
- 所有接口的默认实现（如 `PermissiveVerifier`）保证不阻塞业务流程

## 未来实现 checklist

当你有 GPU 或商务合作后，逐项完成：

```markdown
- [ ] PortraitGenerator 实现 (GPU + IP-Adapter)
- [ ] LocalModelProvider 实现 (GPU + Qwen-7B)
- [ ] FineTuner + ModelDeployer 实现 (GPU + vLLM)
- [ ] Full RLHF pipeline 跑通
- [ ] QidianVerifier 实现 (起点 API key)
- [ ] ColdStorageBackend 实现 (TimescaleDB / ClickHouse)
```
