"""Small shared helpers."""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timezone


def opt(mapping, field, default=None):
    """Read an optional field. An empty value counts as absent."""
    if not isinstance(mapping, dict):
        return default
    value = mapping.get(field)
    if value is None or (isinstance(value, (str, list, tuple, dict)) and len(value) == 0):
        return default
    return value


def to_list(value) -> list:
    """Normalise a list that a file may hold as a single item or nothing."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def is_nan(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def number(value, default=math.nan) -> float:
    """A numeric field, where unset may be stored as None or NaN."""
    if value is None or value == "" or value == []:
        return default
    return float(value)


def sha256(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def iso_time(t) -> str:
    """Format POSIX seconds as a UTC ISO-8601 string with milliseconds."""
    if is_nan(t):
        return ""
    moment = datetime.fromtimestamp(t, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def new_id(prefix: str) -> str:
    """Sortable identifier that is also a valid file and module name."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}_{stamp}_{random.randrange(65536):04X}"
