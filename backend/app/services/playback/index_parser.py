"""Parse Dahua `mediaFileFind` / `findNextFile` response bodies into clip spans.

The NVR replies to `mediaFileFind.findNextFile` with a flat key=value body
where each recording is a numbered `items[N].<field>=<value>` entry.  This
module is pure logic — no network, no I/O — so it can be unit-tested offline.

Real body fragment from a Dahua NVR (192.168.20.15, verified 2026-06-30,
CRLF line endings)::

    items[0].Channel=0
    items[0].StartTime=2026-06-30 16:00:00
    items[0].EndTime=2026-06-30 17:00:00
    items[0].Type=dav
    items[0].Flags[0]=Timing
    items[0].VideoStream=Main

The `Flags[0]` value (e.g. ``Timing``, ``Event``) is the semantic record type;
`VideoStream` (e.g. ``Main``, ``Sub``) is the recorded stream; `Type`
(e.g. ``dav``) is just the container format.  These NVRs record the Main
stream only — ``condition.VideoStream`` filters are ignored and always return
``Main`` (verified against the live NVR).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

__all__ = ["FindRecord", "Clip", "parse_find_records", "merge_into_clips"]

_DT_FMT = "%Y-%m-%d %H:%M:%S"

# Matches lines like:  items[3].StartTime=2026-06-29 08:00:00
#                      items[3].Flags[0]=Timing
_LINE_RE = re.compile(r"^items\[(\d+)\]\.([^=]+)=(.*)$")


@dataclass(slots=True)
class FindRecord:
    """One recording segment returned by the NVR."""

    start: datetime
    end: datetime
    type: str    # semantic type, e.g. "Timing" or "Event"
    stream: str  # recorded stream, e.g. "Main" or "Sub"


@dataclass(slots=True)
class Clip:
    """A merged span of one or more adjacent FindRecords."""

    start: datetime
    end: datetime
    type: str
    stream: str


def parse_find_records(body: str) -> list[FindRecord]:
    """Parse a flat ``items[N].Key=Value`` body into a list of FindRecords.

    Lines are processed in order; records are emitted in ascending index order.
    Unknown or missing fields are skipped gracefully — a record is only included
    if it has at least a valid StartTime and EndTime.

    Args:
        body: Raw response body text (LF or CRLF line endings).

    Returns:
        List of :class:`FindRecord` instances, one per valid ``items[N]`` group.
    """
    # Collect raw fields per item index
    raw: dict[int, dict[str, str]] = defaultdict(dict)
    for line in body.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        idx, key, val = int(m.group(1)), m.group(2), m.group(3).strip()
        raw[idx][key] = val

    records: list[FindRecord] = []
    for idx in sorted(raw):
        fields = raw[idx]
        try:
            start = datetime.strptime(fields["StartTime"], _DT_FMT)
            end = datetime.strptime(fields["EndTime"], _DT_FMT)
        except (KeyError, ValueError):
            continue  # skip incomplete / malformed entries

        # Semantic type comes from Flags[0]; fall back to Type then empty string
        rec_type = fields.get("Flags[0]") or fields.get("Type", "")
        # Recorded stream: VideoStream (e.g. "Main"/"Sub"); empty if absent
        stream = fields.get("VideoStream", "")

        records.append(FindRecord(start=start, end=end, type=rec_type, stream=stream))

    return records


def merge_into_clips(
    records: list[FindRecord],
    gap_tolerance_s: int = 5,
) -> list[Clip]:
    """Merge adjacent FindRecords of the same type+stream into Clip spans.

    Two records are considered adjacent if they share the same ``type`` and
    ``stream`` AND the gap between one record's end and the next record's start
    is at most *gap_tolerance_s* seconds.  Records are processed in the order
    supplied — callers should sort by start time first if needed.

    Args:
        records:        Records to merge, typically from :func:`parse_find_records`.
        gap_tolerance_s: Maximum gap in seconds that is still considered contiguous.

    Returns:
        List of :class:`Clip` instances in the same order as the input.
    """
    if not records:
        return []

    clips: list[Clip] = []
    current = records[0]
    current_start = current.start
    current_end = current.end
    current_type = current.type
    current_stream = current.stream

    for rec in records[1:]:
        gap = (rec.start - current_end).total_seconds()
        same_key = rec.type == current_type and rec.stream == current_stream
        if same_key and gap <= gap_tolerance_s:
            # Extend the current clip
            current_end = max(current_end, rec.end)
        else:
            clips.append(Clip(
                start=current_start,
                end=current_end,
                type=current_type,
                stream=current_stream,
            ))
            current_start = rec.start
            current_end = rec.end
            current_type = rec.type
            current_stream = rec.stream

    clips.append(Clip(
        start=current_start,
        end=current_end,
        type=current_type,
        stream=current_stream,
    ))
    return clips
