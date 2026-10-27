# QuantPlatform — 自托管多策略加密货币量化交易平台

支持 **回测（Backtest）/ 影子交易（Shadow）/ 实盘交易（Live）** 三环境完全同构的加密货币量化交易平台。
首要支持 **Binance（现货 + USDT 本位永续合约，兼容交割合约与多交易所扩展）**。

## 架构红线（10 条硬约束）

1. **策略只产生意图**：策略继承 `Strategy`，仅输出 `Signal | TargetPosition`，时间来自注入 `Clock`，随机性来自注入的 seed 化 `RNG`。
2. **三环境同构**：`BacktestRunner` / `ShadowRunner` / `LiveRunner` 复用同一条策略生命周期与执行管线，核心层零 `if env == ...` 分支。
3. **数据只能经 DataFeed 访问**：游标越界直接抛 `LookaheadError`，结构性杜绝前视偏差。
4. **执行抽象统一**：`BrokerAdapter` 统一抽象（`BacktestBroker` / `ShadowBroker` / `ExchangeBroker`），`ccxt` 仅作传输层，精度/费用/撮合语义全部显式实现。
5. **记录统一**：`orders` / `fills` / `positions` / `balance_snapshots` / `equity_curve` / `funding_payments` 三环境同表，由 `(env, run_id)` 区分。
6. **可复现**：`run_id = sha256(策略代码哈希 + 配置快照 + 数据版本 + SymbolSpec 版本 + 引擎版本)`。
7. **安全隔离**：上传策略与 LLM 生成策略走完全相同的 AST 静态检查 + 容器/命名空间隔离沙箱管线。
8. **版本与审计**：策略代码与参数任何修改自动生成新版本与 diff，审计日志追加写且带哈希链防篡改。
9. **精度与合约规格是第一等公民**：版本化 `SymbolSpec`，所有下单路径强制归一化与精度校验。
10. **交易所是唯一真相源**：启动先拉取交易所挂单/持仓/余额/成交/资金费对账重建，就绪前拒绝任何新单。

## 目录结构

```text
QuantPlatform/
├── pyproject.toml
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/0001_initial_schema.py
├── quant_platform/
│   ├── config.py
│   ├── exceptions.py
│   ├── core/            # 14 核心领域模型 + 8 核心接口 + StrictDecimal + 确定性哈希
│   ├── db/              # SQLAlchemy 2.0 ORM + StrictDecimalColumn + 统一账本 + 审计触发器
│   ├── data/            # P1: DataFeed、时钟偏移同步、订单簿快照+增量重建、数据集哈希
│   ├── specs/           # P2: SymbolSpec 版本服务、变更检测告警、统一订单归一化与校验
│   ├── strategy/        # P3: 策略生命周期、Pydantic 参数表单、AST 安全检查、隔离沙箱、版本库
│   ├── backtest/        # P4/P5: 加密撮合回测引擎（深度滑点/强平/资金费/借币利息/交割）与绩效报告
│   ├── execution/       # P6: 订单状态机、幂等回报处理、超时查单补偿、限频调度、同构 Runner
│   ├── shadow/          # P7: 实时订单簿+逐笔驱动影子撮合、排队模型参数化、全链路追溯
│   ├── live/            # P8: ExchangeBroker、启动状态重建、事前/事后风控、Kill Switch、密钥库
│   ├── reconciliation/  # P9: 三层对账（系统vs交易所 / 影子vs实盘 / 回测vs影子归因）
│   ├── nl_generator/    # P10: 自然语言生成策略流水线（同构安全管线 + 回测门槛 + 强制人工审批）
│   ├── api/             # P11: FastAPI REST/WebSocket + 权限审计 + OpenAPI
│   └── observability/   # P12: 结构化日志、Prometheus 指标、告警、备份恢复、优雅降级
├── frontend/            # P11: Next.js + TypeScript 前端控制台
└── tests/               # P0–P12 全阶段自动化测试套件
```

## 阶段进度

- [x] **P0 骨架与领域模型**：14 个核心模型、8 个核心接口、三环境统一表结构、`StrictDecimal` 禁 `float`、Alembic 正反向迁移、追加写审计日志。
- [x] **P1 数据层**：`HistoricalDataFeed` / `StreamingDataFeed` 结构性禁前视偏差（`LookaheadError`）、K 线缺口与乱序/丢包 Tick REST 补洞与去重、Level-2 订单簿快照+增量序号重建、服务器时间偏移校验告警与拒单、多交易所价格隔离、数据集内容哈希版本。
- [x] **P2 交易对规格与合约元数据**：`SymbolSpecService` 版本化存储与历史时间点回查、Binance 原始过滤器定点解析（零 `float`）、规格变更自动检测与告警、`normalize_and_validate_order` 与 `SpecEnforcedBroker` 强制校验（含结构化拒单原因码）。
- [x] **P3 策略框架、沙箱与版本管理**：完整策略生命周期、Pydantic `ParamModel` JSON Schema 动态表单推导、`ASTSecurityChecker`（前视偏差/危险调用/无限循环/递归/超大内存全拦截）、`ContainerStrategySandbox`（Docker + Linux `unshare --user --net` + `RLIMIT_AS/CPU/FSIZE` 只读无网隔离、超时强杀）、`StrategyVersionStore` 版本 diff、审批与追加写审计。
- [x] **P4 回测引擎（加密撮合规则）**：VIP 与平台币抵扣 Maker/Taker 分离计费（现货扣基础/计价币、合约扣保证金币）、严格精度与最小名义价值拒单、Walk-the-Book 逐档滑点 + 限价排队模型 + Post-Only(GTX)/IOC/FOK、全仓/逐仓分层维持保证金率、**标记价格（Mark Price）** 强平 + 强平惩罚 + ADL 排序 + 周期资金费结算、按小时借币计息、极端行情（插针/簿空/维护窗口/下架）与交割合约到期结算移仓。
- [x] **P5 回测结果与报告**：累计/年化收益、最大回撤（起止与持续期）、Sharpe/Sortino/Calmar（与 `quantstats` 交叉验证误差 `< 1e-6`）、胜率/盈亏比/期望值、换手率、HHI 集中度、多空敞口、逐笔与逐日归因、基准 Alpha/Beta/IR、月度收益矩阵、费用/资金费占比、持仓时长分布，以及样本内外分割、Walk-Forward 滚动前推与参数敏感性热力图。
- [x] **P6 执行管线**：8 状态穷举状态机（64 对迁移单测）、系统全局唯一 `clientOrderId` 与双向映射表、重复/乱序回报幂等收敛、下单超时查单补偿（以交易所结果为准）、闪断重连对账、权重感知限频调度与 429 指数退避、漂移检测，以及经 `coverage` 证明零分支同构的 `BacktestRunner` / `ShadowRunner` / `LiveRunner`。
- [x] **P7 影子交易**：连接实时行情流但与实盘交易密钥物理隔离（`ShadowIsolationError`）的 `ShadowBroker`、参数化持久化的盘口队列排位/成交量参与率/模拟飞行延迟/Maker-Taker 费率与模拟资金费结算、`signal_ts -> order_ts -> fill_ts -> snapshot_ref` 全链路追溯，以及与同日盘口回放回测的 100% 信号一致性验证。
- [x] **P8 实盘接入与风控引擎**：原始字符串直转 `Decimal` 的 `ExchangeBroker`（含启动强制同步挂单/持仓/余额/成交/资金费门禁与交易所原生保护性 `STOP_MARKET` 止损单）、Fernet 加密存储 `ApiKeyVault`（禁止提现权限、强制 IP 白名单、密钥轮换审计）、10 项事前风控门禁 + 事后滑点/强平距离告警、亚秒级（`<1s`）紧急 `Kill Switch` 一键全撤与可选市价平仓，以及真实 `SIGKILL` 崩溃恢复验收测试。
- [x] **P9 三层对账引擎**：第一层系统本地库 vs 交易所真实状态（支持 `auto_heal` 自动收敛）、第二层影子 vs 实盘（按 UTC 日期 × 交易对维度对比成交量、VWAP 价差 bps、手续费与资金费差异）、第三层回测 vs 影子（滑点、成交率、延迟、手续费、资金费、数据快照 6 维归因拆解），支持定时与手动触发、数据库持久化、告警回调、JSON/CSV 导出及按 `order_id` / `fill_id` / `ts_ms` 明细下钻。
- [x] **P10 自然语言生成策略管线**：NL 提示词结构化解析（`NLStrategySpec`）-> LLM 代码生成（支持离线确定性规则合成器与 OpenAI 兼容端点 + 编译器报错有界自修复）-> 与手工上传策略 100% 共享的 Pydantic Schema 校验 + AST 安全检查 + 隔离沙箱冒烟测试 -> 自动历史回测门禁（交易笔数/最大回撤阈值校验）-> 强制人工审批状态（`PENDING`，严禁自动审批放行）入库与审计。
- [x] **P11 后端 API 与前端管理台**：FastAPI REST + WebSocket 实时事件推送、RBAC 角色鉴权（`VIEWER` / `RESEARCHER` / `OPERATOR` / `ADMIN`）与全量写操作哈希链审计、OpenAPI 自动生成严格 TypeScript 类型（`openapi.generated.ts`），以及 Next.js + TypeScript 六大工作台页面（策略列表与 NL/代码创建、含版本 Diff 与 Pydantic 动态表单及一键三环境启动的策略详情页、回测报告页、三环境统一账本页、三层对账中心、风控与亚秒级紧急 Kill Switch 台）。
- [x] **P12 可观测性、运维与自托管部署**：结构化 JSON 日志（敏感凭证自动脱敏 + `RotatingFileHandler` 定量轮转）、10 项 Prometheus 核心指标与 `/metrics` 暴露、在线原子热备份与 SHA-256 校验恢复演练（`DatabaseBackupManager`）、`WebSocketHealthMonitor` 心跳巡检与 5 分钟断连自动补账恢复、`PlatformSupervisor` 启动强制对账与数据库瞬断重试、维护模式优雅降级、`docker-compose.yml` 全栈编排（`postgres`/`redis`/`api`/`worker`/`ui`/`prometheus`）与一键端到端演示脚本 `scripts/demo_e2e.py`。

## 快速启动与验证

### 1. 本地开发与全量验收测试（P0–P12 共 62 项测试）

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
npm ci --prefix frontend

# 运行全阶段测试套件（P0–P12）
.venv/bin/pytest -v

# 一键运行端到端全链路演示（NL 生成 -> 审批 -> 三环境运行 -> 三层对账 -> Kill Switch -> 热备恢复）
.venv/bin/python scripts/demo_e2e.py
```

### 2. Docker Compose 自托管一键部署

```bash
docker compose up -d --build
# API 健康检查: http://localhost:8000/api/v1/health
# Prometheus 指标: http://localhost:8000/metrics
# Web 管理控制台: http://localhost:3000
```
