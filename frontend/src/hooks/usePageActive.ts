/**
 * 缓存页是否正在显示。Chat keep-alive 不卸载，用这个判断「是否回到本页」。
 */

import { createContext, useContext, useEffect, useRef } from 'react';

export const PageActiveContext = createContext(true);

export function usePageActive(): boolean {
  return useContext(PageActiveContext);
}

/** 从隐藏回到显示时为 true，只在这一拍成立。 */
export function usePageBecameActive(): boolean {
  const active = usePageActive();
  const prev = useRef(active);
  const becameActive = active && !prev.current;
  useEffect(() => {
    prev.current = active;
  }, [active]);
  return becameActive;
}
