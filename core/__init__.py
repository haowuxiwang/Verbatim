"""Core (UI-independent) domain logic for Verbatim.

V1: Data models are defined in :mod:`core.models`.
"""

from .models import (  # noqa: F401
    RGB,
    BBox,
    CharData,
    DiffOp,
    DiffOpType,
    PageData,
    RegionData,
    StyleFlags,
)
