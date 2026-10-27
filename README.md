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

## 快速启动与完整运行指南

本项目支持**两种主流启动方式**：
- **方式一：本地开发极速启动（推荐首次体验）** —— 无需安装 PostgreSQL、Redis 或 Docker，默认自动创建本地 SQLite 数据库（`quant_platform.sqlite3`），只需 Python + Node.js 即可运行完整前后端。
- **方式二：Docker Compose 生产级全栈部署** —— 一键编排 `postgres`、`redis`、`api`、`worker`、`ui`、`prometheus` 六个容器服务。

---

### 环境前置要求

| 组件 | 版本要求 | 用途 |
| :--- | :--- | :--- |
| **Python** | `>= 3.11` | 后端核心引擎、撮合回测、沙箱与 FastAPI 控制面 |
| **Node.js & npm** | `Node >= 20`（推荐 22 LTS） | Next.js 14 + TypeScript 前端管理控制台 |
| **Docker & Compose** | Docker Engine `>= 24`（仅方式二需要） | 全栈容器化编排与容器级策略沙箱 |

---

### 方式一：本地极速启动（无需 Docker / 无需外部数据库）

#### 第 1 步：克隆仓库并切换分支

```bash
git clone https://github.com/jervis829/QuantPlatform.git
cd QuantPlatform
# 如果 PR 尚未合并到 main，请切换到功能分支：
git checkout arena/326d1ecd-quantplatform
```

#### 第 2 步：安装 Python 后端环境并启动 API 服务（终端 1）

> **macOS 用户注意**：macOS 系统自带的 `/usr/bin/python3` 版本为 `3.9.6`，而本项目要求 **Python `>= 3.11`**（使用了 `StrEnum` 等现代类型特性）。如果你在 macOS 上遇到 `3.9.6 not in '>=3.11'`，请先用 Homebrew 安装 Python 3.12 并重建虚拟环境：
> ```bash
> brew install python@3.12
> rm -rf .venv
> $(brew --prefix python@3.12)/bin/python3.12 -m venv .venv
> source .venv/bin/activate
> ```

```bash
# 1. 创建并激活虚拟环境（Linux 或默认 python3 已 >= 3.11 时）
python3 -m venv .venv
source .venv/bin/activate

# 2. 安装后端及开发测试依赖
pip install --upgrade pip
pip install -e ".[dev]"

# 3. （可选）复制环境变量模板；不复制也会使用开箱即用的默认 SQLite 配置
cp .env.example .env

# 4. 启动 FastAPI 后端（监听 0.0.0.0:8000，启动时自动建表并安装防篡改审计触发器）
uvicorn quant_platform.api.app:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

启动成功后可验证后端接口：
- 健康检查：`http://127.0.0.1:8000/api/v1/health`
- Swagger 交互式 API 文档：`http://127.0.0.1:8000/docs`
- Prometheus 监控指标：`http://127.0.0.1:8000/metrics`

#### 第 3 步：安装并启动 Next.js 前端管理台（新开终端 2）

保持终端 1 运行，另开一个终端窗口：

```bash
cd QuantPlatform/frontend

# 1. 安装前端依赖
npm ci

# 2. 启动前端开发服务器（默认将 /api/* 自动反向代理到 http://127.0.0.1:8000）
npm run dev
```

打开浏览器访问：**`http://localhost:3000`** 即可进入 **QuantPlatform 管理控制台**。

---

### 方式二：Docker Compose 全栈一键自托管部署

如果你已安装 Docker，可直接启动完整的六服务生产拓扑（PostgreSQL 16 + Redis 7 + FastAPI + Supervisor Worker + Next.js UI + Prometheus）：

```bash
cd QuantPlatform

# 1. 准备环境变量文件（生产环境请务必修改其中的数据库密码与 API Key！）
cp .env.example .env

# 2. 构建镜像并后台启动全部服务
docker compose up -d --build

# 3. 查看各容器运行与健康状态
docker compose ps

# 4. （可选）查看后端与守护进程实时结构化 JSON 日志
docker compose logs -f api worker
```

全栈启动后的访问地址：
- **Web 管理控制台**：`http://localhost:3000`
- **后端 REST API & Swagger 文档**：`http://localhost:8000/docs`
- **后端 Prometheus 原始指标**：`http://localhost:8000/metrics`
- **Prometheus 监控查询面板**：`http://localhost:9090`

停止或清理服务：
```bash
# 停止容器（保留 PostgreSQL / Redis 数据卷）
docker compose down

# 停止并彻底清空数据库数据卷（谨慎操作）
docker compose down -v
```

---

### RBAC 权限角色与默认 API Key 说明

所有受保护 API 与前端控制台右上角的 **`API Key`** 输入框均采用基于角色的权限控制（请求头 `X-API-Key` 或 `Authorization: Bearer <token>`）。本地默认内置以下 4 个开发令牌（可通过 `.env` 中的 `QP_*_API_KEY` 自定义覆盖）：

| 角色（Role） | 默认开发 API Key | 允许的操作范围 |
| :--- | :--- | :--- |
| `VIEWER` | `viewer-dev-token` | 只读查看策略列表/详情、回测报告、三环境统一账本、对账报告与风控事件 |
| `RESEARCHER` | `researcher-dev-token` | 包含 `VIEWER` 权限 + 上传 Python 策略、自然语言生成策略、修改策略参数、启动 **Backtest** 与已审批的 **Shadow** |
| `OPERATOR` | `operator-dev-token` | 包含 `RESEARCHER` 权限 + **人工审批/驳回策略版本**、启动 **Live** 实盘/测试网运行、触发三层对账、触发紧急 **Kill Switch**、查看哈希链审计日志 |
| `ADMIN` | `admin-dev-token` | 平台最高权限（前端右上角默认预填此令牌，方便本地一站式体验全流程） |

---

### Web 管理控制台 6 步全流程操作指引

打开 `http://localhost:3000` 后，确认页面右上角 API Key 为 `admin-dev-token`，即可按顺序体验核心闭环：

1. **创建策略（`Strategies` 标签页）**：
   - **方式 A（上传 Python 策略）**：输入 `Strategy ID`（如 `sma_btc`）与名称，直接使用编辑器内置的 `SmaCrossStrategy` 模板点击上传；系统会自动执行 Pydantic 参数推导、AST 静态安全拦截与隔离沙箱冒烟校验。
   - **方式 B（自然语言生成策略）**：在右侧 NL 生成框输入中文或英文描述（例如：`当 BTC/USDT:USDT 4周期均线上穿 10周期均线时买入 0.05 BTC，下穿时平仓`），系统自动生成代码、通过相同安全沙箱与回测门禁后保存为 `v1` 版本。
2. **版本对比、动态调参与人工审批（`Strategy Detail` 标签页）**：
   - 点击策略进入详情页，可查看代码版本历史与 **Unified Diff** 差异高亮。
   - 下方根据策略 `ParamModel` JSON Schema **自动渲染动态参数表单**；修改参数保存后会自动生成 `v2` 版本与参数 Diff。
   - **关键门禁**：所有新上传/新生成/改参后的版本初始状态均为 `PENDING`（待审批）。此时可直接运行 **Backtest**，但若要运行 **Shadow** 或 **Live**，必须先点击页面上的 **`Approve Version`（人工审批）** 按钮将状态变为 `APPROVED`。
3. **一键启动三环境运行（`Strategy Detail` 标签页）**：
   - 分别点击 **`Launch Backtest`**、**`Launch Shadow`**、**`Launch Live (Testnet/Sim)`**，右侧实时 WebSocket 事件流会推送运行完成通知与对应的确定性 `run_id`。
4. **查看深度回测报告（`Backtest Report` 标签页）**：
   - 选择或填入回测 `run_id`，查看累计/年化收益、最大回撤起止、Sharpe/Sortino/Calmar、胜率盈亏比、多空敞口、成本拆解、逐笔/逐日归因与月度收益矩阵。
5. **查询三环境统一账本与跨环境对账（`Unified Ledger` & `Reconciliation` 标签页）**：
   - 在 `Unified Ledger` 按 `env`（`backtest` / `shadow` / `live`）和 `run_id` 查询同构的 `orders`、`fills`、`positions`、`balance_snapshots`、`equity_curve`、`funding_payments`。
   - 在 `Reconciliation` 标签页选择对账层级（如 `backtest_vs_shadow` 并填入两个 `run_id`），一键生成 6 因子归因对账报告并支持导出 CSV / JSON。
6. **风控监控与紧急熔断（`Risk & Kill Switch` 标签页）**：
   - 查看事前风控拒单与事后滑点/强平距离告警记录；点击红色 **`TRIGGER KILL SWITCH`** 可在亚秒级（`<1s`）内一键撤销全部活动挂单并可选市价全平仓位，同时写入防篡改哈希链审计日志。

---

### 命令行测试、端到端演示与常用运维命令

```bash
# 1. 运行 P0–P12 全阶段 62 项自动化验收测试
.venv/bin/pytest -v

# 2. 无需启动前后端服务，一键跑通全链路闭环脚本
#   （自动完成：NL生成策略 -> 人工审批 -> 回测/影子/实盘运行 -> 三层对账 -> Kill Switch -> 热备与恢复校验）
.venv/bin/python scripts/demo_e2e.py

# 3. 使用 Alembic 执行数据库正向/反向迁移（连接外部 PostgreSQL 时使用）
QUANT_DATABASE_URL="postgresql+psycopg://quant:quant_secret_change_me@localhost:5432/quant_platform" \
  .venv/bin/alembic upgrade head

# 4. 重新从 FastAPI OpenAPI 规范同步生成前端 TypeScript 类型并做严格类型检查
.venv/bin/python -c "
from pathlib import Path
from quant_platform.api import create_app, generate_typescript_types_from_openapi
Path('frontend/src/types/openapi.generated.ts').write_text(
    generate_typescript_types_from_openapi(create_app().openapi()), encoding='utf-8'
)
"
npm --prefix frontend run typecheck
```

### 可选进阶配置：接入外部大模型（LLM）与交易所 Testnet 密钥库

1. **接入外部 OpenAI 兼容大模型生成策略**：
   未配置外部 API Key 时，平台默认使用内置离线确定性规则合成器（无需联网即可使用自然语言生成均线/RSI/突破策略）。如需接入 OpenAI / DeepSeek / 本地 Ollama，在 `.env` 中设置：
   ```env
   QUANT_LLM_API_BASE=https://api.openai.com/v1
   QUANT_LLM_API_KEY=sk-xxxx
   QUANT_LLM_MODEL=gpt-4o-mini
   ```
2. **接入 Binance Spot / USDT-M Futures Testnet 加密密钥库**：
   平台通过 `ApiKeyVault` 使用 Fernet 对称加密保存交易所 API Key，并在入库时硬性拦截三项安全条件：**禁止开启提现权限（`can_withdraw=False`）**、**必须配置非空 IP 白名单**、**影子环境严禁挂载实盘密钥**。生成主加密密钥命令：
   ```bash
   .venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   将输出填入 `.env` 的 `QUANT_MASTER_ENCRYPTION_KEY` 即可启用持久化加密密钥库。
