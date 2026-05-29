# A 股模块待办

## 个股 loss 事后评估

当前 `loss` 只用于方向 / ETF 仓位动作校准，还没有下钻到个股深筛和个股仓位建议。

待补能力：

- 新增 `ashare_stock_prediction_log.csv`，每日记录 `今日推荐`、`长期追踪`、`保留观察` 中每只个股 / ETF 的预测样本。
- 记录字段至少包括：`ticker`、`name`、`theme`、`list_type`、`score`、`rating`、`action`、`suggested_weight`、`current_weight`、`price_t0`、`pressure_label`、`pressure_score`。
- 后续回填 T+1 / T+5 / T+20 的价格、绝对收益、相对所属方向 ETF 超额、相对上证指数超额、hit 和 loss。
- 将个股 loss 按评级、动作、分数区间、列表类型、主题、资金压力标签分组汇总。
- 暂不自动改写个股分数或仓位动作；先作为诊断层展示，等样本量足够后再决定是否引入校准层。

需要回答的问题：

- 个股 hit 应该以相对方向 ETF 超额为主，还是绝对收益为主？
- 长期追踪标的是否需要和今日推荐使用同一套 loss 权重？
- ETF 型长期追踪标的是否复用方向 ETF loss，还是进入个股 / 标的层 loss？
