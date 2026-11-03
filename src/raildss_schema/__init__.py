"""Canonical data model, micro/meso/macro layers (spec §6.1; issue #4).

Entities are Pydantic models (``raildss_schema.entities``). The PostgreSQL / PostGIS DDL,
migrations and JSON Schemas are generated from them (``python -m raildss_schema generate``).
"""

from raildss_schema.base import VERSION_STAMPS, Location, Stamped, UtcDatetime
from raildss_schema.entities import ENTITIES

__version__ = "0.1.0"

__all__ = ["ENTITIES", "VERSION_STAMPS", "Location", "Stamped", "UtcDatetime", "__version__"]
