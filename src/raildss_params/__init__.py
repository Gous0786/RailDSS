"""Parameter register loader.

Every rule value is configuration with a cited source (spec C-05, Appendix B; issue #3).

Typical use::

    from raildss_params import get, value

    spacing = value("abs.subblock_spacing_km.ktv_vzm")       # 1.0
    psa = get("station_line.csr.psa")                         # Parameter with value + bounds
    psa.value["loop"], psa.bounds.low["loop"], psa.bounds.high["loop"]
"""

from raildss_params.register import (
    Bounds,
    Parameter,
    Register,
    RegisterError,
    UnknownParameterError,
    default_path,
    get,
    load,
    value,
)

__version__ = "0.1.0"

__all__ = [
    "Bounds",
    "Parameter",
    "Register",
    "RegisterError",
    "UnknownParameterError",
    "__version__",
    "default_path",
    "get",
    "load",
    "value",
]
