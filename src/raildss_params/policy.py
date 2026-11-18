"""The precedence policy table (OD-04, REC-09): one source for the optimiser objective and KPI A5.

FR-KPI-03: weighted delay (A5) uses the same table as the optimiser objective, so both read the
weights through :func:`weight_of`.
"""

from __future__ import annotations

from raildss_params.register import Register

DEFAULT_CLASS = "freight_other"


def weights(register: Register) -> dict[str, float]:
    return {str(k): float(v) for k, v in register.value("policy.weights").items()}


def weight_of(category: str, register: Register) -> float:
    """Weight of a precedence class; unknown classes take the ``freight_other`` weight."""
    w = weights(register)
    return w.get(category, w[DEFAULT_CLASS])
