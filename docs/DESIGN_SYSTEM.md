# QuantPlatform 量化交易系统 UI/UX 设计系统说明文档

本文档定义 `QuantPlatform`（回测 · 影子 · 实盘三环境同构加密量化交易平台）前端的设计原则、设计 Token、核心量化组件契约、状态与防呆规范以及 OpenAPI 类型工程纪律。可结合线上交互式组件陈列页 `/design-system` 对照查阅。

---

## 1. 核心量化交互原则（优先级高于视觉装饰）

1. **危险操作分级与物理隔离（Principle 1）**：
   - **安全级 (`Backtest`)**：蓝色主按钮 (`BacktestActionButton`)，允许在巡检列表行内或单屏快速启动（从 `/strategies` 发起回测 ≤ 2 次点击）。
   - **谨慎级 (`Shadow`)**：琥珀色描边次级按钮 + 显式 `SHADOW` 环境徽章 (`ShadowActionButton`)，要求版本已通过人工审批（红线 8）并确认模拟撮合物理隔离。
   - **高危动钱级 (`Live` & `Kill Switch`)**：
     - 实盘部署按钮 (`LiveDangerActionButton`) **严禁**与安全按钮并排同色展示，必须置于斜纹警示物理隔离容器 `IsolatedDangerZone` 内；
     - 启动实盘强制执行**三步确认向导**（`LiveThreeStepWizardModal` / `/runs/new`）：① 确认账户环境、可用余额、`NO_WITHDRAW` 与 IP 白名单 → ② 事前风控预检（展示预计初始保证金、单笔委托名义上限、当前杠杆 vs 合约/风控上限、强平距离与时钟偏移，未通过硬性锁死）→ ③ 手动逐字输入 `strategy_id` 解锁（禁用自动填充）；
     - 全局紧急停机开关 `KILL SWITCH` 通过左侧红色竖向物理分隔线固定在顶部状态栏最右端，点击后先调 `/api/v1/risk/kill-switch/preview` 预览即将撤销的活跃挂单与即将平掉的真实持仓，再二次确认触发亚秒级停机。
2. **全局常驻关键状态栏（Principle 2）**：
   - `GlobalStatusBar`（固定 48px 顶栏）在所有页面常驻显示：三环境运行中策略计数、今日盈亏（及昨日参考）、交易所连接健康度、服务器毫秒时钟偏移、三层对账未解决差异数及影响金额、未确认风控告警计数。
   - **严禁仅用几秒消失的 Toast 提示连接断开或对账异常**：所有异常指标保持高亮/脉冲直至恢复或人工确认，且每一项均可点击直接下钻到对应诊断页。
3. **三层信息密度递进（Principle 3）**：
   - **一眼层**：每页顶部固定 3～5 张 `KpiMetricCard`（必须附带时间窗口、基准或前期参考、异常边框色调）；
   - **第一层结构表**：`QuantDataTable` 高密度可排序筛选表格；
   - **原始明细下钻层**：行展开 (`Strategy -> Run -> Order -> Fill + SignalTrace`) 或抽屉检视，杜绝直接把原始 JSON 摊平铺满屏幕。
4. **强制空 / 加载 / 错误三态（Principle 6）**：
   - `EmptyState`：说明当前为何为空，并提供明确的主/次引导动作；
   - `RunLoadingState`：展示阶段步进条、完成百分比、已处理 Bar 进度、已耗时与预计剩余时间（ETA）及取消入口；
   - `ActionableErrorState`：Typescript 编译期强制要求传入 `reasonCode`（错误原因码）与 `remediation`（可执行修复建议），并支持折叠查看原始回执与一键恢复动作。

---

## 2. 设计 Token 体系 (`frontend/src/lib/tokens.ts` & `globals.css`)

### 2.1 色彩与涨跌极性动态切换
- **深色低疲劳底色**：主背景 `#080c16`、卡片层 `#0e1525`、悬浮层 `#111a2e`、边框 `#1e293b`，适应长时间盯盘。
- **环境语义色（全站绝对唯一）**：
  - `BACKTEST`：冷调投研蓝（`blue-500/400`）
  - `SHADOW`：模拟预警琥珀（`amber-500/300`）
  - `LIVE`：实盘真金白银深红（`rose-600/300`）
- **严重程度四级色阶**：`CRITICAL`（深红脉冲）> `HIGH`（橙红）> `MEDIUM`（琥珀）> `LOW`（石板蓝）。
- **涨跌极性可配置 (`data-pnl-polarity`)**：
  - 支持在侧边栏底部或 `/design-system` 一键切换 **`green-up`（绿涨红跌，加密主流）** 与 **`red-up`（红涨绿跌，国内期货/A股习惯）**，持久化于 `localStorage`，并通过 CSS 变量 `--pnl-up` / `--pnl-down` 及图表配色字典同步驱动所有数字与 K 线蜡烛颜色。

### 2.2 字体与数字精度规则 (`frontend/src/lib/formatters.ts`)
- 全部金额、价格、数量、收益率、时间戳、哈希与订单号强制使用等宽字体 + `font-variant-numeric: tabular-nums lining-nums`（`.tabular-nums-strict`），表格中严格右对齐。
- **精度绑定版本化 `SymbolSpec`**：
  - `formatPriceBySpec(price, tick_size)`：严格对齐合约 `tick_size` 小数位，绝不截断有效精度或凭空补零；
  - `formatQtyBySpec(qty, step_size)`：严格对齐合约 `step_size` 数量步长；
  - `formatCurrency(value, currency)`：千分位分隔符 + 显式货币单位（如 `+18,420.55 USDT`）；
  - **负值与精确零值严格区分**：零值渲染中性灰 `0.00 USDT`（不带正负号），微小亏损渲染极性亏损色并带显式 `-` 号；
  - 字符串级十进制解析，避免大额名义本金在前端因 IEEE-754 浮点转换产生尾数漂移。

---

## 3. 核心组件清单与职责

| 组件路径 | 组件名 | 核心职责 |
| --- | --- | --- |
| `components/ui/button.tsx` | `Button`, `BacktestActionButton`, `ShadowActionButton`, `LiveDangerActionButton`, `IsolatedDangerZone` | 操作风险分级按钮与实盘斜纹物理隔离区 |
| `components/quant/badges.tsx` | `EnvBadge`, `ApprovalBadge`, `SeverityBadge`, `RunStatusBadge` | 环境、人工审批门禁、差异严重度与运行状态徽章 |
| `components/quant/numeric-display.tsx` | `NumericDisplay`, `KpiMetricCard` | 统一精度/极性数值渲染与一眼层指标卡 |
| `components/quant/state-views.tsx` | `EmptyState`, `RunLoadingState`, `ActionableErrorState` | 强制空状态、多阶段加载态、带修复建议的错误态 |
| `components/quant/confirm-dialog.tsx` | `ConfirmActionDialog`, `KillSwitchModal` | 支持确认词解锁的分级弹窗与挂单/仓位预览二次确认停机框 |
| `components/quant/data-table.tsx` | `QuantDataTable` | TanStack Table + Virtual 虚拟滚动、列排序/筛选/显隐、三档密度、分页、URL 参数同步与行展开 |
| `components/quant/chart-container.tsx` | `FinancialChartContainer` | `lightweight-charts` K线、买卖点箭头、成交量副图、净值/基准/水下回撤同步缩放、十字光标读数、表格行点击时间轴定位、CSV/PNG 导出 |
| `components/strategy/schema-param-editor.tsx` | `SchemaParamEditor` | Pydantic JSON Schema 自动语义分组、默认值/范围/单位提示、仓位放大风险警告、与已保存版本的实时前后 Diff |
| `components/layout/*` | `AppShell`, `GlobalStatusBar`, `SidebarNav`, `CommandPalette`, `NotificationCenter` | 全局桌面端骨架（1440px 基准 / 1280px 无水平滚动）、`Cmd/Ctrl+K` 全局检索、常驻通知抽屉 |

---

## 4. 路由信息架构与类型工程约束

- **多路由工作流**：
  - `/strategies`（及 `/`）：策略巡检列表（10 秒状态判断 + 2 次点击快速回测 + 新建 Python/NL 策略）
  - `/strategies/[strategyId]`：策略详情六分区工作台（首屏概览 + 物理隔离操作区 + 参数 Diff + 源码与人工审批 + 分类运行记录 + 可暂停搜索日志）
  - `/runs/new`：三环境差异化防呆启动中心
  - `/reports/[runId]`（支持 `?compareRunId=...`）：回测深度归因、弱点诊断、月度矩阵、费用侵蚀拆解、参数敏感性热力图、Walk-Forward 与双运行对比
  - `/ledger`：三环境统一账本组合筛选与 `Order -> Fills + SignalTrace` 展开溯源
  - `/reconciliation`：三层对账 Tab、严重度排序、影响总金额、行级 `order_id/fill_id/ts_ms` 下钻与一键导出
  - `/system`：连接、时钟偏移、限频、沙箱统计、风控告警历史与 Kill Switch 哈希链审计
  - `/design-system`：设计基础与组件验收页
- **OpenAPI 类型唯一事实源**：前端严禁手写后端接口返回类型，统一由 `quant_platform.api.openapi_ts` 从 FastAPI OpenAPI Schema 生成 `frontend/src/types/openapi.generated.ts`，并由 `tests/test_p11_api_and_ui.py` 在每次测试中强制校验一致性、零 `localhost`/`127.0.0.1` 硬编码与 `tsc --noEmit` 零错误。
