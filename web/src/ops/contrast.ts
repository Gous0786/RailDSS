// Contrasting options on the recommendation card and the 'why not' answers (FR-UI-09, FR-OPT-05/06; issue #174).
// Reads the option records of the export (up to three options of one decision, optionally with who they serve
// first) and the re-solves of the loop (`ContrastRecord` of issue #154). Everything beyond the plain option
// record is optional: a decision with one precedence, or an export without re-solves, is shown as such.

import type { Option, Preview, WhyNot } from "../types";

export const MAX_OPTIONS = 3; // the card shows up to three options (OD-04 / FR-OPT-05)

/** The option a number key selects (1-3) while the card is open and shows `n` options, else null. */
export function optionForKey(key: string, cardOpen: boolean, n: number): number | null {
  if (!cardOpen || !/^[1-3]$/.test(key)) return null;
  const i = Number(key) - 1;
  return i < Math.min(n, MAX_OPTIONS) ? i : null;
}

export interface ContrastRow {
  key: number; // the keyboard shortcut, 1-based
  id: string;
  title: string;
  delta_min: number; // weighted delay against the baseline (negative: better)
  later: number;
  earlier: number;
  verifier: "PASS" | "FAIL";
  first: string | null; // the train it serves first at the decision's resource, when the export says
  onlyHere: string[]; // orders no other option has
  sameAs: string | null; // the option with exactly the same orders
}

/** The contrast of the (up to three) options: what each costs and what only it orders. */
export function contrastRows(options: Option[]): ContrastRow[] {
  const shown = options.slice(0, MAX_OPTIONS);
  return shown.map((o, i) => {
    const others = shown.filter((_, j) => j !== i);
    const elsewhere = new Set(others.flatMap((x) => x.orders));
    const same = others.find((x) => x.orders.length === o.orders.length && o.orders.every((a) => x.orders.includes(a)));
    return {
      key: i + 1,
      id: o.id,
      title: o.title,
      delta_min: o.weighted_delay_delta_min,
      later: o.impacts.filter((x) => x.delta_s > 0).length,
      earlier: o.impacts.filter((x) => x.delta_s < 0).length,
      verifier: o.verification.status,
      first: o.first ?? null,
      onlyHere: o.orders.filter((a) => !elsewhere.has(a)),
      sameAs: same ? same.id : null,
    };
  });
}

/** How many different trains the options serve first (null when the export does not say). */
export function precedences(options: Option[]): number | null {
  const firsts = options.map((o) => o.first).filter((f): f is string => !!f);
  return firsts.length === 0 ? null : new Set(firsts).size;
}

/** One line on what the options contrast: precedences when known, else timing only, and the single-option case. */
export function contrastNote(p: Preview): string {
  const n = Math.min(p.options.length, MAX_OPTIONS);
  if (n <= 1) return "One option only: nothing to contrast.";
  const k = p.decision?.precedences ?? precedences(p.options);
  if (k === null) return `${n} options.`;
  if (k <= 1) return `${n} options, one precedence: they differ in timing, not in who goes first.`;
  return `${n} options, ${k} different precedences: they differ in who goes first.`;
}

/** The re-solves ('why not X first?') of the export, the one for `first` first when given. */
export function whyNotList(p: Preview, first?: string): WhyNot[] {
  const all = p.why_not ?? [];
  return first ? all.filter((w) => w.first === first) : all;
}

/** The answer of one re-solve as the card shows it. */
export function whyNotText(w: WhyNot): string {
  if (!w.verified) return `${w.first} first at ${w.resource}: not possible (${w.fail.join(", ") || "rules"})`;
  if (!w.reversed) return `${w.first} first at ${w.resource}: the best plan keeps this order already`;
  const cost = w.cost_min === null ? "" : `, ${w.cost_min >= 0 ? "+" : "−"}${Math.abs(w.cost_min).toFixed(1)} passenger delay-min against the best plan`;
  return `${w.first} first at ${w.resource}: possible and verified${cost}`;
}

export type WhyNotState = "possible" | "impossible" | "same";

export function whyNotState(w: WhyNot): WhyNotState {
  if (!w.verified) return "impossible";
  return w.reversed ? "possible" : "same";
}
