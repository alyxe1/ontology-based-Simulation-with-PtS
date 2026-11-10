# OntologySim × Plant Simulation 语义仿真工作台

首个正式版本 `v0.1`。应用版本为 `0.1.0`，数据契约和本体版本为 `1.0`。

本机 Web PoC：**合成车间数据接入 → 刀具维护事件 → 订单影响 → 冻结现场 → 原生方案比较 → 保存、回放与重载**。

本版已经使用本机 Plant Simulation 2606 COM 实际运行，不提供 Python 模拟器冒充原生结果。数据均明确标注为合成演示数据，未连接真实生产设备。首版无 LLM；ASMG／LLM 融合接口与边界见 [架构说明](docs/ARCHITECTURE.md)。

## 立即运行

从 Git 检出后先运行 `install.cmd` 安装依赖并构建前端。当前机器已安装独立 micromamba 环境 `ontologysim`。

1. 双击 **`start.cmd`**，浏览器打开 **http://127.0.0.1:8820**。
2. 演示结束双击 **`stop.cmd`**，正常关闭数据发布器、工作进程及本应用创建的原生实例。
3. 新机器首次部署运行 **`install.cmd`**，要求已安装 micromamba、Node.js 和有可用许可的 Plant Simulation 2606。安装后运行时无需 CDN 或在线模型服务。

重复启动会复用本项目已运行的服务；刷新网页不会新建任务。默认 OPC UA 地址为 `opc.tcp://127.0.0.1:4842/ontologysim/`。环境变量 `ONTOLOGYSIM_PORT`、`ONTOLOGYSIM_OPC_ENDPOINT`、`ONTOLOGYSIM_PROGID`、`ONTOLOGYSIM_DATA` 可覆盖配置。

详细操作见 [客户演示脚本](docs/DEMO.md)。在工作台运行演示并保存现场后，可从“历史存档”导出配置、事件、三种原生结果、语义和模型。已启动工作台时，也可运行 `micromamba run -n ontologysim python -m scripts.prepare_demo` 自动生成 `artifacts/demo/customer-demo.zip`。运行时数据库、前端构建和交付 ZIP 保存在本机。

## 已验证的业务结果

基准配置：6 台设备、3 种产品、6 个订单、120 件工件。CNC01 每完成 8 次加工触发 45 分钟维护；首次冻结点为 **3,843.223713 秒**，已完工 7 件，CNC01 排队 67 件。方案采用同一快照、同一工件—工序样本，统一在 **129,600 秒（36 小时）** 统计。

| 指标 | 维持 FIFO | 兼容改派 + EDD | 兼容改派 + 12 小时班次 |
|---|---:|---:|---:|
| 累计完工 | 79 / 120 | 120 / 120 | 120 / 120 |
| 准时 / 已完工订单 | 1 / 3 | 6 / 6 | 6 / 6 |
| 未完工订单 | 3 | 0 | 0 |
| 全部完工时间 | 未全部完成 | 25.220 小时 | 9.321 小时 |
| 时间加权平均在制 | 74.582 | 11.948 | 10.128 |

这些是此合成配置的实测结果，不能外推为客户工厂的收益承诺。FIFO 在终点尚未完成的订单单独列示；利用率采用冻结后日历时间作分母，不是 OEE。设备速度差异、随机故障、人员与搬运车约束尚未建模。

## 实现范围

- **原生执行**：独立 COM 工作进程、单 STA 所有者、有界任务队列、原生事件步进、暂停／继续／取消与归属核对。
- **真实协议链路**：独立 asyncua OPC UA 服务与订阅客户端、MES HTTP 接收、ERP JSON 文件导入。跨系统实体映射、毫秒到秒的单位转换、唯一键去重与异常待处理区。
- **语义参与执行**：RDF 图编译输入；SPARQL 设备资格查询；SHACL 输入与快照校验；不合格或已开工改派拒绝；维护—设备—工件—订单影响关系；PROV-O 证据链。
- **持久化与恢复**：SQLite/WAL 历史、Oxigraph 可重建投影、完整事件边界快照、组合存档、重建快进及状态核对。原生 `.spp` 是存档的一部分。
- **客户工作台**：中文工厂现场、数据接入、关系／影响／本体三视图、方案 KPI 与甘特图、历史存档、回放、配置编辑和导出。

本体使用应用词表并参考 ISA-95、IOF、ISO 23247、OWL、SHACL、PROV-O。[标准映射表](docs/STANDARDS.md) 列出实际 URI、版本和范围；不声明标准认证。

首版保存与恢复的是本系统受控基准现场，支持从其创建独立预测方案。不支持任意第三方模型的通用存档恢复，也不支持在预测方案执行中间保存一个新的可续跑分支。调度采用受控中央策略，未实现论文完整的去中心化多 Agent 系统。

## 文件与开发入口

| 位置 | 内容 |
|---|---|
| `app/contracts.py` | 五类版本化 Pydantic 契约 |
| `app/native.py`、`app/worker.py` | 原生工件与时钟控制、独立 STA 所有者 |
| `app/publisher.py`、`app/service.py` | 协议发布／订阅、切片事务、任务、存档 |
| `app/semantic.py`、`ontology/` | RDF 编译、SPARQL、SHACL、有限 OWL-RL 与图投影 |
| `frontend/` | React、TypeScript、Vite、ECharts、Vis Network |
| `requirements.lock.txt`、`conda-win64.lock.txt` | 验证环境的确切包版本与 conda 构建 |
| `artifacts/acceptance/` | 可手算模型、原生与端到端验收证据 |
| `.runtime/` | 当前数据库、来源轨迹、运行产物、存档和日志 |

开发启动：`micromamba run -n ontologysim python -m scripts.serve`。单进程运行，不使用 `--reload` 或多个 Uvicorn worker。Vite 开发服务可用 `npm --prefix frontend run dev`。

常用 API：`/api/state`、`/api/stream`、`/api/spec/validate`、`/api/ingest/mes`、`/api/snapshots`、`/api/impact`、`/api/runs/compare`、`/api/checkpoints`；完整接口在本机 `/docs`。

## 验证

在项目目录运行：

```powershell
micromamba run -n ontologysim python -m pytest -q --basetemp=.runtime/pytest-check
micromamba run -n ontologysim python scripts/native_probe.py
micromamba run -n ontologysim python -m scripts.kernel_acceptance
# 工作台服务已启动时；将创建测试演示并自动归档原有演示
micromamba run -n ontologysim python -m scripts.integration_acceptance
```

[实机验收说明](docs/ACCEPTANCE.md) 说明测试证据、版本和局限。运行环境需要当前用户有本机 COM 激活与 Plant Simulation 使用权限；不修改许可证服务或强制选择许可证类型。
