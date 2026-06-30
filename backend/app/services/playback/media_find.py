"""Dahua mediaFileFind HTTP client — stateful, paginated, always-close.

Runs the full Dahua ``mediaFileFind`` CGI sequence to fetch a recording index
for a given channel and time window, then returns the results as merged
:class:`~.index_parser.Clip` spans.

Protocol summary::

    GET /cgi-bin/mediaFileFind.cgi?action=factory.create
        → result=<object_id>

    GET /cgi-bin/mediaFileFind.cgi?action=findFile
            &object=<id>
            &condition.Channel=<ch>
            &condition.StartTime=YYYY-MM-DD%20HH:MM:SS
            &condition.EndTime=YYYY-MM-DD%20HH:MM:SS
        → result=true

    GET /cgi-bin/mediaFileFind.cgi?action=findNextFile
            &object=<id>&count=<batch>
        → items[N].field=value … (repeated until empty or <batch records)

    GET /cgi-bin/mediaFileFind.cgi?action=close&object=<id>
    GET /cgi-bin/mediaFileFind.cgi?action=destroy&object=<id>
        (always, even on error — leaked handles exhaust the NVR)
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from .index_parser import Clip, FindRecord, merge_into_clips, parse_find_records

log = logging.getLogger("dss.playback.media_find")

_DT_FMT = "%Y-%m-%d %H:%M:%S"

__all__ = ["MediaFindError", "find_clips"]


class MediaFindError(Exception):
    """Raised when the mediaFileFind sequence fails (HTTP error, protocol
    violation, or unexpected exception).  Never swallowed — a leaked
    find-handle exhausts the NVR's session table."""


# ── Internal helpers ──────────────────────────────────────────────────────────


def _cgi_base(ip: str, port: int) -> str:
    return f"http://{ip}:{port}/cgi-bin/mediaFileFind.cgi"


async def _get(client: httpx.AsyncClient, url: str) -> str:
    """Issue a GET and return the response body as text.

    Re-raises ``httpx.HTTPError`` as :class:`MediaFindError`.
    """
    try:
        r = await client.get(url)
        r.raise_for_status()
        return r.text
    except httpx.HTTPError as exc:
        raise MediaFindError(f"HTTP error fetching {url!r}: {exc}") from exc


def _parse_object_id(body: str) -> str:
    """Extract ``result=<id>`` from a ``factory.create`` response."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("result="):
            return stripped.split("=", 1)[1].strip()
    raise MediaFindError(
        f"No 'result=<id>' in factory.create response: {body!r}"
    )


def _is_ok(body: str) -> bool:
    """Return True if the response contains ``result=true``."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("result="):
            return stripped.split("=", 1)[1].strip().lower() == "true"
    return False


# ── Public API ────────────────────────────────────────────────────────────────


async def find_clips(
    ip: str,
    port: int,
    user: str,
    pw: str,
    *,
    channel: int,
    start: datetime,
    end: datetime,
    batch: int = 100,
) -> list[Clip]:
    """Fetch a recording index from a Dahua NVR and return merged Clip spans.

    Runs the full ``mediaFileFind`` sequence:

    1. ``factory.create``   → obtain an object handle.
    2. ``findFile``         → set search condition (channel + time range).
    3. ``findNextFile``     → paginate until an empty or short-batch response.
    4. ``close`` + ``destroy`` → **always**, even when an earlier step failed.

    Args:
        ip:      NVR IP address.
        port:    NVR HTTP port (80 is the Dahua default).
        user:    Digest-auth username.
        pw:      Digest-auth password.
        channel: Camera channel number (1-based on Dahua NVRs).
        start:   Beginning of the search window (inclusive).
        end:     End of the search window (inclusive).
        batch:   Max records to request per ``findNextFile`` call.

    Returns:
        Merged :class:`~.index_parser.Clip` list (may be empty if no recordings
        exist in the window).

    Raises:
        MediaFindError: On any failure — HTTP error, protocol violation, or
            unexpected exception.  The NVR handle is always closed before
            this propagates.
    """
    base = _cgi_base(ip, port)
    start_str = start.strftime(_DT_FMT)
    end_str = end.strftime(_DT_FMT)

    async with httpx.AsyncClient(
        auth=httpx.DigestAuth(user, pw),
        timeout=10.0,
        trust_env=False,
    ) as client:
        # ── Step 1: create object handle ──────────────────────────────────
        create_body = await _get(client, f"{base}?action=factory.create")
        obj_id = _parse_object_id(create_body)
        log.debug("mediaFileFind object created: id=%s", obj_id)

        all_records: list[FindRecord] = []
        try:
            # ── Step 2: set search condition ──────────────────────────────
            find_url = (
                f"{base}?action=findFile"
                f"&object={obj_id}"
                f"&condition.Channel={channel}"
                f"&condition.StartTime={start_str}"
                f"&condition.EndTime={end_str}"
            )
            find_body = await _get(client, find_url)
            if not _is_ok(find_body):
                raise MediaFindError(
                    f"findFile returned non-true result for channel={channel} "
                    f"[{start_str} – {end_str}]: {find_body!r}"
                )

            # ── Step 3: paginate ──────────────────────────────────────────
            while True:
                next_url = (
                    f"{base}?action=findNextFile"
                    f"&object={obj_id}&count={batch}"
                )
                page_body = await _get(client, next_url)
                page_records = parse_find_records(page_body)
                all_records.extend(page_records)
                log.debug(
                    "mediaFileFind page: %d records (batch=%d)",
                    len(page_records),
                    batch,
                )
                if len(page_records) < batch:
                    # Short (or empty) page signals end of results
                    break

        except MediaFindError:
            raise
        except Exception as exc:
            raise MediaFindError(
                f"mediaFileFind unexpected error (object={obj_id}): {exc}"
            ) from exc
        finally:
            # ── Step 4: always release the handle ─────────────────────────
            for action in ("close", "destroy"):
                try:
                    await client.get(f"{base}?action={action}&object={obj_id}")
                except Exception as cleanup_exc:  # noqa: BLE001
                    log.warning(
                        "mediaFileFind %s(object=%s) failed: %s",
                        action,
                        obj_id,
                        cleanup_exc,
                    )

    return merge_into_clips(all_records)
