# DSS netcheck — 2026-06-10T10:27:39

NVR: nvr-192-168-20-58 192.168.20.58:554 vendor=dahua
Sample URL (main ch1): `rtsp://admin:***@192.168.20.58:554/cam/realmonitor?channel=1&subtype=0`

## Network path (VPN should be OFF)
- route → `{"InterfaceAlias":"Ethernet","IPAddress":"10.10.1.127"}`
- Ethernet link speed: 100 Mbps
- ping: recv=10/10 avg=2ms
- existing :554 conn source IPs: (none)

## Load phases (isolated MediaMTX, sourceOnDemand off, TCP)

| phase | streams | ready | aggregate Mbps | Ethernet RX Mbps | RTP packets lost |
|---|---|---|---|---|---|
| 1 main | 1 | 1/1 | 1.0 | 1.3 | 4977  |
| 2 main | 2 | 2/2 | 1.8 | 2.0 | 14779  |
| 4 main | 4 | 4/4 | 2.8 | 3.2 | 32423  |
| 8 main | 8 | 8/8 | 5.1 | 5.5 | 56400  |
| 8 sub (baseline) | 8 | 8/8 | 3.9 | 4.3 | 156  |

## Read-me (interpretation)
- Loss appears while Ethernet RX is only ≈ 5.5 Mbps (well under 100) → **NVR OUTPUT LIMIT**. Gigabit won't help; reduce concurrent streams / lower stream bitrate / raise NVR remote-bandwidth.
- Even a SINGLE main stream lost 4977 packets → per-stream NVR/channel issue (bitrate/codec), not aggregate capacity.

_Tip: compare 'aggregate Mbps' growth vs where 'RTP packets lost' first jumps._
