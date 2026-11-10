# 本体与标准映射

此项目使用应用本体 `urn:ontologysim:`，版本 1.0。**参考并映射相关标准，不声明认证、完整符合性或导入全部标准本体。** 以下映射可以在 `ontology/core.ttl`、`ontology/shapes.ttl` 和导出的 TriG 中检查。

| 体系与固定参考 | 本项目对象 | 实际关系与边界 |
|---|---|---|
| [ISA-95 / IEC 62264](https://www.isa.org/standards-and-publications/isa-standards/isa-95-standard) | Equipment、Capability、Product、Order、OperationPlan | 设备、物料、生产订单及工艺的业务对齐。未实现完整 B2MML 交换协议或全部 Part 2 对象；不为标准杜撰 RDF URI。 |
| [IOF Core 202603](https://spec.industrialontologies.org/portal/release/202603/core.html) | Equipment | `skos:closeMatch https://spec.industrialontologies.org/ontology/construct/PieceOfEquipment`。应用的设备能力模型是简化实现，未声明等价类。 |
| 同上，版本 IRI `https://spec.industrialontologies.org/ontology/202603/core/Core/` | OperationPlan | `skos:closeMatch https://spec.industrialontologies.org/ontology/construct/PlanSpecification`，表示计划，不代表过程已经发生。 |
| 同上 | OperationExecution | `skos:closeMatch https://spec.industrialontologies.org/ontology/construct/PlannedProcess`，用于工序执行与维护过程的业务对应。 |
| [ISO 23247-3](https://www.iso.org/standard/78744.html) | 设备、观测、数字表示、执行服务 | 参考制造要素数字表示与数字孪生职责分工。不是 ISO 23247 符合性认证实现。 |
| [OWL 2](https://www.w3.org/TR/owl2-overview/) | 应用类、对象属性、子类关系 | RDFLib + OWL-RL 对小型应用 TBox 做有限闭包，不推导未经观测的生产事实。IOF 使用注释映射，不在线导入外部本体闭包。 |
| [SHACL](https://www.w3.org/TR/shacl/) | 能力、工序、订单、工件位置 | pySHACL 检查必填关系、正值、设备与产品引用、秒单位和单一位置；业务校验额外检查工件守恒、占用、跨协议时钟和改派资格。 |
| [PROV-O](https://www.w3.org/TR/prov-o/) | 观测、快照、运行活动、结果 | 实际使用 `prov:Entity`、`prov:Activity`、`prov:wasDerivedFrom`、`prov:wasGeneratedBy`、`prov:used`。原始消息含来源 ID、事件与接收时间、质量，SQLite 保存完整证据。 |

能力匹配使用 SPARQL：设备必须具备相同工序类型和材料，且设备可达到的公差数值 `≤` 工序所要求的公差数值。所有精度均采用 μm；所有运行时间采用秒；OPC 发布时钟采用毫秒并在接入时转换。

`compile_input()` 从通过校验的 RDF 图解析工时、资源班次、维护参数、订单交期与释放时间，构建原生执行契约。候选设备由图查询形成，修改能力配置会改变候选集。原生执行入口不接收任意 SPARQL 或 SimTalk 字符串。

命名图：

- `urn:ontologysim:graph/source`：已接收来源记录的元数据。
- `urn:ontologysim:graph/linking`：统一实体、已知计划、当前观测切片。
- `urn:ontologysim:graph/enrichment`：维护影响关系与有限模式推理。
- `urn:ontologysim:graph/schema`：应用本体。
- 每次运行独立的 `graph/scenario/...`：预测结果、订单交付、设备利用率和原生证据。

Oxigraph 是可重建投影，SQLite/WAL 是历史记录依据。图附带项目、版本、配置摘要和已提交事件游标。重建仅使用该游标之前已经接收的信息。
