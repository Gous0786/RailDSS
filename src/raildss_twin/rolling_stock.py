"""Rolling-stock and consist templates for the twin (issue #15).

The templates live in :mod:`raildss_etl.rolling_stock`, so the network publication can build the
``loco_class``, ``vehicle_type`` and ``consist`` tables without importing the twin (issue #66;
NFR-MNT-02, FR-TWIN-14: the DSS reaches the publication and must never reach the twin).
"""

from raildss_etl.rolling_stock import KMH_PER_MPS, ConsistTemplate, RollingStock, UnmappedWagonTypeError

__all__ = ["KMH_PER_MPS", "ConsistTemplate", "RollingStock", "UnmappedWagonTypeError"]
