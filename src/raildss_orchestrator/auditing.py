"""What the loop writes to the audit trail (spec FR-AUD-01, -03, -07; issue #85).

Records, in chain order (see :mod:`raildss_audit.chain`):

* ``alert``: a conflict first seen, a data-quality alert, an executor alert.
* ``plan_run``: one planning cycle: everything needed to run it again (the plan context, the state hash and
  version, versions of the register, network and rule catalogue, the seed and solver settings) and the full
  candidate set with its verification results, with a digest of it.
* ``recommendation``: each option shown for a decision: all options of that decision, the state it was built
  on, its verification results and the explanation text (the order phrases), linked to its ``plan_run``.
* ``decision``: accept, reject (reason code) or the declared evaluation policy, with the time to decision
  and what the option changes (KPI delta against the baseline).
* ``order`` and ``ack``: control orders and the station masters' answers. ``outcome``: terminal states.

:func:`completeness` is the FR-AUD-01 check: every PRESENTED recommendation has a full record.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Mapping, Sequence
from importlib import metadata
from typing import Any

from raildss_audit.chain import AuditLog, AuditRecord, canonical
from raildss_hitl.executor import PlanAction
from raildss_hitl.lifecycle import Lifecycle, Status
from raildss_optimiser.greedy import Decision, Hold
from raildss_params import Register
from raildss_twin.sim import Closure

from .model import Candidate, DssView, PlanContext

EVALUATION_POLICY = "evaluation-policy"  # NFR-AUD-02: the declared policy that accepts in headless runs
REQUIRED = (
    "rec_id",
    "plan_run",
    "conflict",
    "options",
    "state",
    "versions",
    "seed",
    "verification",
    "explanation",
)


# --------------------------------------------------------------------------- serialisation
def ctx_json(ctx: PlanContext) -> dict[str, Any]:
    def dec(ds: Sequence[Decision]) -> list[list[Any]]:
        return [[d.run_id, d.station, d.line_no, d.dep_s] for d in ds]

    return {
        "now_s": ctx.now_s,
        "plan_id": ctx.plan_id,
        "triggers": list(ctx.triggers),
        "frozen": dec(ctx.frozen),
        "committed": dec(ctx.committed),
        "history": dec(ctx.history),
        "impose": ctx.impose,
        "closures": [[c.segment, c.line_id, c.start_s, c.end_s] for c in ctx.closures],
        "exit_windows": {k: [list(w) for w in v] for k, v in ctx.exit_windows.items()},
        "commit_holds": [[r, s, t] for (r, s), t in ctx.commit_holds.items()],
        "commit_lines": [[r, s, n] for (r, s), n in ctx.commit_lines.items()],
        "policy_from_s": ctx.policy_from_s,
        "verify": ctx.verify,
        "time_limit_s": ctx.time_limit_s,
    }


def ctx_from_json(d: Mapping[str, Any]) -> PlanContext:
    def dec(rows: Sequence[Sequence[Any]]) -> list[Decision]:
        return [Decision(str(r[0]), str(r[1]), int(r[2]), float(r[3])) for r in rows]

    return PlanContext(
        now_s=float(d["now_s"]),
        plan_id=str(d["plan_id"]),
        triggers=list(d["triggers"]),
        frozen=dec(d["frozen"]),
        committed=dec(d["committed"]),
        history=dec(d["history"]),
        impose=bool(d["impose"]),
        closures=[Closure(str(c[0]), c[1], float(c[2]), float(c[3])) for c in d["closures"]],
        exit_windows={k: [(float(a), float(b)) for a, b in v] for k, v in d["exit_windows"].items()},
        commit_holds={(str(r), str(s)): float(t) for r, s, t in d["commit_holds"]},
        commit_lines={(str(r), str(s)): int(n) for r, s, n in d["commit_lines"]},
        policy_from_s=float(d["policy_from_s"]),
        verify=bool(d["verify"]),
        time_limit_s=float(d["time_limit_s"]),
    )


def candidate_json(c: Candidate) -> dict[str, Any]:
    """The deterministic content of a candidate: its plan and every verification result (no wall times)."""
    return {
        "name": c.name,
        "plan_id": c.plan_id,
        "decisions": [[d.run_id, d.station, d.line_no, d.dep_s] for d in c.decisions],
        "holds": [[h.run_id, h.station, h.until_s, h.held_s] for h in c.holds],
        "solver": c.g1 is not None,
        "g1": c.g1.model_dump(mode="json") if c.g1 is not None else None,
        "g2": c.g2.overall.value if c.g2 is not None else None,
        "g3": c.g3.model_dump(mode="json"),
        "fail": c.fail_rules,
        "warn": c.warn_rules,
    }


def decisions_of(rec: Mapping[str, Any]) -> list[Decision]:
    return [Decision(str(r[0]), str(r[1]), int(r[2]), float(r[3])) for r in rec["decisions"]]


def holds_of(rec: Mapping[str, Any]) -> list[Hold]:
    return [Hold(str(r[0]), str(r[1]), float(r[2]), float(r[3])) for r in rec["holds"]]


def digest_of(candidates: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical(list(candidates)).encode("utf-8")).hexdigest()


def incident_id(conflict: Any) -> str:
    raw = canonical([conflict.type, list(conflict.trains), conflict.resource])
    return "INC-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- the recorder
class AuditRecorder:
    def __init__(
        self,
        log: AuditLog,
        register: Register,
        origin: dt.datetime,
        *,
        day: dt.date,
        solver: Mapping[str, Any] | None = None,
    ) -> None:
        self.log, self.register, self.origin, self.day = log, register, origin, day
        self.solver = dict(solver or {})
        self._runs = 0
        self._alerts = 0
        self._status: dict[str, Status] = {}
        self._catalogue: str | None = None

    def at(self, now_s: float) -> dt.datetime:
        return self.origin + dt.timedelta(seconds=now_s)

    def versions(self, network_version: str) -> dict[str, str]:
        if self._catalogue is None:
            from raildss_verifier import catalogue as cat

            self._catalogue = cat.load().catalogue_version
        try:
            solver = metadata.version("ortools")
        except metadata.PackageNotFoundError:  # pragma: no cover
            solver = "-"
        return {
            "register": self.register.version,
            "network": network_version,
            "rule_catalogue": self._catalogue,
            "ortools": solver,
        }

    # ------------------------------------------------------------------ alerts
    def alert(
        self,
        now_s: float,
        kind: str,
        detail: str,
        priority: str,
        *,
        trains: Sequence[str] = (),
        incident: str | None = None,
    ) -> None:
        self._alerts += 1
        refs: dict[str, Any] = {"trains": sorted(trains)}
        if incident:
            refs["incident"] = incident
        self.log.append(
            "alert",
            {"alert_id": f"ALR-{self._alerts:05d}", "kind": kind, "priority": priority, "detail": detail},
            self.at(now_s),
            actor="dss",
            refs=refs,
        )

    def conflict(self, now_s: float, c: Any) -> None:
        self.alert(
            now_s,
            f"conflict type {c.type}",
            f"{c.priority} on {c.resource} at {c.time.isoformat()}: {', '.join(c.trains)}",
            c.priority,
            trains=c.trains,
            incident=incident_id(c),
        )

    # ------------------------------------------------------------------ plan runs and recommendations
    def plan_run(self, ctx: PlanContext, view: DssView, candidates: Sequence[Candidate]) -> str:
        self._runs += 1
        run_id = f"RUN-{self._runs:04d}"
        cands = [candidate_json(c) for c in candidates]
        reg = self.register
        self.log.append(
            "plan_run",
            {
                "run_id": run_id,
                "day": self.day.isoformat(),
                "context": ctx_json(ctx),
                "state": {"hash": view.state_hash, "version": view.version, "time_s": view.now_s},
                "versions": self.versions(view.snapshot.network_version),
                "seed": int(reg.value("twin.sim")["default_seed"]),
                "solver": self.solver,
                "candidates": cands,
                "digest": digest_of(cands),
            },
            self.at(ctx.now_s),
            actor="orchestrator",
            refs={"run_id": run_id},
        )
        return run_id

    def recommendation(
        self,
        now_s: float,
        *,
        rec_id: str,
        run_id: str,
        conflict: Any,
        rank: int,
        cand: Candidate,
        actions: Sequence[PlanAction],
        shown: Sequence[Mapping[str, Any]],
        view: DssView,
        verdict_warn: Sequence[str],
        valid_until_s: float,
    ) -> None:
        self.log.append(
            "recommendation",
            {
                "rec_id": rec_id,
                "plan_run": run_id,
                "conflict": {
                    "type": conflict.type,
                    "trains": list(conflict.trains),
                    "resource": conflict.resource,
                },
                "rank": rank,
                "candidate": cand.name,
                "options": list(shown),  # every option shown for this decision
                "state": {"hash": view.state_hash, "version": view.version},
                "versions": self.versions(view.snapshot.network_version),
                "seed": int(self.register.value("twin.sim")["default_seed"]),
                "verification": {
                    "fail": cand.fail_rules,
                    "warn": list(verdict_warn),
                    "g3": cand.g3.status,
                    "kpi_delta": cand.delta,
                },
                "explanation": [a.phrase() for a in actions],
                "actions": [a.model_dump(mode="json") for a in actions],
                "valid_until_s": valid_until_s,
            },
            self.at(now_s),
            actor="orchestrator",
            refs={
                "rec_id": rec_id,
                "trains": sorted(conflict.trains),
                "incident": incident_id(conflict),
                "run_id": run_id,
            },
        )

    # ------------------------------------------------------------------ decisions, orders, outcomes
    def decision(
        self,
        now_s: float,
        *,
        rec_id: str,
        action: str,
        actor: str,
        trains: Sequence[str],
        reason: str | None,
        presented_s: float | None,
        diff: Mapping[str, Any],
        status: str,
    ) -> None:
        self.log.append(
            "decision",
            {
                "rec_id": rec_id,
                "action": action,
                "reason": reason,
                "time_to_decision_s": None if presented_s is None else round(now_s - presented_s, 3),
                "diff": dict(diff),
                "status": status,
            },
            self.at(now_s),
            actor=actor,
            refs={"rec_id": rec_id, "trains": sorted(trains)},
        )

    def order(self, now_s: float, payload: Mapping[str, Any]) -> None:
        self.log.append(
            "order",
            dict(payload),
            self.at(now_s),
            actor="executor",
            refs={
                "rec_id": payload.get("recommendation_id"),
                "trains": [payload.get("run_id")],
                "order_id": payload["order_id"],
            },
        )

    def ack(
        self, now_s: float, order_id: str, status: str, reason: str | None, payload: Mapping[str, Any]
    ) -> None:
        self.log.append(
            "ack",
            {"order_id": order_id, "status": status, "reason_code": reason},
            self.at(now_s),
            actor=f"SM/{payload.get('station')}",
            refs={
                "rec_id": payload.get("recommendation_id"),
                "trains": [payload.get("run_id")],
                "order_id": order_id,
            },
        )

    def outcomes(self, now_s: float, lc: Lifecycle, trains: Mapping[str, Sequence[str]]) -> None:
        """Every status a recommendation reaches after it was presented, once (FR-AUD-01 outcomes)."""
        for key, rec in lc.recs.items():
            rec_id = str(key)
            if self._status.get(rec_id) is rec.status:
                continue
            self._status[rec_id] = rec.status
            if rec.status in (Status.PROPOSED, Status.PRESENTED):
                continue
            self.log.append(
                "outcome",
                {"rec_id": rec_id, "status": str(rec.status), "reason": rec.reason},
                self.at(now_s),
                actor="lifecycle",
                refs={"rec_id": rec_id, "trains": sorted(trains.get(rec_id, ()))},
            )

    # ------------------------------------------------------------------ completeness (FR-AUD-01)
    def completeness(self, lc: Lifecycle) -> list[str]:
        """Problems: every recommendation that was PRESENTED must have a full record."""
        have = {r.refs.get("rec_id"): r for r in self.log.query(kind="recommendation")}
        problems = []
        for rec_id, rec in lc.recs.items():
            if Status.PRESENTED not in [s for _, s in rec.history]:
                continue
            row = have.get(rec_id)
            if row is None:
                problems.append(f"{rec_id}: no recommendation record")
                continue
            missing = [k for k in REQUIRED if row.payload.get(k) in (None, "", [], {})]
            if missing:
                problems.append(f"{rec_id}: missing {', '.join(missing)}")
        return problems


def record_of(rows: Sequence[AuditRecord], kind: str, **refs: Any) -> AuditRecord | None:
    for r in rows:
        if r.kind == kind and all(r.refs.get(k) == v for k, v in refs.items()):
            return r
    return None
