/**
 * 缓存页是否正在显示。Chat keep-alive 不卸载，用这个判断「是否回到本页」。
 */

import { createContext, useContext, useEffect, useRef } from 'react';

export const PageActiveContext = createContext(true);

export function usePageActive(): boolean {
  return useContext(PageActiveContext);
}

/** 页面从隐藏回到显示时执行一次回调；首次挂载不触发。回调始终取最新引用。 */
export function useOnPageBecameActive(callback: () => void): void {
  const active = usePageActive();
  const wasActive = useRef(active);
  const latest = useRef(callback);
  useEffect(() => {
    latest.current = callback;
  }, [callback]);
  useEffect(() => {
    const becameActive = active && !wasActive.current;
    wasActive.current = active;
    if (becameActive) latest.current();
  }, [active]);
}
