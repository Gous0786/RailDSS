"""Independent verification engine (spec §9). M1: G2 rule checker and offline rule auditor (issue #78).

NFR-MNT-02: this package is an independent codebase. It must never import
raildss_optimiser, raildss_twin or raildss_etl; import-linter enforces this in CI.

* :mod:`raildss_verifier.model`: the verifier's own plan IR and its results (FR-VER-11).
* :mod:`raildss_verifier.network`: network facts read from a published network.
* :mod:`raildss_verifier.catalogue`: the rule catalogue ``config/rule_catalogue.yaml`` (FR-VER-04).
* :mod:`raildss_verifier.engine`: G2, PASS / WARN / FAIL per rule with citations (FR-VER-03).
* :mod:`raildss_verifier.auditor`: the same catalogue over ground-truth traces (FR-VER-12).
"""

__version__ = "0.1.0"
