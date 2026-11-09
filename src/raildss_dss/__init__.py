"""DSS online services (spec §4, DS-1 to DS-16). M1: DS-1 ingestion and data quality (issue #70).

:mod:`raildss_dss.ingest` consumes the ``obs.*`` and ``ops.*`` feeds. The DSS never reads ground truth
(``gt.*``, FR-TWIN-14) and does not import the twin.
"""

__version__ = "0.1.0"
