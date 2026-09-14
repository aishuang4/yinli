/**
 * ECharts 图表实例封装。独立模块以便 React.lazy 按需加载，echarts 不进入首屏 chunk。
 */

import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export default function EchartsChart({ option }: { option: EChartsOption }) {
  const ref = useRef<HTMLDivElement>(null);
  const instance = useRef<ReturnType<typeof echarts.init> | null>(null);
  // JSON 来源的配置按内容比较，避免每次解析产生的新对象触发动画重播。
  const optionJson = JSON.stringify(option);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const chart = echarts.init(element);
    instance.current = chart;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth <= 0 || element.clientHeight <= 0) return;
      if (chart.getWidth() !== element.clientWidth || chart.getHeight() !== element.clientHeight) {
        chart.resize();
      }
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      instance.current = null;
      chart.dispose();
    };
  }, []);

  useEffect(() => {
    instance.current?.setOption(JSON.parse(optionJson) as EChartsOption, { notMerge: true });
  }, [optionJson]);

  return (
    <div
      ref={ref}
      style={{ width: '100%', height: 320, margin: '12px 0' }}
      role="img"
      aria-label="ECharts 图表"
    />
  );
}
