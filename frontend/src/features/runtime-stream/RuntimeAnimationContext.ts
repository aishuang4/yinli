import { createContext } from 'react';

export type RuntimeAnimationReporter = (isAnimating: boolean) => void;

export const RuntimeAnimationContext = createContext<RuntimeAnimationReporter | null>(null);
