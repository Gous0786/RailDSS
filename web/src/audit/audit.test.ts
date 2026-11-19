import { describe, expect, it } from "vitest";
import { type AuditRecord, NO_AUDIT_FILTER, cardOf, chartTime, kinds, matches, span, summary, visible } from "./audit";

// 02:30 UTC is 08:00 IST: chart second 28800.
const rec = (seq: number, minute: number, kind: string, refs: AuditRecord["refs"] = {}, payload = {}): AuditRecord => ({
  seq,
  section: "KTV-PSA",
  day: "2025-08-20",
  time: `2025-08-20T02:${String(30 + minute).padStart(2, "0")}:00+00:00`,
  kind,
  actor: kind === "decision" ? "ctl-abc" : "dss",
  refs,
  payload,
  prev: "",
  hash: "",
});

const records = [
  rec(0, 0, "alert", { trains: ["12503@2025-08-19"], incident: "INC-1" }, { priority: "P2", detail: "conflict on NWP" }),
  rec(1, 1, "recommendation", { trains: ["12503@2025-08-19"], rec_id: "REC-0001", incident: "INC-1" }, {
    rec_id: "REC-0001",
    rank: 1,
    candidate: "greedy",
    explanation: ["SM/NWP: hold 12503 until 08:18"],
    options: [{ rec_id: "REC-0001", rank: 1, candidate: "greedy", explanation: [] }],
    state: { hash: "abcdef0123456789" },
    versions: { register: "2026.11.1" },
    seed: 0,
    verification: { fail: [], warn: [], g3: "PASS" },
  }),
  rec(2, 2, "decision", { rec_id: "REC-0001" }, { rec_id: "REC-0001", action: "accept", reason: "evaluation policy", time_to_decision_s: 0 }),
];

describe("audit and replay view (FR-UI-13)", () => {
  it("maps record times to chart seconds (IST)", () => {
    expect(chartTime(records[0])).toBe(8 * 3600);
    expect(chartTime(records[2])).toBe(8 * 3600 + 120);
  });

  it("scrubs the timeline: the log as it stood at a time", () => {
    expect(visible(records, NO_AUDIT_FILTER, 8 * 3600 + 30).map((r) => r.seq)).toEqual([0]);
    expect(visible(records, NO_AUDIT_FILTER, 8 * 3600 + 60).map((r) => r.seq)).toEqual([0, 1]);
    expect(visible(records, NO_AUDIT_FILTER, 9 * 3600)).toHaveLength(3);
    expect(span(records)).toEqual([8 * 3600, 8 * 3600 + 120]);
  });

  it("filters by kind and by train, recommendation, incident or controller", () => {
    expect(records.filter((r) => matches(r, { kind: "decision", text: "" }))).toHaveLength(1);
    expect(records.filter((r) => matches(r, { kind: "", text: "12503" }))).toHaveLength(2);
    expect(records.filter((r) => matches(r, { kind: "", text: "rec-0001" }))).toHaveLength(2);
    expect(records.filter((r) => matches(r, { kind: "", text: "INC-1" }))).toHaveLength(2);
    expect(records.filter((r) => matches(r, { kind: "", text: "ctl-abc" }))).toHaveLength(1);
    expect(kinds(records)).toEqual(["alert", "decision", "recommendation"]);
  });

  it("summarises records and rebuilds the recommendation card as it was shown", () => {
    expect(summary(records[0])).toBe("P2 conflict on NWP");
    expect(summary(records[2])).toContain("REC-0001 accept by ctl-abc");
    const card = cardOf(records[1]);
    expect(card.lines).toEqual(["SM/NWP: hold 12503 until 08:18"]);
    expect(card.stateHash).toBe("abcdef0123456789");
    expect(card.verification.g3).toBe("PASS");
  });
});
