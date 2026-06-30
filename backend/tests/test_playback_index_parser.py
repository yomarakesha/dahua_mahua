"""Tests for the Dahua mediaFileFind response parser.

Covers parse_find_records (key=val body → FindRecord list) and
merge_into_clips (adjacent same-stream records → Clip list).
"""

from datetime import datetime

import pytest

from app.services.playback.index_parser import (
    Clip,
    FindRecord,
    merge_into_clips,
    parse_find_records,
)

SAMPLE = (
    "items[0].Channel=1\r\n"
    "items[0].StartTime=2026-06-29 08:00:00\r\n"
    "items[0].EndTime=2026-06-29 08:30:00\r\n"
    "items[0].Type=dav\r\n"
    "items[0].Flags[0]=Timing\r\n"
    "items[1].StartTime=2026-06-29 08:30:00\r\n"
    "items[1].EndTime=2026-06-29 09:00:00\r\n"
    "items[1].Flags[0]=Event\r\n"
)


# ── parse_find_records ────────────────────────────────────────────────────────


def test_parses_records_with_times_and_type():
    recs = parse_find_records(SAMPLE)
    assert len(recs) == 2
    assert recs[0].start.hour == 8 and recs[0].end.minute == 30
    assert recs[0].type == "Timing" and recs[1].type == "Event"


def test_returns_findrecord_dataclasses():
    recs = parse_find_records(SAMPLE)
    for r in recs:
        assert isinstance(r, FindRecord)
        assert isinstance(r.start, datetime)
        assert isinstance(r.end, datetime)
        assert isinstance(r.type, str)
        assert isinstance(r.stream, str)


def test_parse_empty_body():
    assert parse_find_records("") == []


def test_parse_stream_field_present():
    recs = parse_find_records(SAMPLE)
    # items[0] has Type=dav → stream must be "dav"
    # items[1] has no Type key → stream must fall back to ""
    assert recs[0].stream == "dav"
    assert recs[1].stream == ""


# ── merge_into_clips ─────────────────────────────────────────────────────────


def _make_record(start_h: int, end_h: int, type_: str = "Timing", stream: str = "main") -> FindRecord:
    return FindRecord(
        start=datetime(2026, 6, 29, start_h, 0, 0),
        end=datetime(2026, 6, 29, end_h, 0, 0),
        type=type_,
        stream=stream,
    )


def test_merge_adjacent_records_into_one_clip():
    recs = [_make_record(8, 9), _make_record(9, 10)]
    clips = merge_into_clips(recs)
    assert len(clips) == 1
    assert clips[0].start == datetime(2026, 6, 29, 8, 0, 0)
    assert clips[0].end == datetime(2026, 6, 29, 10, 0, 0)


def test_gap_beyond_tolerance_creates_two_clips():
    recs = [_make_record(8, 9), _make_record(10, 11)]  # 1-hour gap
    clips = merge_into_clips(recs, gap_tolerance_s=5)
    assert len(clips) == 2


def test_different_streams_not_merged():
    recs = [
        _make_record(8, 9, stream="main"),
        _make_record(9, 10, stream="sub"),
    ]
    clips = merge_into_clips(recs)
    assert len(clips) == 2


def test_different_types_not_merged():
    recs = [
        _make_record(8, 9, type_="Timing"),
        _make_record(9, 10, type_="Event"),
    ]
    clips = merge_into_clips(recs)
    assert len(clips) == 2


def test_merge_returns_clip_dataclasses():
    recs = [_make_record(8, 9)]
    clips = merge_into_clips(recs)
    assert len(clips) == 1
    c = clips[0]
    assert isinstance(c, Clip)
    assert isinstance(c.start, datetime)
    assert isinstance(c.end, datetime)
    assert isinstance(c.type, str)
    assert isinstance(c.stream, str)


def test_merge_empty_input():
    assert merge_into_clips([]) == []


def test_gap_within_tolerance_merges():
    # 3-second gap, tolerance = 5 → should merge
    recs = [
        FindRecord(
            start=datetime(2026, 6, 29, 8, 0, 0),
            end=datetime(2026, 6, 29, 8, 30, 0),
            type="Timing",
            stream="main",
        ),
        FindRecord(
            start=datetime(2026, 6, 29, 8, 30, 3),  # 3s gap
            end=datetime(2026, 6, 29, 9, 0, 0),
            type="Timing",
            stream="main",
        ),
    ]
    clips = merge_into_clips(recs, gap_tolerance_s=5)
    assert len(clips) == 1
    assert clips[0].end == datetime(2026, 6, 29, 9, 0, 0)


def test_gap_exactly_at_tolerance_merges():
    # gap == gap_tolerance_s (5s) must merge — condition is <=, not <
    recs = [
        FindRecord(
            start=datetime(2026, 6, 29, 8, 0, 0),
            end=datetime(2026, 6, 29, 8, 30, 0),
            type="Timing",
            stream="main",
        ),
        FindRecord(
            start=datetime(2026, 6, 29, 8, 30, 5),  # exactly 5s gap
            end=datetime(2026, 6, 29, 9, 0, 0),
            type="Timing",
            stream="main",
        ),
    ]
    clips = merge_into_clips(recs, gap_tolerance_s=5)
    assert len(clips) == 1
    assert clips[0].start == datetime(2026, 6, 29, 8, 0, 0)
    assert clips[0].end == datetime(2026, 6, 29, 9, 0, 0)
