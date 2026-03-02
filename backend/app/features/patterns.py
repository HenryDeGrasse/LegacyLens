"""Pattern detection: find routines matching SPICE coding patterns."""

from __future__ import annotations

import json
from pathlib import Path

from app.ingestion.call_graph import load_call_graph


AVAILABLE_PATTERNS = [
    "error_handling",
    "kernel_loading",
    "spk_operations",
    "frame_transforms",
    "time_conversion",
    "geometry",
    "matrix_vector",
    "file_io",
]

PATTERN_DESCRIPTIONS = {
    "error_handling": "Error handling routines using CHKIN/CHKOUT/SIGERR/SETMSG",
    "kernel_loading": "Kernel management routines using FURNSH/UNLOAD/KCLEAR/LDPOOL",
    "spk_operations": "SPK ephemeris operations using SPKEZ/SPKEZR/SPKPOS/SPKGEO",
    "frame_transforms": "Reference frame transformations using FRMCHG/NAMFRM/SXFORM/PXFORM",
    "time_conversion": "Time conversion routines using STR2ET/ET2UTC/TIMOUT/UNITIM",
    "geometry": "Geometry computations using SUBPNT/SINCPT/ILLUMF/TANGPT/TERMPT",
    "matrix_vector": "Matrix/vector operations using MXV/VCRSS/VNORM/VDOT/ROTATE",
    "file_io": "File I/O operations using DAFOPR/DAFCLS/TXTOPN/WRITLN/READLN",
}


def list_patterns() -> list[dict]:
    """List all available SPICE patterns."""
    return [
        {"name": p, "description": PATTERN_DESCRIPTIONS.get(p, "")}
        for p in AVAILABLE_PATTERNS
    ]
