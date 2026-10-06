/** Fixed steps under a model's own window. The window itself is the last step. */
export const CONTEXT_BUDGET_LADDER = [128_000, 256_000, 512_000] as const;

/** ``128000`` → ``128K``, ``1000000`` → ``1M``. */
export function contextBudgetLabel(tokens: number): string {
  if (tokens % 1_000_000 === 0) return `${tokens / 1_000_000}M`;
  if (tokens % 1_000 === 0) return `${tokens / 1_000}K`;
  return String(tokens);
}

/**
 * Steps for the assembly control. Hidden when the model window is unknown or
 * no larger than 128K.
 */
export function contextBudgetSteps(
  contextLength: number | null | undefined,
): number[] | null {
  if (contextLength == null || !Number.isFinite(contextLength)) return null;
  if (contextLength <= 128_000) return null;
  const steps: number[] = CONTEXT_BUDGET_LADDER.filter(
    (n) => n < contextLength,
  );
  steps.push(contextLength);
  const unique = [...new Set(steps)];
  return unique.length >= 2 ? unique : null;
}

/** Ring and fold ceiling: the shorter of the assembly step and the model window. */
export function effectiveContextWindow(
  catalogWindow: number | null,
  budget: number | null | undefined,
): number | null {
  if (catalogWindow == null || catalogWindow <= 0) return null;
  if (budget == null || !Number.isFinite(budget) || budget <= 0) {
    return catalogWindow;
  }
  return Math.min(budget, catalogWindow);
}
