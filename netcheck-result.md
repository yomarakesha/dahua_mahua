# DSS netcheck — 2026-06-11T10:17:02 — run=day
_Contention run label: **day** (compare peak vs night, §3.6)._

NVR: nvr-192-168-20-58 192.168.20.58:554 vendor=dahua
Sample URL (main ch1): `rtsp://admin:***@192.168.20.58:554/cam/realmonitor?channel=1&subtype=0`

## Network path (VPN should be OFF)
- egress to NVR: ifIndex=13 srcIP=192.168.20.240
- egress link speed: 100 Mbps
- ping: recv=4/4 avg=1ms
- existing :554 conn source IPs: (none)

## Physical link (§3.5)
- link speed: 100 Mbps   full-duplex: True
- NIC counters (cumulative — non-zero/growing = bad cable/port/duplex): rxErrors=0 rxDiscarded=0 txErrors=0 txDiscarded=0

## §3.1 DECISIVE — same main stream: via NVR vs direct from camera
- A via NVR (ch1): `rtsp://admin:***@192.168.20.58:554/cam/realmonitor?channel=1&subtype=0`
- B direct camera:       `rtsp://admin:***@192.168.23.11:554/cam/realmonitor?channel=1&subtype=0`

| path | ready | Mbps | RTP packets lost |
|---|---|---|---|
| A via-NVR ch1 | 1/1 | 1.1 | 7815  |
| B direct-cam | 1/1 | 0.9 | 0  |

## Load phases (isolated MediaMTX, sourceOnDemand off, TCP)

| phase | streams | ready | aggregate Mbps | Ethernet RX Mbps | RTP packets lost |
|---|---|---|---|---|---|
| 1 main | 1 | 1/1 | 1.0 | 1.3 | 5586  |
| 2 main | 2 | 2/2 | 2.2 | 2.5 | 13132  |
| 4 main | 4 | 4/4 | 3.2 | 3.6 | 31243  |
| 8 main | 8 | 8/8 | 6.1 | 6.5 | 50458  |
| 8 sub (baseline) | 8 | 7/8 | 3.2 | 3.9 | 0  |

## Read-me (interpretation)
- **\u00a73.1: direct-camera CLEAN (0) but via-NVR lost 7815** \u2192 NVR RELAY is the bottleneck \u2192 Variant A (pull cameras directly) would fix it.
- Loss appears while Ethernet RX is only \u2248 6.5 Mbps (well under 100) \u2192 **NVR OUTPUT LIMIT**. Gigabit won't help; reduce concurrent streams / lower stream bitrate / raise NVR remote-bandwidth.
- Even a SINGLE main stream lost 5586 packets \u2192 per-stream NVR/channel issue (bitrate/codec), not aggregate capacity.
- 8 **sub**-streams via the same NVR: 0 lost \u2192 the NVR relays sub cleanly; only the **main**-stream relay path drops.

_Tip: compare 'aggregate Mbps' growth vs where 'RTP packets lost' first jumps._

_(Interpretation appended manually: the original run crashed while printing `\u2192` to a cp1251 console after all measurements completed; netcheck.py now reconfigures stdout to UTF-8.)_
