# DSS netcheck (macOS) — label=nvr15-direct-vs-relay
NVR: 192.168.20.15:554 vendor=dahua egress_iface=en7

## Network path
- ping NVR 192.168.20.15: loss=0.0% avg=1.592ms jitter=0.241ms
- ping CAM 192.168.20.101: loss=0.0% avg=28.613ms jitter=61.029ms
  - ⚠️ camera path jitter 61.029ms is high — RTP-loss below is still valid (sequence gaps = source drops), but throughput/Mbps may be understated from this vantage. For Mbps trust the server-side run.

## §3.1 DECISIVE — same main stream: via NVR vs direct from camera
- A via NVR (ch1): `rtsp://admin:***@192.168.20.15:554/cam/realmonitor?channel=1&subtype=0`
- B direct camera:    `rtsp://admin:***@192.168.20.101:554/cam/realmonitor?channel=1&subtype=0`

| path | ready | Mbps | RTP packets lost |
|---|---|---|---|
| A via-NVR ch1 | 1/1 | 1.3 | 1594 |
| B direct-cam | 1/1 | 1.5 | 0 |

**Verdict:** NVR relay lost 1594 pkts, direct camera lost 0 → the NVR relay is the bottleneck. Variant A (pull this camera direct) fixes it.
