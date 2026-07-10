from cw_discover.ft8.esp_link_guard import EspLinkGuard


def test_detects_disconnect_and_restored() -> None:
  guard = EspLinkGuard(retry_sec=2.0)

  s1 = guard.observe(ping_ok=True, tx_active=False, now_mono=10.0)
  assert not s1.link_down
  assert not s1.just_went_down
  assert not s1.just_restored

  s2 = guard.observe(ping_ok=False, tx_active=False, now_mono=11.0)
  assert s2.link_down
  assert s2.just_went_down
  assert s2.should_try_recover

  s3 = guard.observe(ping_ok=True, tx_active=False, now_mono=12.0)
  assert not s3.link_down
  assert s3.just_restored


def test_recover_retry_window_is_rate_limited() -> None:
  guard = EspLinkGuard(retry_sec=2.0)

  first = guard.observe(ping_ok=False, tx_active=False, now_mono=100.0)
  second = guard.observe(ping_ok=False, tx_active=False, now_mono=101.0)
  third = guard.observe(ping_ok=False, tx_active=False, now_mono=102.2)

  assert first.should_try_recover
  assert not second.should_try_recover
  assert third.should_try_recover


def test_skips_state_changes_during_tx() -> None:
  guard = EspLinkGuard(retry_sec=2.0)

  tx_step = guard.observe(ping_ok=False, tx_active=True, now_mono=5.0)
  assert not tx_step.link_down
  assert not tx_step.just_went_down
  assert not tx_step.should_try_recover
