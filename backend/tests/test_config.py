"""Config parsing tests (no app boot needed)."""
import pytest
from app.config import Settings


def test_sync_combos_default_has_us_ios():
    s = Settings()
    combos = s.sync_combos_list
    assert ("US", "ios") in combos


def test_sync_combos_parses_multi():
    s = Settings(SYNC_RANKING_COMBOS="US:ios, JP:android,KR:ios")
    assert s.sync_combos_list == [("US", "ios"), ("JP", "android"), ("KR", "ios")]


def test_sync_combos_dedupes():
    s = Settings(SYNC_RANKING_COMBOS="US:ios,US:ios,US:IOS")
    assert s.sync_combos_list == [("US", "ios")]


def test_sync_combos_skips_malformed():
    """逗号分隔的坏 token 应该被静默跳过而不是炸掉整个 scheduler。"""
    s = Settings(SYNC_RANKING_COMBOS="US:ios,bogus,:ios,US:,US:windows,US:ANDROID")
    assert s.sync_combos_list == [("US", "ios"), ("US", "android")]


def test_sync_combos_empty_returns_empty_list():
    s = Settings(SYNC_RANKING_COMBOS="")
    assert s.sync_combos_list == []
