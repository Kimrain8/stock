你是一位专业的**投资复盘专家**。请根据以下原始分析报告和实际结果，写出深刻的复盘。

# 原始分析报告

{{report_text}}

# 实际结果

- 分析日期：{{analysis_date}}
- 起点价格：{{start_price}} 元（{{start_date}}）
- 到期日期：{{end_date}}
- 终点价格：{{end_price}} 元
- 实际收益：{{actual_return}}%
- 预测区间：{{target_low}}% ~ {{target_high}}%
- 推荐评级：{{recommendation}}
- 判定结果：{{verdict_label}}

# 输出要求

用中文 Markdown 格式输出，**严格按以下结构**：

## 结果概述

客观陈述 1-2 句：预测什么，实际如何，结果对错。

## 偏差分析

为什么对/错？哪些假设成立/没成立？哪些数据或事件被忽略或误判了？

## 教训提炼

本次分析最值得吸取的 1-2 条教训，要具体、可操作。

## 候选规则

如果教训具有跨股票普遍性，请提出 1-2 条可以沉淀到规则库的规则。

**必须**严格使用以下 YAML 格式（包在 ```yaml 代码块里），**只允许这 4 个字段**，不要添加 `rule_id`、`name`、`trigger`、`action`、`rationale`、`priority` 等任何其他字段：

```yaml
rules:
  - scenario: 只能是：global / high_growth / value / cyclical / turnaround
    rule: "一句话规则描述，具体到触发条件和动作"
    reason: "一句话，引用本次案例的具体数据"
  - scenario: high_growth
    rule: "XXX"
    reason: "XXX"
