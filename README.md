# OntologySim V0.2 · 生产推演工作台

**以实例化本体承载运行状态，使用 Plant Simulation 2606 进行原生方案推演。**

业务闭环：订单与批次 → 产品工艺和设备资格 → 维护／质量异常 → 固定分析基线 → 比较方案 → 保存、直接恢复、追溯与回放。

V0.2 使用独立的 `app_v2`、`ontologysim-v02` micromamba 环境与 `.runtime-v02` 数据目录。保留 `v0.1` 标签、旧源码和旧数据；旧版说明见 [V0.1 历史文档](docs/V0.1-README.md)。

## 运行

1. 首次部署执行 **install.cmd**；本机已安装独立环境。
2. 双击 **start.cmd**，打开 **http://127.0.0.1:8820**，首次创建本地管理员。
3. 点击“载入柔性机加工车间”。默认配置核对随附的原生参考轨迹后直接启动 MES／OPC UA 接入；配置或执行模型改变时重新计算，并在页面顶部显示准备进度。
4. 使用 **stop.cmd** 退出并释放本应用的 COM 会话。

需要 Windows、micromamba、Node.js 及有可用许可的 Plant Simulation 2606。实测版本为 **2606.0006**，ProgID 为 `Tecnomatix.PlantSimulation.RemoteControl.26.6`。安装后的前后端可离线运行；许可服务按本机配置工作。

## 本版能力

- **本体运行内核**：设备、工件、队列、活动、时钟、样本和待执行事件直接保存在 RDF 中；Python 查询、决策和协调，原生引擎执行 MU 移动与加工。
- **直接恢复**：核心存档为 TriG；按剩余时间直接初始化原生模型，不从零快进，不依赖 JSON 生产状态。
- **柔性机加工**：毛坯 BOM、原料／生产批次、差异化工艺、设备资格和工时系数、产品族换型、共享夹具、非抢占刀具维护、一次返工与复检。
- **持续接入**：实际 ERP 文件、MES HTTP、OPC UA 订阅；跨系统编号、单位、质量、去重和来源游标。创建分析基线不暂停接入。
- **证据与权限**：PROV-O、不可覆盖版本、Ed25519 签名、DPAPI 密钥保护、公钥指纹和独立离线校验；管理员／分析员／审计员。
- **客户工作台**：六个工作区、产品工艺图、BOM、资格矩阵、聚焦图谱、依据抽屉、结果与甘特图、三类回放和主数据修订。

当前来源为**合成工厂数据**，在来源区域准确标识；没有接入或写回真实生产设备。LLM／ASMG 融合不在本版。系统参考并映射制造标准，不声明完整标准认证。

## 实机结果与验收

默认 120 件工件、6 个订单，维护基线约 **3,092.753 秒**，统计终点 36 小时。一次配对样本实测：

| 指标 | FIFO | 合格设备改派 + EDD | 改派 + 12 小时班次 |
|---|---:|---:|---:|
| 完工工件 | 81 | 120 | 120 |
| 完工订单 | 1 | 6 | 6 |
| 按期订单 | 0 | 5 | 6 |
| 平均在制 | 75.319 | 25.945 | 18.810 |

结果来自真实 Plant Simulation，均有原生核对记录。这是明确参数下的合成场景结果，不是客户收益承诺或经过工厂校准的预测置信度。

已验证六类复杂状态的直接恢复与连续运行具有相同业务事件顺序、终态和 KPI；另验证真实协议接入、基线隔离、权限、签名篡改检测、暂停／取消、刷新不重跑、SSE 重连与 COM 实例释放。具体范围见 [验收记录](docs/V0.2-ACCEPTANCE.md)。

## 文档与工具

- [架构、状态所有权与模型契约](docs/V0.2-ARCHITECTURE.md)
- [业务操作、备份与 V0.1 迁移](docs/V0.2-OPERATIONS.md)
- [PROV-O、签名与独立校验](docs/V0.2-EVIDENCE.md)
- [制造本体与标准映射](docs/V0.2-STANDARDS.md)

```powershell
micromamba run -n ontologysim-v02 python -X utf8 -m pytest tests/test_v2_graph.py tests/test_v2_api.py tests/test_v2_integrity.py -q
micromamba run -n ontologysim-v02 python -X utf8 -m scripts.v2_restore_acceptance
micromamba run -n ontologysim-v02 python -X utf8 -m scripts.verify_evidence archive.zip --fingerprint <独立保存的指纹> --receipt receipt.json
```

原生验收脚本会创建自己的 Plant Simulation 会话。不要与工作台原生任务同时运行。

## 2026-11-12 首次载入修复

修复首次使用必须等待全量参考仿真、进度位于长表格底部的问题。默认车间随包提供已签名且经过 Plant Simulation 核对的参考轨迹；加载时检查签名公钥、配置、模型版本和内容摘要。轨迹只进入独立数据发布器，未来记录不进入分析基线，方案仍由原生引擎执行。

准备、就绪、失败和正常播放结束均显示在顶部。失败提供重试；播放结束提供“归档并重新载入”，保留旧工作空间与证据。浏览器增加状态轮询作为实时通知的补充。首次载入验收报告见 [first-load.json](artifacts/v02/first-load.json)。

维护快进在下一次维护开始的完整接收帧停住。可从维护／检验异常或任意完整现场创建基线；「异常与方案」支持编辑模板或自定义干预，校验后才提交。每批自动加入「保持现状」作为对照，使用同一基线、统计窗口和配对样本。详见 [操作说明](docs/V0.2-OPERATIONS.md)。
