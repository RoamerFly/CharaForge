import { useEffect, useRef } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { PromptParts } from "./cloud-generation";

export const RESOURCE_FIELDS = ["identity", "pose", "scene", "quality"] as const;
type ResourceField = typeof RESOURCE_FIELDS[number];
export type PromptBinding = { key: string; text: string };
export type PromptBindings = Record<ResourceField, PromptBinding>;
export type BindingState = Partial<PromptBindings>;

/** Keep an edited module with its resource ID when switching resources. */
export function reconcilePrompts(
  parts: PromptParts,
  previous: BindingState,
  incoming: PromptBindings,
  drafts: Map<string, string>,
): PromptParts {
  const next = { ...parts };
  for (const field of RESOURCE_FIELDS) {
    const before = previous[field];
    const after = incoming[field];
    if (before?.key !== after.key) {
      if (before) drafts.set(before.key, parts[field]);
      next[field] = drafts.get(after.key) ?? after.text;
    } else if (parts[field] === before.text && after.text !== before.text) {
      next[field] = after.text;
    }
  }
  return next;
}

export function useResourcePrompts(
  parts: PromptParts,
  setParts: Dispatch<SetStateAction<PromptParts>>,
  bindings: PromptBindings,
) {
  const active = useRef<BindingState>({});
  const drafts = useRef(new Map<string, string>());
  useEffect(() => {
    const next = reconcilePrompts(parts, active.current, bindings, drafts.current);
    active.current = bindings;
    if (RESOURCE_FIELDS.some(field => next[field] !== parts[field])) setParts(next);
  }, [parts, bindings, setParts]);

  const modified = RESOURCE_FIELDS.filter(field => parts[field] !== bindings[field].text);
  const reset = () => {
    const next = { ...parts };
    for (const field of RESOURCE_FIELDS) {
      drafts.current.delete(bindings[field].key);
      next[field] = bindings[field].text;
    }
    setParts(next);
  };
  return { modified, reset };
}
