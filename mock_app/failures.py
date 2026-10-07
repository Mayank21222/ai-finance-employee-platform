"""Failure-injection switches for the mock payables app.

Modes:
  popup         - an overlay popup blocks the add-invoice form until closed
  renamed_field - the due-date input is rendered as #due-date-field, not #due_date
  slow          - add-invoice page and submit are delayed by SLOW_SECONDS
  validation    - amount submissions with commas or currency symbols are rejected
"""

from __future__ import annotations

import os
import time
from typing import Iterable

VALID_MODES = ("popup", "renamed_field", "slow", "validation")
SLOW_SECONDS = float(os.environ.get("MOCK_SLOW_SECONDS", "3"))
_active: set[str] = set()


def set_modes(modes: Iterable[str], replace: bool = True) -> list[str]:
    global _active
    unknown = [m for m in modes if m not in VALID_MODES]
    if unknown:
        raise ValueError(f"unknown failure modes: {unknown}")
    _active = set(modes) if replace else (_active | set(modes))
    return sorted(_active)


def clear() -> list[str]:
    global _active
    _active = set()
    return []


def active() -> list[str]:
    return sorted(_active)


def is_on(mode: str) -> bool:
    return mode in _active


def maybe_slow() -> None:
    if is_on("slow"):
        time.sleep(SLOW_SECONDS)
