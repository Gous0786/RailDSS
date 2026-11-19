"""Append-only, hash-chained audit trail and queries (spec FR-AUD-01, -02, -05, -07; issue #85).

* :mod:`raildss_audit.chain`: the tamper-evident log (one chain per section and day, head anchored daily),
  its verification and the audit queries.
* ``python -m raildss_audit verify|query|export``: check a log, query it, export it for the replay view.

What is recorded and how a recommendation is replayed lives with the loop that produces them
(``raildss_orchestrator.auditing``, ``raildss_orchestrator.replay``).
"""

__version__ = "0.1.0"
