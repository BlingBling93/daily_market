# A股两阶段筛选系统设计

这个模块用两阶段流程筛选 A 股候选：

1. 先用 ETF 代理筛选市场方向。
2. 只在已选方向内对个股打分。

目标是避免每天早上扫描整个 A 股市场，同时保持输出逻辑可解释。

## 数据文件

- `ashare_theme_etf.csv`：方向池。每个方向用一只 ETF 作为代理，在同类 ETF 中优先选择当前规模最大的产品。
- `ashare_watchlist.csv`：本地核心观察池和人工备注文件。它记录长期追踪标的、当前持仓权重和人工兜底备注，但不再是每日推荐的唯一来源。
- `ashare_observation_state.json`：滚动观察状态，记录每日推荐和保留观察状态。
- `ashare_lowfreq_cache.json`：A 股低频数据的日内缓存，包括 ST 列表、个股基本面、估值和盈利预测快照。

`ashare_watchlist.csv` 中有一个 `initial_list` 字段：

- `long_term`：初始化进入长期追踪列表。
- `candidate`：参与每日推荐筛选，也适用保留观察规则。

初始长期追踪列表刻意保持很窄：

- `600900` 长江电力
- `603605` 珀莱雅

## 实时数据接口

实现上使用轻量级实时接口，而不是通过 AKShare 分页拉取全市场数据。系统会先请求东方财富，如果东方财富断连，再回退到新浪：

- 行情：`https://push2.eastmoney.com/api/qt/stock/get`
- 日 K：`https://push2his.eastmoney.com/api/qt/stock/kline/get`
- 备用行情：`https://hq.sinajs.cn/list=...`
- 备用日 K：`https://quotes.sina.cn/cn/api/jsonp_v2.php/...CN_MarketDataService.getKLineData`

每次运行会抓取：

- 来自 `ashare_theme_etf.csv` 的 18 个 ETF 代理
- 只抓取已选方向或扩展方向内 ETF 的持仓成分股
- 只对这些自动召回的股票抓取实时/准实时数据

这样可以把请求量控制在较小范围，避免一次性拉取几千只证券。

## 阶段一：方向评分

对每个 ETF 代理，系统计算：

- `return_5d`：短期热度
- `return_20d`：中期趋势
- `return_60d`：主趋势
- `sma_20_gap_pct`：过热程度 / 与趋势均线的距离
- `volume_ratio`：近 5 个交易日平均成交额 / 近 20 个交易日平均成交额
- `drawdown_60d_pct`：距离 60 日高点的回撤
- `volatility_20d`：近 20 个交易日的日收益率波动

`theme_score` 是一个 0-100 的分数：

```text
theme_score =
  trend_momentum * 35%
+ reversal_quality * 15%
+ liquidity_heat * 20%
+ crowding_control * 15%
+ macro_fit * 15%
```

当前实现细节：

- `trend_momentum = 50 + 20d_return * 2.0 + 60d_return * 0.8`
- `reversal_quality` 奖励完整 60 日上升趋势中的回调，以及下跌趋势后的早期修复。
- `liquidity_heat = 50 + (volume_ratio - 1.0) * 80`
- `crowding_control` 惩罚过高的 MA20 偏离和过高的 20 日波动率。
- `macro_fit` 目前只是一个简单的风格先验：
  - low risk：62
  - medium risk：55
  - high risk：50

方向动作标签：

| 条件 | 动作 |
|---|---|
| 5日涨幅 > 8% 或 MA20 偏离 > 10% | `暂缓` |
| score >= 68 | `重点跟踪` |
| score >= 58 | `观察` |
| score >= 50 | `轻跟踪` |
| 其他情况 | `暂缓` |

ETF 仓位动作：

| 条件 | ETF动作 |
|---|---|
| 5日涨幅 > 12%，且当日转弱或成交放大不足 | `减仓降温` |
| 5日涨幅 > 12%，或 MA20 偏离 > 14% | `减仓提醒` |
| 5日涨幅 > 8% 或 MA20 偏离 > 10% | `等回调` |
| score >= 72，5日涨幅 <= 6%，MA20 偏离 <= 8%，成交额放大 >= 1.1x | `可小幅加仓` |
| score >= 68 | `持有观察` |
| score < 58 | `暂不配置` |
| 其他情况 | `持有观察` |

晨报卡片展示排名前 3 的方向。

## 阶段二：个股评分

每日推荐池由程序自动生成。系统不再只从 ETF 前十大持仓里选股，而是围绕强方向做三层召回，然后再做深筛评分。如果候选数少于 4 只，系统会继续扩展更多方向，以保证卡片有可用内容。

三层召回：

| 层级 | 来源 | 目的 |
|---|---|---|
| `主线龙头` | 强方向 ETF 前排高权重成分股 | 确认主线方向是否真实有效，但限制数量，避免组合变成 ETF 复刻 |
| `右侧初期` | 强方向 ETF 中后段持仓 | 找刚开始被资金发现、趋势刚转右侧、拥挤度还不高的标的 |
| `扩散补涨` | 与强方向相关的本地主题/产业链股票 | 捕捉主线扩散和补涨机会，例如通信扩散到计算机、半导体、人工智能 |

`ashare_watchlist.csv` 仍然用于：

- 初始化长期追踪列表；
- 记录当前持仓权重；
- 当自动召回股票与本地观察池重合时，复用人工 thesis / catalyst / risk 备注；
- 当低频实时接口不可用时，提供数值兜底字段。

当前实时深筛的个股分数：

```text
stock_score =
  theme_score * 20%
+ fundamental_quality * 22%
+ valuation_position * 13%
+ stock_momentum * 12%
+ early_right_score * 13%
+ buy_sell_pressure * 10%
+ catalyst_risk_blend * 10%
- crowding_penalty
```

其中：

- `ST/*ST/退市整理` 在初筛阶段硬过滤，不进入深筛评分。
- `fundamental_quality` 优先使用 AKShare 财务指标，包括 ROE、利润增长、营收增长、经营现金流/净利润，以及本地观察池里的股息率。
- `valuation_position` 优先使用 AKShare/Baidu 估值中的近一年 PE TTM 分位；分位越低，得分越高。
- `stock_momentum` 使用东方财富个股日 K 数据：20 日收益和 MA20 偏离。
- `early_right_score` 奖励右侧交易初期特征：20 日收益刚转正、60 日趋势修复、站上 MA20 但偏离不高、成交额温和放大、资金压力转强。
- `buy_sell_pressure` 结合成交额放大、当日价格强度、20/60 日趋势，以及东方财富主力/超大单净流入占比。
- `catalyst_risk_blend` 结合本地催化分、风险控制，以及 AKShare 一致预期 EPS/机构评级数据。
- `risk_control` 奖励更低的本地风险分、更好的治理分，以及更低的资产负债率。
- `crowding_penalty` 惩罚 ETF 持仓权重过高、20 日涨幅过大、MA20 偏离过高、5 日涨幅过热的标的。

右侧初期的主要判断：

```text
return_20d > 0
return_60d 处于修复区间
sma_20_gap_pct 介于 0% 到 8% 更优
volume_ratio 介于 1.1 到 2.0 更优
buy_sell_pressure 高于中性更优
```

最终 4 只今日推荐尽量满足组合约束：

```text
1 只主线龙头确认
2 只右侧初期候选
1 只扩散/补涨候选
```

如果某一层候选不足，则按综合分数自动补足。

`D` / `X` 评级，或动作是 `回避` / `减仓/回避` 的个股，不允许进入今日推荐，只能进入回避状态记录。

买卖压力标签：

| 标签 | 主要条件 |
|---|---|
| `大量买入` | 当日价格上涨，成交额明显放大或主力资金明显流入，压力分较高 |
| `温和买入` | 价格正向确认，压力分高于中性 |
| `资金分歧` | 价格、趋势和资金流没有形成同向确认 |
| `温和卖出` | 价格负向确认，压力分偏弱 |
| `大量卖出` | 当日价格下跌，成交额明显放大或主力资金明显流出，压力分较低 |

深筛当前使用的实时/准实时数据接口：

- AKShare `stock_financial_analysis_indicator`：ROE、成长、杠杆、现金流质量。
- AKShare `stock_zh_valuation_baidu`：近一年 PE TTM 分位。
- AKShare `stock_profit_forecast_em`：一致预期 EPS 和近期机构评级结构。
- AKShare `stock_zh_a_st_em`：ST 硬过滤列表。
- AKShare `fund_portfolio_hold_em`：ETF 持仓，用于从强方向自动召回个股。
- 东方财富资金流日 K：主力、大单、超大单净流入比例。

低频缓存策略：

- 缓存按当前本地日期生效。
- 缓存字段：ST 股票列表、个股深筛基本面、PE 分位、EPS 预测、机构评级结构。
- 不缓存字段：行情、K 线动量、当前价格、资金流压力。这些字段每次运行仍保持实时或准实时更新。
- 日期变化后，旧缓存自动失效并重建。

动作覆盖规则：

- 如果方向是 `暂缓`，高分个股也会显示为 `不追高`，而不是 `可建仓`。
- 如果方向是 `观察`，高分个股显示为 `强关注`。
- 如果方向是 `轻跟踪`，高分个股显示为 `轻仓试探`。

晨报卡片展示 4 只股票候选。

## 观察状态机

卡片中有三类股票列表：

- `长期追踪`：由 `initial_list=long_term` 初始化的固定长期追踪列表。
- `今日推荐`：当天由方向评分和个股评分生成的前 4 只股票。
- `保留观察`：由连续多日推荐自动晋升而来的股票。

状态规则：

- 如果一只股票出现在 `今日推荐` 中，它的 `recommend_streak` 加 1。
- 如果一只股票没有出现在 `今日推荐` 中，它的 `recommend_streak` 重置为 0。
- 如果 `recommend_streak >= 3`，这只股票加入 `保留观察`。
- 如果一只股票收到硬回避信号，也就是评级为 `D`/`X`，或动作是 `回避`/`减仓/回避`，它的 `avoid_streak` 加 1。
- 如果一只股票没有收到硬回避信号，它的 `avoid_streak` 重置为 0。
- 如果 `avoid_streak >= 3`，这只股票从 `保留观察` 中移除。
- 同一天重复运行不会再次增加连续天数计数。

## 当前限制

- 部分基本面和估值输入仍然依赖 `ashare_watchlist.csv` 中的本地/手动字段兜底。
- `macro_fit` 目前只是简单先验，还不是实时宏观环境模型。
- 方向 ETF 规模记录在 `ashare_theme_etf.csv` 中，需要定期刷新。
- 东方财富和新浪接口都不是正式授权的生产 API，所以生产路径应保留 fallback，并避免全市场抓取。
- 当前候选股票池仍然较稀疏，所以热门方向可能暂时没有映射到本地核心股。

## 后续可调整项

后面比较适合调的旋钮：

- 上调或下调方向重点跟踪阈值 `score >= 68`。
- 调整过热阈值：`5d return > 8%`、`MA20 gap > 10%`。
- 用 AKShare/Tushare/BaoStock 的低频字段进一步替换本地基本面。
- 为实时股价增加更多备用行情源，例如新浪。
