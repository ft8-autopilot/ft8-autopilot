"""Villogó riasztó gombok stílusai — ESP / line-in."""
from __future__ import annotations


def linein_alert_stylesheet(highlight: bool) -> str:
  if highlight:
    return (
      "QPushButton { background-color: #9e6a03; color: #ffffff; font-weight: bold; "
      "padding: 4px 12px; border-radius: 4px; border: 2px solid #f0883e; }"
    )
  return (
    "QPushButton { background-color: #3d1300; color: #f0883e; font-weight: bold; "
    "padding: 4px 12px; border-radius: 4px; border: 1px solid #9e6a03; }"
  )


def esp_alert_stylesheet(highlight: bool) -> str:
  if highlight:
    return (
      "QPushButton { background-color: #a40e26; color: #ffffff; font-weight: bold; "
      "padding: 4px 12px; border-radius: 4px; border: 2px solid #f85149; }"
    )
  return (
    "QPushButton { background-color: #3d1320; color: #ff7b72; font-weight: bold; "
    "padding: 4px 12px; border-radius: 4px; border: 1px solid #a40e26; }"
  )


def tx_indicator_stylesheet(active: bool) -> str:
  if active:
    return (
      "QPushButton { background-color: #b62324; color: #ffffff; font-weight: bold; "
      "padding: 4px 14px; border-radius: 4px; border: 1px solid #f85149; }"
    )
  return (
    "QPushButton { background-color: #21262d; color: #8b949e; padding: 4px 14px; "
    "border-radius: 4px; border: 1px solid #30363d; }"
  )
