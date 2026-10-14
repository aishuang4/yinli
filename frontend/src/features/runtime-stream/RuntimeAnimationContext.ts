import { createContext } from 'react';

export type RuntimeAnimationReporter = (isAnimating: boolean) => void;

export const RuntimeAnimationContext = createContext<RuntimeAnimationReporter | null>(null);

/** 当前消息里仍在增长的正文块。只有这一块显示流式光标。 */
export const StreamCaretContext = createContext<string | null | undefined>(undefined);
