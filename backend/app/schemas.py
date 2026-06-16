"""Pydantic schemas for API request/response bodies.

Conventions:
  • *Create / *Update — input bodies (writeable fields only).
  • *Read           — output bodies (no secrets, no encrypted blobs).
  • RTSP credentials are accepted on create/update but NEVER returned.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import Role, StreamQuality, Vendor


# ── Auth ────────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


# ── Users ───────────────────────────────────────────────────────────────────


class UserBase(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    role: Role = Role.operator
    is_active: bool = True


class UserCreate(UserBase):
    password: str = Field(min_length=8)
    region_ids: list[uuid.UUID] = Field(default_factory=list)


class UserUpdate(BaseModel):
    is_active: bool | None = None
    role: Role | None = None
    region_ids: list[uuid.UUID] | None = None
    new_password: str | None = Field(default=None, min_length=8)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None
    region_ids: list[uuid.UUID] = Field(default_factory=list)


# ── Regions ─────────────────────────────────────────────────────────────────


class RegionBase(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=128)


class RegionCreate(RegionBase):
    pass


class RegionRead(RegionBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime


# ── NVRs ────────────────────────────────────────────────────────────────────


class NvrBase(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    ip: str
    port: int = 554
    rtsp_username: str = "admin"
    vendor: Vendor = Vendor.dahua
    enabled: bool = True
    group: str | None = None
    region_id: uuid.UUID | None = None


class NvrCreate(NvrBase):
    # id is optional — if omitted the router derives one from the IP address
    # (e.g. "192.168.20.34" → "nvr-192-168-20-34") so casual users don't have
    # to invent an identifier.
    id: str | None = Field(
        default=None, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )
    rtsp_password: str = Field(min_length=1)
    # channels: null/missing → backend auto-detects (Dahua magicBox CGI).
    # Falls back to 1 channel if detection isn't possible.
    channels: int | None = Field(default=None, ge=1, le=512)
    # skip_probe: caller can force-skip the credential check (used by the
    # discovery import flow which already validated creds). Default: probe.
    skip_probe: bool = False


class SetChannelsRequest(BaseModel):
    """Bulk-set how many channels an NVR has. Creates cameras for any missing
    channel in 1..count. With `prune=True`, also deletes channels above
    `count` (and their MediaMTX paths). Used to populate a multi-channel NVR
    in one shot instead of adding cameras one at a time."""
    count: int = Field(ge=1, le=512)
    prune: bool = False


class NvrUpdate(BaseModel):
    label: str | None = None
    ip: str | None = None
    port: int | None = None
    rtsp_username: str | None = None
    rtsp_password: str | None = None
    vendor: Vendor | None = None
    enabled: bool | None = None
    group: str | None = None
    region_id: uuid.UUID | None = None


class NvrRead(NvrBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    updated_at: datetime
    camera_count: int
    # Populated only on the response to POST /nvrs to tell the UI what
    # happened during pre-create validation. Always null on GET.
    create_notice: str | None = None


# ── Cameras ─────────────────────────────────────────────────────────────────


class CameraRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    nvr_id: str
    channel: int
    name: str | None
    # Camera's own IP — when set, the main stream is pulled straight from the
    # camera instead of the NVR relay. Null = main via NVR (legacy).
    ip: str | None = None
    enabled: bool
    has_sub: bool
    has_main: bool
    display_name: str
    # Region inherited from NVR.
    region_id: uuid.UUID | None = None


class CameraCreate(BaseModel):
    nvr_id: str
    channel: int = Field(ge=1, le=512)
    name: str | None = None
    enabled: bool = True
    has_sub: bool = True
    has_main: bool = True


class CameraUpdate(BaseModel):
    name: str | None = None
    # Empty string clears the IP (camera's main falls back to the NVR relay).
    ip: str | None = None
    enabled: bool | None = None
    has_sub: bool | None = None
    has_main: bool | None = None


class CameraIpImportResult(BaseModel):
    """Outcome of pulling the camera-IP list from an NVR (RemoteDevice CGI)."""
    nvr_id: str
    found: int      # channels the NVR reported with a real camera IP
    updated: int    # cameras whose stored IP actually changed
    message: str


# ── Streams ─────────────────────────────────────────────────────────────────


class StreamUrlResponse(BaseModel):
    """What the client receives to start playback.

    `webrtc_whep_url` is the primary low-latency path; `hls_url` is the fallback.
    Neither contains NVR credentials — both point at MediaMTX, which fans
    out a single RTSP pull to N viewers.
    """

    camera_id: uuid.UUID
    quality: StreamQuality
    path: str
    webrtc_whep_url: str
    hls_url: str
    rtsp_url: str | None = None  # exposed only to admin / on demand


# ── Health / events ─────────────────────────────────────────────────────────


class NvrHealthResult(BaseModel):
    nvr_id: str
    ok: bool
    message: str


class NvrTestResult(BaseModel):
    ok: bool
    message: str
    banned_until: float | None = None
    remaining: int | None = None


class NvrEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    nvr_id: str
    ip: str
    event_type: str
    message: str | None
    created_at: datetime


# ── Discovery ───────────────────────────────────────────────────────────────


class DiscoveryScanRequest(BaseModel):
    """Optional CIDR override. If null, the backend derives the default /24
    from its outbound interface. Set `onvif=false` to skip multicast (e.g.
    when MTX-isolated). Set `tcp=false` to skip CIDR scan."""
    cidr: str | None = None
    onvif: bool = True
    tcp: bool = True
    timeout: float = Field(default=3.0, ge=0.5, le=10.0)
    rtsp_username: str | None = None  # if provided, run Dahua channel autodetect
    rtsp_password: str | None = None


class DiscoveryCandidate(BaseModel):
    ip: str
    port: int = 554
    sources: list[str]          # "onvif" / "tcp"
    vendor_guess: str
    label_hint: str | None = None
    xaddrs: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    detected_channels: int | None = None
    already_known: bool = False  # this IP already exists in the inventory


class DiscoveryScanResponse(BaseModel):
    cidr_used: str | None
    candidates: list[DiscoveryCandidate]
    duration_ms: int


class DiscoveryImportItem(BaseModel):
    ip: str
    port: int = 554
    vendor: Vendor = Vendor.dahua
    channels: int = Field(ge=1, le=512)
    label: str | None = None
    group: str | None = None
    nvr_id: str | None = None  # auto-generated from IP if absent


class DiscoveryImportRequest(BaseModel):
    rtsp_username: str
    rtsp_password: str
    region_id: uuid.UUID | None = None
    test_first: bool = True  # probe RTSP digest before saving
    hosts: list[DiscoveryImportItem]


class DiscoveryImportResult(BaseModel):
    ip: str
    nvr_id: str | None
    ok: bool
    message: str
