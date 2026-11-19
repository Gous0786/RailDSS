"""The audit log: append-only JSON lines, hash-chained per section and day, head anchored daily.

Each record carries the hash of its predecessor and its own SHA-256 over the canonical JSON of all other
fields, so changing, inserting, deleting or reordering a record breaks :meth:`AuditLog.verify` (FR-AUD-02).
The first record of a day links to the last record of the previous day. :meth:`AuditLog.anchor` writes a
day's head (and record count) to a separate anchor file, a stand-in for the daily anchoring outside the log
store; it also exposes truncation and a rewritten tail. Controllers appear only as pseudonyms (FR-AUD-05).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

GENESIS = "0" * 64
KINDS = (
    "alert",
    "plan_run",
    "recommendation",
    "decision",
    "order",
    "ack",
    "outcome",
    "config_change",
)
PSEUDONYM_LEN = 12


class AuditRecord(BaseModel):
    seq: int  # position in the day's chain
    section: str
    day: str  # ISO date of ``time`` (UTC)
    time: str  # ISO time of the event (simulated or wall clock)
    kind: str
    actor: str | None = None  # pseudonymised controller, a system component or the evaluation policy
    refs: dict[str, Any] = Field(default_factory=dict)  # trains, rec_id, incident, ... for queries
    payload: dict[str, Any] = Field(default_factory=dict)
    prev: str
    hash: str = ""


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(record: AuditRecord) -> str:
    body = record.model_dump(exclude={"hash"})
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def pseudonym(name: str, salt: str) -> str:
    """A stable pseudonym of a controller or approver; the name itself is never stored."""
    return "ctl-" + hmac.new(salt.encode(), name.encode(), hashlib.sha256).hexdigest()[:PSEUDONYM_LEN]


class Verification(BaseModel):
    ok: bool
    records: int
    days: int
    problems: list[str] = Field(default_factory=list)


class AuditLog:
    """One section's audit trail under ``root``: ``<section>/<day>.jsonl`` and ``<section>/anchors.json``.

    ``root=None`` keeps everything in memory (tests, replay of an exported log)."""

    def __init__(self, root: Path | str | None, section: str, *, salt: str = "raildss") -> None:
        self.root = Path(root) if root is not None else None
        self.section, self.salt = section, salt
        self._days: dict[str, list[AuditRecord]] = {}
        self._anchors: dict[str, dict[str, Any]] = {}
        if self.root is not None:
            self._load()

    # ------------------------------------------------------------------ storage
    @property
    def _dir(self) -> Path | None:
        return self.root / self.section if self.root is not None else None

    def _load(self) -> None:
        d = self._dir
        if d is None or not d.exists():
            return
        for path in sorted(d.glob("*.jsonl")):
            self._days[path.stem] = [
                AuditRecord.model_validate_json(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        anchors = d / "anchors.json"
        if anchors.exists():
            self._anchors = json.loads(anchors.read_text(encoding="utf-8"))

    def days(self) -> list[str]:
        return sorted(self._days)

    def records(self, day: str | None = None) -> list[AuditRecord]:
        if day is not None:
            return list(self._days.get(day, []))
        return [r for d in self.days() for r in self._days[d]]

    def __len__(self) -> int:
        return sum(len(v) for v in self._days.values())

    # ------------------------------------------------------------------ append
    def _head(self, day: str) -> str:
        rows = self._days.get(day)
        if rows:
            return rows[-1].hash
        earlier = [d for d in self.days() if d < day and self._days[d]]
        return self._days[earlier[-1]][-1].hash if earlier else GENESIS

    def append(
        self,
        kind: str,
        payload: dict[str, Any],
        time: dt.datetime,
        *,
        actor: str | None = None,
        refs: dict[str, Any] | None = None,
    ) -> AuditRecord:
        if kind not in KINDS:
            raise ValueError(f"unknown audit record kind {kind!r}")
        day = time.astimezone(dt.UTC).date().isoformat()
        prev = self._head(day)
        rows = self._days.setdefault(day, [])
        rec = AuditRecord(
            seq=len(rows),
            section=self.section,
            day=day,
            time=time.astimezone(dt.UTC).isoformat(),
            kind=kind,
            actor=actor,
            refs=json.loads(canonical(refs or {})),
            payload=json.loads(canonical(payload)),
            prev=prev,
        )
        rec.hash = digest(rec)
        rows.append(rec)
        d = self._dir
        if d is not None:
            d.mkdir(parents=True, exist_ok=True)
            with (d / f"{day}.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                f.write(rec.model_dump_json() + "\n")
        return rec

    def config_change(
        self,
        item: str,
        key: str,
        before: Any,
        after: Any,
        approvers: Iterable[str],
        time: dt.datetime,
        *,
        by: str,
    ) -> AuditRecord:
        """FR-AUD-07: a policy, rule-catalogue, threshold or user change, with before/after and approvers."""
        return self.append(
            "config_change",
            {
                "item": item,
                "key": key,
                "before": before,
                "after": after,
                "approvers": [pseudonym(a, self.salt) for a in approvers],
            },
            time,
            actor=pseudonym(by, self.salt),
            refs={"config_key": key},
        )

    # ------------------------------------------------------------------ anchors and verification
    def anchor(self, day: str) -> dict[str, Any]:
        """Anchor the day's chain head and record count (daily, FR-AUD-02)."""
        rows = self._days.get(day, [])
        if not rows:
            raise ValueError(f"no records on {day}")
        self._anchors[day] = {"head": rows[-1].hash, "count": len(rows)}
        d = self._dir
        if d is not None:
            d.mkdir(parents=True, exist_ok=True)
            (d / "anchors.json").write_text(canonical(self._anchors) + "\n", encoding="utf-8", newline="\n")
        return self._anchors[day]

    def verify(self) -> Verification:
        problems: list[str] = []
        prev = GENESIS
        total = 0
        for day in self.days():
            rows = self._days[day]
            for n, rec in enumerate(rows):
                total += 1
                where = f"{day} #{n}"
                if rec.seq != n or rec.day != day or rec.section != self.section:
                    problems.append(f"{where}: position or day changed")
                if rec.prev != prev:
                    problems.append(f"{where}: does not follow its predecessor")
                if digest(rec) != rec.hash:
                    problems.append(f"{where}: content does not match its hash")
                prev = rec.hash
            anchor = self._anchors.get(day)
            if anchor and rows and (anchor["count"] != len(rows) or anchor["head"] != rows[-1].hash):
                problems.append(f"{day}: chain head differs from the anchor")
        problems += [f"{d}: anchored day has no records" for d in self._anchors if d not in self._days]
        return Verification(ok=not problems, records=total, days=len(self._days), problems=problems)

    # ------------------------------------------------------------------ queries (FR-AUD-05)
    def query(
        self,
        *,
        kind: str | None = None,
        train: str | None = None,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
        controller: str | None = None,
        incident: str | None = None,
        rec_id: str | None = None,
    ) -> list[AuditRecord]:
        """Records matching every given filter, in chain order. ``controller`` is the name or pseudonym."""
        who = None
        if controller is not None:
            who = controller if controller.startswith("ctl-") else pseudonym(controller, self.salt)
        lo = since.astimezone(dt.UTC).isoformat() if since else None
        hi = until.astimezone(dt.UTC).isoformat() if until else None
        days = self.days()
        if since:
            days = [d for d in days if d >= since.astimezone(dt.UTC).date().isoformat()]
        if until:
            days = [d for d in days if d <= until.astimezone(dt.UTC).date().isoformat()]
        return list(self._scan(days, kind, train, lo, hi, who, incident, rec_id))

    def _scan(
        self,
        days: list[str],
        kind: str | None,
        train: str | None,
        lo: str | None,
        hi: str | None,
        who: str | None,
        incident: str | None,
        rec_id: str | None,
    ) -> Iterator[AuditRecord]:
        for day in days:
            for r in self._days[day]:
                if kind and r.kind != kind:
                    continue
                if lo and r.time < lo:
                    continue
                if hi and r.time > hi:
                    continue
                if who and r.actor != who:
                    continue
                if train and train not in r.refs.get("trains", ()):
                    continue
                if incident and r.refs.get("incident") != incident:
                    continue
                if rec_id and r.refs.get("rec_id") != rec_id:
                    continue
                yield r
