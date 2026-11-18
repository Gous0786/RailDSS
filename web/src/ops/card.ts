// Recommendation card (FR-UI-09, FR-HITL-12, FR-HITL-13, OD-04): what the card shows and when Accept is enabled.

import type { Option } from "../types";

export const TOP_AFFECTED = 3; // FR-HITL-13

/** The three most affected trains (weighted), shown before Accept is enabled. */
export function topAffected(option: Option) {
  return [...option.impacts].sort((a, b) => b.weight * Math.abs(b.delta_s) - a.weight * Math.abs(a.delta_s)).slice(0, TOP_AFFECTED);
}

/**
 * Accept is enabled once the card shows the most affected trains (information placement, no artificial
 * delay) and the option passed the verifier. A failed option can be modified or rejected, never accepted.
 */
export function acceptState(option: Option, cardShown: boolean, expired = false): { enabled: boolean; reason: string } {
  if (!cardShown) return { enabled: false, reason: "open the card first" };
  if (expired) return { enabled: false, reason: "the recommendation has expired" };
  if (option.verification.status !== "PASS") return { enabled: false, reason: "the verifier did not pass this option" };
  if (topAffected(option).length === 0 && option.impacts.length > 0) return { enabled: false, reason: "no affected trains to show" };
  return { enabled: true, reason: "" };
}

/** Seconds left before the card expires (never negative). */
export function expiresIn(validUntilS: number, now: number): number {
  return Math.max(0, validUntilS - now);
}

/** `mm:ss` countdown, or `h:mm:ss` beyond an hour. */
export function countdown(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

/** The class weights an explanation lists, only the classes of the affected trains first (OD-04). */
export function weightsUsed(option: Option, all: Record<string, number>): [string, number][] {
  const used = new Set(option.impacts.map((i) => i.class));
  return Object.entries(all).sort((a, b) => Number(used.has(b[0])) - Number(used.has(a[0])) || b[1] - a[1]);
}

/** Signed minutes for an impact, e.g. `+15.5 min`. */
export function signedMin(seconds: number): string {
  const m = seconds / 60;
  return `${m >= 0 ? "+" : "−"}${Math.abs(m).toFixed(1)} min`;
}
