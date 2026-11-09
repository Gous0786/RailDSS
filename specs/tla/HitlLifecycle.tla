---------------------------- MODULE HitlLifecycle ----------------------------
(***************************************************************************)
(* RailDSS recommendation lifecycle (spec sec. 3.8; FR-VER-13, FR-HITL-01, -02, *)
(* -07; issue #82). The Python implementation is raildss_hitl.lifecycle;   *)
(* tests/hitl/test_conformance.py replays every transition of the state    *)
(* graph TLC dumps from this model against it.                             *)
(*                                                                         *)
(* Bounded world: NRecs recommendations. With SameContext they share one  *)
(* decision context (supersede, one per context); without it each has its *)
(* own context. All act on block 1, so their orders always conflict.      *)
(* "clock" is the time in ticks, "ver" the version of the estimated state  *)
(* S_t (a material state change bumps it); basedOn[r] is the version       *)
(* recommendation r was planned on.                                        *)
(*                                                                         *)
(* Two choices beyond the sec. 3.8 diagram, both made explicit here:           *)
(*  - a new plan for a context also supersedes a recommendation of that    *)
(*    context that is open in the SANDBOX (not only PRESENTED);            *)
(*  - a material state change while orders are in force moves the         *)
(*    recommendation to DIVERGED at once (a new cycle starts, sec. 9.5 item 4).*)
(* SANDBOX --re-verify FAIL--> SANDBOX is a stutter step and not modelled. *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS NRecs, MaxTime, MaxVer, Validity, SameContext

Recs == 1..NRecs
Ctx(r) == IF SameContext THEN 1 ELSE r
Block(c) == 1

Statuses == {"NONE", "PROPOSED", "REJECTED_BY_VERIFIER", "PRESENTED", "SANDBOX",
             "ACCEPTED", "REJECTED", "EXPIRED", "SUPERSEDED", "PRE_EXEC_CHECK",
             "ORDER_ISSUED", "ACKNOWLEDGED", "ORDER_DECLINED", "EXECUTING",
             "COMPLETED", "DIVERGED"}
Open == {"PRESENTED", "SANDBOX"}
Committed == {"ACCEPTED", "PRE_EXEC_CHECK", "ORDER_ISSUED", "ACKNOWLEDGED",
              "ORDER_DECLINED", "EXECUTING"}
Authority == {"ORDER_ISSUED", "ACKNOWLEDGED", "EXECUTING"}
PostDecision == Committed \cup {"COMPLETED", "DIVERGED"}

VARIABLES status, basedOn, validUntil, decided, clock, ver
vars == <<status, basedOn, validUntil, decided, clock, ver>>
recVars == <<basedOn, validUntil, decided, clock, ver>>

Siblings(r) == {s \in Recs \ {r} : Ctx(s) = Ctx(r)}
Rivals(r) == {s \in Recs \ {r} : Block(Ctx(s)) = Block(Ctx(r))}
Free(r) == \A s \in Rivals(r) : status[s] \notin Authority
Set(r, st) == status' = [status EXCEPT ![r] = st]

Init ==
    /\ status = [r \in Recs |-> "NONE"]
    /\ basedOn = [r \in Recs |-> 0]
    /\ validUntil = [r \in Recs |-> 0]
    /\ decided = [r \in Recs |-> FALSE]
    /\ clock = 0
    /\ ver = 0

Tick ==
    /\ clock < MaxTime
    /\ clock' = clock + 1
    /\ UNCHANGED <<status, basedOn, validUntil, decided, ver>>

StateChange ==
    /\ ver < MaxVer
    /\ ver' = ver + 1
    /\ status' = [r \in Recs |-> IF status[r] \in Authority THEN "DIVERGED" ELSE status[r]]
    /\ UNCHANGED <<basedOn, validUntil, decided, clock>>

\* Recommendations of a context are created in id order (symmetry reduction).
Propose(r) ==
    /\ status[r] = "NONE"
    /\ \A s \in Siblings(r) : s < r => status[s] # "NONE"
    /\ Set(r, "PROPOSED")
    /\ basedOn' = [basedOn EXCEPT ![r] = ver]
    /\ validUntil' = [validUntil EXCEPT ![r] = clock + Validity]
    /\ UNCHANGED <<decided, clock, ver>>

VerifyFail(r) ==
    /\ status[r] = "PROPOSED"
    /\ Set(r, "REJECTED_BY_VERIFIER")
    /\ UNCHANGED recVars

\* Verified (PASS / PASS_WITH_WARN). Not while a sibling is committed; an open
\* sibling is superseded (FR-HITL-07).
Present(r) ==
    /\ status[r] = "PROPOSED"
    /\ \A s \in Siblings(r) : status[s] \notin Committed
    /\ status' = [s \in Recs |->
                    IF s = r THEN "PRESENTED"
                    ELSE IF s \in Siblings(r) /\ status[s] \in Open THEN "SUPERSEDED"
                    ELSE status[s]]
    /\ UNCHANGED recVars

Accept(r) ==
    /\ status[r] = "PRESENTED"
    /\ clock < validUntil[r]
    /\ Set(r, "ACCEPTED")
    /\ decided' = [decided EXCEPT ![r] = TRUE]
    /\ UNCHANGED <<basedOn, validUntil, clock, ver>>

Modify(r) == status[r] = "PRESENTED" /\ Set(r, "SANDBOX") /\ UNCHANGED recVars
ReverifyPass(r) == status[r] = "SANDBOX" /\ Set(r, "PRESENTED") /\ UNCHANGED recVars
Reject(r) == status[r] = "PRESENTED" /\ Set(r, "REJECTED") /\ UNCHANGED recVars

Expire(r) ==
    /\ status[r] = "PRESENTED"
    /\ clock >= validUntil[r]
    /\ Set(r, "EXPIRED")
    /\ UNCHANGED recVars

PreExecCheck(r) == status[r] = "ACCEPTED" /\ Set(r, "PRE_EXEC_CHECK") /\ UNCHANGED recVars

PreExecOk(r) ==
    /\ status[r] = "PRE_EXEC_CHECK"
    /\ basedOn[r] = ver
    /\ Free(r)
    /\ Set(r, "ORDER_ISSUED")
    /\ UNCHANGED recVars

\* Stale (the state changed) or another plan holds the block: re-present.
PreExecFail(r) ==
    /\ status[r] = "PRE_EXEC_CHECK"
    /\ (basedOn[r] # ver \/ ~Free(r))
    /\ Set(r, "SUPERSEDED")
    /\ UNCHANGED recVars

Acknowledge(r) == status[r] = "ORDER_ISSUED" /\ Set(r, "ACKNOWLEDGED") /\ UNCHANGED recVars
Decline(r) == status[r] = "ORDER_ISSUED" /\ Set(r, "ORDER_DECLINED") /\ UNCHANGED recVars

\* Re-plan with the declined constraint: a new plan that needs a new decision.
Replan(r) ==
    /\ status[r] = "ORDER_DECLINED"
    /\ Set(r, "PRESENTED")
    /\ basedOn' = [basedOn EXCEPT ![r] = ver]
    /\ validUntil' = [validUntil EXCEPT ![r] = clock + Validity]
    /\ decided' = [decided EXCEPT ![r] = FALSE]
    /\ UNCHANGED <<clock, ver>>

Start(r) == status[r] = "ACKNOWLEDGED" /\ Set(r, "EXECUTING") /\ UNCHANGED recVars
Complete(r) == status[r] = "EXECUTING" /\ Set(r, "COMPLETED") /\ UNCHANGED recVars
Diverge(r) == status[r] = "EXECUTING" /\ Set(r, "DIVERGED") /\ UNCHANGED recVars

Next ==
    \/ Tick
    \/ StateChange
    \/ \E r \in Recs : Propose(r)
    \/ \E r \in Recs : VerifyFail(r)
    \/ \E r \in Recs : Present(r)
    \/ \E r \in Recs : Accept(r)
    \/ \E r \in Recs : Modify(r)
    \/ \E r \in Recs : ReverifyPass(r)
    \/ \E r \in Recs : Reject(r)
    \/ \E r \in Recs : Expire(r)
    \/ \E r \in Recs : PreExecCheck(r)
    \/ \E r \in Recs : PreExecOk(r)
    \/ \E r \in Recs : PreExecFail(r)
    \/ \E r \in Recs : Acknowledge(r)
    \/ \E r \in Recs : Decline(r)
    \/ \E r \in Recs : Replan(r)
    \/ \E r \in Recs : Start(r)
    \/ \E r \in Recs : Complete(r)
    \/ \E r \in Recs : Diverge(r)

Spec ==
    /\ Init
    /\ [][Next]_vars
    /\ \A r \in Recs : WF_vars(PreExecCheck(r)) /\ WF_vars(PreExecOk(r) \/ PreExecFail(r))

-----------------------------------------------------------------------------
(* Invariants (FR-VER-13, FR-HITL-02, FR-HITL-07).                          *)

TypeOK ==
    /\ status \in [Recs -> Statuses]
    /\ basedOn \in [Recs -> 0..MaxVer]
    /\ validUntil \in [Recs -> 0..(MaxTime + Validity)]
    /\ decided \in [Recs -> BOOLEAN]
    /\ clock \in 0..MaxTime
    /\ ver \in 0..MaxVer

\* FR-HITL-02: at most one live recommendation per decision context.
OnePerContext ==
    \A r \in Recs : \A s \in Siblings(r) :
        ~(status[r] \in Open \cup Committed /\ status[s] \in Open \cup Committed)

\* No stale plan is ever executed: orders in force were planned on the current state.
NoStaleExecution == \A r \in Recs : status[r] \in Authority => basedOn[r] = ver

\* Never two conflicting authorities for one block.
NoTwoAuthorities ==
    \A r \in Recs : \A s \in Rivals(r) : ~(status[r] \in Authority /\ status[s] \in Authority)

\* No execution without a controller decision.
NoExecutionWithoutDecision == \A r \in Recs : status[r] \in PostDecision => decided[r]

\* FR-HITL-07: an expired recommendation was never accepted.
ExpiredWasNotDecided == \A r \in Recs : status[r] = "EXPIRED" => ~decided[r]

\* An accepted plan is superseded on a material state change.
StaleAcceptedSuperseded ==
    \A r \in Recs :
        (status[r] \in {"ACCEPTED", "PRE_EXEC_CHECK"} /\ basedOn[r] # ver) ~> (status[r] = "SUPERSEDED")

=============================================================================
