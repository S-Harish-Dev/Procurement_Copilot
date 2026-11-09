// Fixed entity → categorical slot. Colour follows the entity everywhere (never its rank), so a
// layer, provider or budget part looks the same on every chart and survives filtering.

export const slot = (n: number) => `var(--series-${n})`;
export const OTHER = "var(--other)";

const ENTITY: Record<string, number> = {
  // Guard layers
  regex: 1,
  jev: 2,
  classifier: 3,
  ensemble: 4,
  documents: 5,
  output: 7,
  action_integrity: 8,
  // Cache layers
  l1: 1,
  l2: 2,
  semantic: 3,
  miss: 0,
  // Cost kinds
  actual: 1,
  shadow: 2,
  // Router tiers / intents
  cheap: 1,
  expensive: 2,
  policy_qa: 1,
  data_lookup: 3,
  create_request: 4,
  approve: 5,
  out_of_scope: 0,
  // Budget parts (part-to-whole): spent, committed, remaining
  spent: 1,
  committed: 2,
  remaining: 0,
  // Providers (fallback order Groq → Gemini → OpenRouter, then the offline mocks)
  groq: 1,
  gemini: 2,
  openrouter: 3,
  // Offline mocks take the slots of the real providers they stand in for (primary, secondary).
  "mock-primary": 1,
  "mock-secondary": 2,
};

/** Colour for a known entity; unknown ones fold into the neutral "other". */
export function colorFor(entity: string): string {
  const n = ENTITY[entity.toLowerCase()];
  return n ? slot(n) : OTHER;
}
