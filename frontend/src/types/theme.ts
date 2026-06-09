/**
 * 主题系统类型定义
 *
 * 定义支持的主题名称和 ThemeContext 的接口。
 */

/** 可用的主题标识 */
export type ThemeName = 'light' | 'dark';

/** 主题上下文的值类型 */
export interface ThemeContextValue {
  /** 当前主题名称 */
  theme: ThemeName;
  /** 设置主题 */
  setTheme: (theme: ThemeName) => void;
  /** 切换亮/暗模式 */
  toggle: () => void;
  /** 是否为暗色模式 */
  isDark: boolean;
}
