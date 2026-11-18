// Alerts and conflicts panel (FR-UI-08): ordering, grouping, acknowledging.

import type { Alert, Priority } from "../types";

const RANK: Record<Priority, number> = { P1: 0, P2: 1, P3: 2, P4: 3 };
export const GROUP_GAP_S = 30 * 60; // alerts of one kind at one place within 30 min are one disruption

export interface AlertGroup {
  parent: Alert; // the most urgent, earliest alert of the group
  children: Alert[];
  open: number; // alerts of the group not yet acknowledged
}

/** Alerts at or above `minPriority`, as groups: P1 first, then by time. Acknowledged ones stay, marked. */
export function alertGroups(alerts: Alert[], minPriority: Priority, acked: ReadonlySet<string>): AlertGroup[] {
  const shown = alerts.filter((a) => RANK[a.priority] <= RANK[minPriority]);
  const byPlace = new Map<string, Alert[]>();
  for (const a of [...shown].sort((x, y) => x.time_s - y.time_s)) {
    const key = `${a.type}/${a.location}`;
    byPlace.set(key, [...(byPlace.get(key) ?? []), a]);
  }
  const groups: Alert[][] = [];
  for (const list of byPlace.values()) {
    let current: Alert[] = [];
    for (const a of list) {
      if (current.length > 0 && a.time_s - current[current.length - 1].time_s > GROUP_GAP_S) {
        groups.push(current);
        current = [];
      }
      current.push(a);
    }
    if (current.length > 0) groups.push(current);
  }
  return groups
    .map((g) => {
      const sorted = [...g].sort((a, b) => RANK[a.priority] - RANK[b.priority] || a.time_s - b.time_s);
      return { parent: sorted[0], children: sorted.slice(1), open: g.filter((a) => !acked.has(a.id)).length };
    })
    .sort((a, b) => RANK[a.parent.priority] - RANK[b.parent.priority] || a.parent.time_s - b.parent.time_s);
}

/** Minutes from `now` to the alert (negative: it was raised that long ago). */
export function leadMin(a: Alert, now: number): number {
  return (a.time_s - now) / 60;
}

/** Acknowledge an alert, or every alert of its group with one action. */
export function acknowledge(acked: ReadonlySet<string>, g: AlertGroup): Set<string> {
  return new Set([...acked, g.parent.id, ...g.children.map((c) => c.id)]);
}

/** Counts by priority of the alerts that are open (raised by `now`, not acknowledged). */
export function openCounts(alerts: Alert[], now: number, acked: ReadonlySet<string>): Record<Priority, number> {
  const out: Record<Priority, number> = { P1: 0, P2: 0, P3: 0, P4: 0 };
  for (const a of alerts) if (a.time_s <= now && !acked.has(a.id)) out[a.priority] += 1;
  return out;
}
