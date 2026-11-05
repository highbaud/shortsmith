import { useEffect, useMemo } from "react";

type VisualInputResult<T> = {
  values: T[];
  rejectedIndexes: number[];
  isArray: boolean;
};

export function normalizeVisualInputs<T>(
  input: unknown,
  isValid: (value: unknown) => value is T,
): VisualInputResult<T> {
  const values: T[] = [];
  const rejectedIndexes: number[] = [];
  if (!Array.isArray(input)) return { values, rejectedIndexes, isArray: false };
  for (const [index, value] of input.entries()) {
    if (isValid(value)) values.push(value);
    else rejectedIndexes.push(index);
  }
  return { values, rejectedIndexes, isArray: true };
}

export function reportVisualInputRejections(
  kind: "b-roll" | "VFX",
  input: VisualInputResult<unknown>,
): void {
  if (!input.isArray) console.warn(`Invalid ${kind} payload: expected an array`);
  else if (input.rejectedIndexes.length) {
    const noun = kind === "b-roll" ? "slide" : "event";
    console.warn(`Skipped ${input.rejectedIndexes.length} invalid ${kind} ${noun}(s) at indexes [${input.rejectedIndexes.join(", ")}]`);
  }
}

/** Keep the valid entries of a visual-input array and warn once per change about the rest. */
export function useVisualInputs<T>(
  kind: "b-roll" | "VFX",
  input: unknown,
  isValid: (value: unknown) => value is T,
): T[] {
  const normalized = useMemo(() => normalizeVisualInputs(input, isValid), [input, isValid]);
  useEffect(() => reportVisualInputRejections(kind, normalized), [kind, normalized]);
  return normalized.values;
}
