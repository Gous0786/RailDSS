"""DS-9 decision orchestrator: the rolling closed loop (spec DS-9, FR-OPT-07, FR-VER-07; OD-25; issue #84).

* :mod:`raildss_orchestrator.cadence`: when to re-plan and what to freeze (OD-25 parameters).
* :mod:`raildss_orchestrator.model`: candidates, plan contexts and the protocols the loop is built on.
* :mod:`raildss_orchestrator.loop`: the event loop: DSS chain, planner, gates, release, HITL.
* :mod:`raildss_orchestrator.twin`: the twin-backed feed, DSS chain and planner (greedy + CP-SAT, G0-G3).
* ``python -m raildss_orchestrator run``: one real twin day, headless.
"""

__version__ = "0.1.0"
