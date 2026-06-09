/**
 * cn — 合并 CSS class 名的工具函数
 *
 * 组合 clsx（条件类名）和 tailwind-merge（冲突解决）。
 * 用法：cn("base-class", isActive && "active-class", className)
 *
 * @param inputs - 类名参数，支持字符串、布尔值、对象、数组
 * @returns 合并后的类名字符串
 */

import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
