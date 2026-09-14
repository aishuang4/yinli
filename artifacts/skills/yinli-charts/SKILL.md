---
name: yinli-charts
description: 在助手回复中用 ECharts JSON 围栏输出柱状图、折线图、饼图和散点图。用户要求做图、可视化、图表、趋势或占比时使用。
---

# 图表

在 Markdown 回复中用显式围栏输出 ECharts option。前端只认 `echarts` / `echarts-json`，不要用普通 `json` 围栏。

## 格式

````markdown
```echarts
{
  "title": { "text": "销售额" },
  "xAxis": { "type": "category", "data": ["1月", "2月", "3月"] },
  "yAxis": { "type": "value" },
  "series": [{ "type": "bar", "data": [12, 20, 18] }]
}
```
````

## 规则

1. 载荷必须是纯 JSON 对象，不要写函数。
2. 第一版只用 `bar`、`line`、`pie`、`scatter`。
3. 不要使用 `dataset`、`graphic`、`calendar`、`custom`。
4. 围栏未闭合时当代码块；合法闭合后再渲染。
5. 图前后可以继续写 Markdown 说明。
6. 需要落盘时用 Write 把 JSON 写到项目目录，不要只活在气泡里。
