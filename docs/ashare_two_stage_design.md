# A-share two-stage screening design

This module screens A-share ideas in two stages:

1. Select market directions with ETF proxies.
2. Score stocks only inside selected directions.

The goal is to avoid scanning the whole A-share market every morning while keeping the output explainable.

## Data Files

- `ashare_theme_etf.csv`: direction pool. One ETF proxy per direction, selected by current total market value among comparable ETFs.
- `ashare_watchlist.csv`: local core watchlist and annotation file. It stores long-term tracking names, current weights, and manual fallback notes; it is not the sole source of daily recommendations.
- `ashare_observation_state.json`: rolling observation state for daily recommendations and retained observations.
- `ashare_lowfreq_cache.json`: daily cache for low-frequency A-share data, including ST list and per-stock fundamentals / valuation / forecast snapshots.

`ashare_watchlist.csv` has an `initial_list` field:

- `long_term`: initialized into the long-term tracking list.
- `candidate`: eligible for daily recommendation and retained observation rules.

The initial long-term list is intentionally narrow:

- `600900` 长江电力
- `603605` 珀莱雅

## Live Data Interfaces

The implementation uses lightweight realtime endpoints instead of full-market AKShare pagination. It tries Eastmoney once, then falls back to Sina if Eastmoney disconnects:

- Quote: `https://push2.eastmoney.com/api/qt/stock/get`
- Daily K-line: `https://push2his.eastmoney.com/api/qt/stock/kline/get`
- Fallback quote: `https://hq.sinajs.cn/list=...`
- Fallback daily K-line: `https://quotes.sina.cn/cn/api/jsonp_v2.php/...CN_MarketDataService.getKLineData`

Each run fetches:

- 18 ETF proxies from `ashare_theme_etf.csv`
- ETF holdings only inside selected or expanded directions
- Realtime / near-realtime data only for those recalled stocks

This keeps the request count small and avoids pulling thousands of securities.

## Stage 1: Direction Scoring

For each ETF proxy, the system computes:

- `return_5d`: short-term heat
- `return_20d`: medium-term trend
- `return_60d`: primary trend
- `sma_20_gap_pct`: overheat / trend distance
- `volume_ratio`: average amount over last 5 sessions divided by last 20 sessions
- `drawdown_60d_pct`: distance from 60-day high
- `volatility_20d`: 20-session daily return volatility

`theme_score` is a 0-100 score:

```text
theme_score =
  trend_momentum * 35%
+ reversal_quality * 15%
+ liquidity_heat * 20%
+ crowding_control * 15%
+ macro_fit * 15%
```

Current implementation details:

- `trend_momentum = 50 + 20d_return * 2.0 + 60d_return * 0.8`
- `reversal_quality` rewards pullbacks inside an intact 60-day uptrend and early repair after downtrends.
- `liquidity_heat = 50 + (volume_ratio - 1.0) * 80`
- `crowding_control` penalizes high MA20 gaps and high 20-day volatility.
- `macro_fit` is a simple style prior for now:
  - low risk: 62
  - medium risk: 55
  - high risk: 50

Direction action:

| Condition | Action |
|---|---|
| 5d return > 8% or MA20 gap > 10% | `暂缓` |
| score >= 68 | `重点跟踪` |
| score >= 58 | `观察` |
| score >= 50 | `轻跟踪` |
| otherwise | `暂缓` |

ETF position action:

| Condition | ETF action |
|---|---|
| 5d return > 12%, plus same-day weakness or insufficient turnover expansion | `减仓降温` |
| 5d return > 12%, or MA20 gap > 14% | `减仓提醒` |
| 5d return > 8% or MA20 gap > 10% | `等回调` |
| score >= 72, 5d return <= 6%, MA20 gap <= 8%, volume ratio >= 1.1x | `可小幅加仓` |
| score >= 68 | `持有观察` |
| score < 58 | `暂不配置` |
| otherwise | `持有观察` |

The morning card displays the top 3 directions.

## Prediction Validation and Feedback Loop

Direction scoring now has two layers:

1. `theme_score` remains an interpretable rule-based score for current trend, turnover, crowding, and style priors.
2. A prediction log stores each day's score and ETF action as an auditable sample, then gradually validates the signal after 1/5/10/20 trading days.

Each run maintains two files:

- `ashare_prediction_log.csv`: daily samples for each direction, including score, action, prediction signal, T0 direction ETF price, T0 Shanghai Composite price, and T+1/T+5/T+10/T+20 prices, returns, excess returns versus both the direction universe and the Shanghai Composite, hit flags, and losses.
- `ashare_model_state.json`: rolling hit rates, strong-signal hit rates, average excess returns versus the direction universe and the Shanghai Composite, and average losses for each horizon.

Validation horizons advance only by real trading sessions, not calendar days or workflow run days. The system derives the trading-date set from the daily K-lines of the direction ETFs and the Shanghai Composite. A T+1/T+5/T+10/T+20 result is filled only when both the prediction date and the current market date are present in that trading calendar and enough sessions have elapsed. Weekend runs, market holidays, and runs before the data source has advanced to a new trading session do not enter hit rates or losses early.

ETF actions are mapped into prediction signals:

| ETF action | Prediction signal |
|---|---:|
| `可小幅加仓` | +1.0 |
| `持有观察` | +0.3 |
| `等回调` | 0.0 |
| `暂不配置` | -0.3 |
| `减仓提醒` / `减仓降温` | -1.0 |

Each horizon uses two benchmarks. For any horizon `h`:

```text
actual_hd_return = price_th / price_t0 - 1
actual_excess_universe_hd = direction ETF h-day return - equal-weighted h-day return of all direction ETFs
actual_excess_benchmark_hd = direction ETF h-day return - Shanghai Composite h-day return
actual_excess_hd =
  actual_excess_universe_hd * 65%
+ actual_excess_benchmark_hd * 35%
```

The universe benchmark measures direction-selection skill, while the Shanghai Composite benchmark measures whether the signal beats the broad A-share market. If Shanghai Composite data is unavailable, the system falls back to the universe benchmark only.

Hit rules:

- Positive signals require positive blended excess return.
- Negative signals require negative blended excess return.
- Neutral signals require blended excess return to stay inside a small band.

Each horizon has its own loss. The base loss still combines four components:

```text
loss =
  0.35 * directional_miss
+ 0.25 * mse(signal, normalized_next_excess_return)
+ 0.25 * rank_gap
+ 0.15 * downside_penalty
```

Return normalization scales with the horizon: T+1 uses the short-term scale, while T+5/T+10/T+20 widen return and drawdown tolerance by the square root of the horizon so medium-term validation is not over-penalized by a one-day volatility scale.

The morning card shows feedback lines by horizon:

```text
Yesterday check: 2 of the prior 3 strong directions hit; addable excess universe +0.42% / Shanghai +0.86%
T+1 short-term: direction hit rate 58%, strong-signal hit rate 64%, universe +0.18%/day, Shanghai +0.31%/day, loss 0.31
T+5/T+10 swing: 5d hit 62% / 10d hit 59%; T+20 trend hit 55%, loss 0.44
```

T+1 mainly calibrates ETF execution actions, T+5/T+10 calibrate direction persistence, and T+20 calibrates medium-term theme capture. This MVP only evaluates and displays feedback. It does not directly rewrite `theme_score` yet. Once the sample size is large enough, a calibration layer can adjust predictive scores down for historically weak crowded signals and up for early right-side signals with strong realized hit rates.

The system also writes a parameter diagnostics table into the `parameter_diagnostics` field in `ashare_model_state.json`. It is kept as a backend cache for observation only; it is not shown directly on the morning card and does not change parameters automatically. The table groups samples by:

- ETF action: `可小幅加仓`, `持有观察`, `等回调`, `暂不配置`, `减仓提醒`, and related actions.
- Score bucket: `<58`, `58-67`, `68-71`, `72+`.
- Heat state: 5d return > 8% or MA20 gap > 10% is `短线过热`; otherwise `非过热`.
- Style: the `style` field from the direction universe, such as tech, growth, or low risk.

For each group, the cached table records sample count, T+1/T+5/T+20 hit rates, T+1 average excess return versus the direction universe, and T+1 average loss. Once enough samples accumulate, this cache is the basis for human-reviewed threshold and signal calibration.

## Stage 2: Stock Scoring

The daily recommendation pool is generated by the program. It no longer selects only the top ETF holdings. Instead, it recalls stocks around strong directions in three layers and then deep-scores those stocks. If fewer than 4 ideas are found, the system expands to more directions to keep the card useful.

Recall layers:

| Layer | Source | Goal |
|---|---|---|
| `主线龙头` | top high-weight constituents of strong-direction ETFs | confirm the main line, while limiting overlap with the ETF itself |
| `右侧初期` | middle/back constituents of strong-direction ETFs | find stocks that are just entering early right-side confirmation with lower crowding |
| `扩散补涨` | related local themes / supply-chain names | capture diffusion and catch-up opportunities outside the most crowded ETF leaders |

`ashare_watchlist.csv` is still used for:

- long-term tracking initialization,
- current holding weight,
- manual thesis / catalyst / risk notes when the same ticker appears in the auto-recalled pool,
- fallback numeric inputs if a live low-frequency interface is unavailable.

Stock score:

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

Where:

- `ST/*ST/退市整理` is a hard exclusion in the initial stock filter. It does not enter deep scoring.
- `fundamental_quality` uses AKShare financial indicators when available: ROE, profit growth, revenue growth, operating cash flow / net profit, and dividend yield from the local watchlist.
- `valuation_position` uses one-year PE TTM percentile from AKShare/Baidu valuation when available; lower percentile scores better.
- `stock_momentum` uses Eastmoney stock K-line data: 20-day return and MA20 gap.
- `early_right_score` rewards early right-side confirmation: 20-day return turning positive, 60-day trend repair, price above MA20 without excessive extension, moderate amount expansion, and improving pressure.
- `buy_sell_pressure` combines amount expansion, daily price strength, 20/60-day trend, and Eastmoney main/super-large order net-flow percentage.
- `catalyst_risk_blend` combines the local catalyst score, risk control, and AKShare consensus EPS/rating data.
- `risk_control` rewards lower local risk score, better governance score, and lower asset-liability ratio.
- `crowding_penalty` penalizes excessive ETF holding weight, high 20-day gain, large MA20 extension, and overheated 5-day gain.

Early right-side preference:

```text
return_20d > 0
return_60d in a repair zone
sma_20_gap_pct between 0% and 8% is preferred
volume_ratio between 1.1 and 2.0 is preferred
buy_sell_pressure above neutral is preferred
```

The final 4 daily recommendations try to follow this portfolio constraint:

```text
1 main-line leader confirmation
2 early right-side candidates
1 diffusion / catch-up candidate
```

If a layer has too few candidates, the remaining slots are filled by total score.

Stocks rated `D` / `X`, or with action `回避` / `减仓/回避`, are not allowed into `今日推荐`; they are only recorded as avoid signals in the observation state.

Buy/sell pressure labels:

| Label | Main Conditions |
|---|---|
| `大量买入` | positive daily price move, strong amount expansion or strong main-flow inflow, high pressure score |
| `温和买入` | positive price confirmation and above-neutral pressure score |
| `资金分歧` | price, trend, and flow do not confirm the same direction |
| `温和卖出` | negative price confirmation and weak pressure score |
| `大量卖出` | negative daily price move, heavy amount expansion or strong main-flow outflow, low pressure score |

Current live data interfaces used in deep screening:

- AKShare `stock_financial_analysis_indicator`: ROE, growth, leverage, cash-flow quality.
- AKShare `stock_zh_valuation_baidu`: one-year PE TTM percentile.
- AKShare `stock_profit_forecast_em`: consensus EPS forecasts and recent rating mix.
- AKShare `stock_zh_a_st_em`: ST hard-exclusion list.
- AKShare `fund_portfolio_hold_em`: ETF holdings used to auto-recall stocks from strong directions.
- Eastmoney fund-flow day K-line: main, big-order, and super-large-order net inflow ratios.

Low-frequency cache policy:

- The cache is keyed by the current local date.
- Cached fields: ST ticker list and per-stock deep-screen fundamentals, PE percentile, EPS forecast, and analyst rating mix.
- Non-cached fields: quote, K-line momentum, price, and fund-flow pressure. These remain realtime / near-realtime on each run.
- When the date changes, the cache is ignored and rebuilt automatically.

Action override:

- If direction is `暂缓`, high-scoring stocks become `不追高` instead of `可建仓`.
- A `暂缓` direction only blocks chasing strength; it does not protect weak names: `score >= 68` becomes `B / 不追高`, `52 <= score < 68` becomes `C / 持有观察`, and `score < 52` becomes `D / 减仓/回避`.
- If direction is `观察`, high-scoring stocks become `强关注`.
- If direction is `轻跟踪`, high-scoring stocks become `轻仓试探`.

The morning card displays 4 stock candidates.

## Observation State Machine

The card has three stock lists:

- `长期追踪`: fixed long-term tracking list initialized from `initial_list=long_term`.
- `今日推荐`: top 4 stocks produced by the current day's direction and stock scoring.
- `保留观察`: stocks promoted from repeated daily recommendations.

State rules:

- If a stock appears in `今日推荐`, its `recommend_streak` increases by 1.
- If a stock does not appear in `今日推荐`, its `recommend_streak` resets to 0.
- If `recommend_streak >= 3`, the stock is added to `保留观察`.
- If a stock receives a hard avoid signal (`D`/`X` rating or `回避`/`减仓/回避` action), its `avoid_streak` increases by 1.
- If a stock does not receive a hard avoid signal, its `avoid_streak` resets to 0.
- If `avoid_streak >= 3`, the stock is removed from `保留观察`.
- Same-day reruns do not increment streak counters again.

## Current Limitations

- Fundamental and valuation inputs are still local/manual in `ashare_watchlist.csv`.
- `macro_fit` is a simple prior, not a live macro regime model yet.
- Direction ETF scale is captured in `ashare_theme_etf.csv`; it should be refreshed periodically.
- Eastmoney and Sina endpoints are unofficial public endpoints, so the production path should keep fallback sources and avoid full-market scraping.
- The candidate universe is still sparse, so a hot direction may have no mapped local core stock.

## Future Adjustments

Good next knobs to tune:

- Raise/lower `score >= 68` for direction expansion.
- Change overheat thresholds: `5d return > 8%`, `MA20 gap > 10%`.
- Replace local fundamentals with AKShare/Tushare/BaoStock low-frequency fields.
- Add a fallback quote source such as Sina for realtime stock prices.
