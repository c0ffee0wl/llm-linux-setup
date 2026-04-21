"""Tests for find_cast_file multiplexer-aware selection."""

import os

from llm_tools_context.core import find_cast_file


def _touch(path, mtime):
    path.write_text("")
    os.utime(path, (mtime, mtime))


def _clear_env(monkeypatch):
    for var in ("SESSION_LOG_FILE", "SESSION_LOG_DIR", "TMUX_PANE", "STY", "WINDOW"):
        monkeypatch.delenv(var, raising=False)


def test_returns_session_log_file_when_set(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    cast = tmp_path / "2025-01-01_00-00-00-000_12345.cast"
    cast.write_text("")
    monkeypatch.setenv("SESSION_LOG_FILE", str(cast))

    assert find_cast_file() == str(cast)


def test_plain_shell_falls_back_to_newest(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path))
    old = tmp_path / "old_1.cast"
    new = tmp_path / "new_2.cast"
    _touch(old, 1000)
    _touch(new, 2000)

    assert find_cast_file() == str(new)


def test_tmux_filters_by_pane_suffix(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("TMUX_PANE", "%3")
    pane0 = tmp_path / "2025-01-01_00-00-00-000_11111_tmux0.cast"
    pane3 = tmp_path / "2025-01-01_00-00-00-000_22222_tmux3.cast"
    _touch(pane0, 2000)  # newer but wrong pane
    _touch(pane3, 1000)  # older but our pane

    assert find_cast_file() == str(pane3)


def test_tmux_returns_none_when_no_matching_file(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("TMUX_PANE", "%7")
    other = tmp_path / "2025-01-01_00-00-00-000_11111_tmux0.cast"
    _touch(other, 2000)

    # Must NOT return another pane's file just because it's newest.
    assert find_cast_file() is None


def test_screen_filters_by_window_suffix(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("STY", "12345.pts-0.host")
    monkeypatch.setenv("WINDOW", "2")
    win0 = tmp_path / "2025-01-01_00-00-00-000_11111_screen0.cast"
    win2 = tmp_path / "2025-01-01_00-00-00-000_22222_screen2.cast"
    _touch(win0, 2000)
    _touch(win2, 1000)

    assert find_cast_file() == str(win2)


def test_screen_needs_both_sty_and_window(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("STY", "12345.pts-0.host")
    # WINDOW absent — screen detection should not kick in.
    cast = tmp_path / "any_file.cast"
    _touch(cast, 1000)

    assert find_cast_file() == str(cast)


def test_nested_tmux_in_screen_requires_both_suffixes(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setenv("STY", "12345.pts-0.host")
    monkeypatch.setenv("WINDOW", "2")
    tmux_only = tmp_path / "2025-01-01_00-00-00-000_11111_tmux1.cast"
    screen_only = tmp_path / "2025-01-01_00-00-00-000_22222_screen2.cast"
    nested = tmp_path / "2025-01-01_00-00-00-000_33333_tmux1_screen2.cast"
    _touch(tmux_only, 3000)
    _touch(screen_only, 2000)
    _touch(nested, 1000)

    assert find_cast_file() == str(nested)


def test_nested_missing_match_returns_none(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setenv("STY", "12345.pts-0.host")
    monkeypatch.setenv("WINDOW", "2")
    # Only an outer-tmux-only file exists; no nested match.
    tmux_only = tmp_path / "2025-01-01_00-00-00-000_11111_tmux1.cast"
    _touch(tmux_only, 3000)

    assert find_cast_file() is None


def test_empty_log_dir_returns_none(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path))

    assert find_cast_file() is None
