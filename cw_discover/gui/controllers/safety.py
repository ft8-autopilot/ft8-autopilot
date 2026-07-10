"""Biztonsági GUI döntések — trip feloldás és újraaktiválás logika."""
from __future__ import annotations

from dataclasses import dataclass

from cw_discover.ft8.safety_manager import SafetySnapshot, mark_reactivated


def esp_trip_reason_unlockable(reason: str) -> bool:
  """ESP LOCK / SAFETY_LOCK jellegű trip feloldható RESUME-val."""
  upper = (reason or "").upper()
  return "ESP" in upper or "LOCK" in upper


@dataclass(frozen=True)
class SafetyReactivatePlan:
  watchdog: bool
  line_guard: bool
  mcu: bool
  clear_trip: bool


def plan_reactivate_all(
  snap: SafetySnapshot,
  *,
  watchdog: bool,
  line_guard: bool,
  mcu: bool = True,
) -> SafetyReactivatePlan:
  return SafetyReactivatePlan(
    watchdog=watchdog,
    line_guard=line_guard,
    mcu=mcu,
    clear_trip=True,
  )


def plan_esp_unlock_reactivate(
  snap: SafetySnapshot,
  *,
  watchdog: bool,
  line_guard: bool,
) -> SafetyReactivatePlan | None:
  if not snap.tripped or not esp_trip_reason_unlockable(snap.reason):
    return None
  return SafetyReactivatePlan(
    watchdog=watchdog,
    line_guard=line_guard,
    mcu=True,
    clear_trip=True,
  )


def apply_reactivate_plan(
  snap: SafetySnapshot,
  plan: SafetyReactivatePlan,
) -> SafetySnapshot:
  if plan.clear_trip:
    mark_reactivated(
      snap,
      watchdog=plan.watchdog,
      line_guard=plan.line_guard,
      mcu=plan.mcu,
    )
  return snap
