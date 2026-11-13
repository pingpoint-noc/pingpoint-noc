# Review of default settings: do they fit any network?

Principle: a default must be right for a network the author has never seen. A site tunes it afterwards, but the
out-of-the-box value should neither flood a small network nor stay silent on a large one. Status: review of
2026-11-13 against the code; items marked **change** are not done yet unless the status says so.

| Setting | Default | Verdict | Why / proposal |
|---|---|---|---|
| `alert_bw_threshold_bps` (high_bandwidth) | 10 Mbit/s absolute | **done 2026-11-13**: new `alert_bw_utilization_pct` (80 %) | Lab-sized. On a 1 Gbit/s or 10 Gbit/s link it fires at 1 % or 0.1 % load; on a 10 Mbit/s link it never means "saturated". Judge **utilisation of the link speed** (`interfaces.speed_bps` is already stored, from `ifHighSpeed`): default 80 % for 5 minutes. Keep the absolute value as an optional override and as the fallback when the speed is unknown (0). Needs a new setting `alert_bw_utilization_pct` through settings, API, UI and tests. |
| `alert_bw_hold_seconds` | 120 s | change with the above | 5 min is the usual "sustained" time for a utilisation alert; 120 s remains for the absolute mode. |
| `alert_stale_seconds` | 300 s | keep, document | Must be at least 5x the polling interval of the source. Fine for the shipped 30-60 s pollers; say so next to the setting. |
| `alert_failed_logins` | 5 | keep | Matches common lockout policies. |
| `alert_port_scan_threshold` / window | 15 ports / 180 s | keep | Scanners the site owns (vulnerability scans, monitoring) are expected sources; an allow-list is the missing piece, not a new default. |
| `alert_rare_conn_duration_seconds` | 120 s | keep | The rule looks at a 300 s window, so a longer duration would almost never match. Long sessions on non-standard ports (VPN, games, remote desktop) are handled by the rule's list of standard ports, not by this value. |
| `flow_new_destination` | 24 h learning, 20 blocks, burst 10, window 600 s | **changed** | See the section below. |
| `alert_dns_latency_threshold_ms` | 500 ms | keep | A resolver slower than half a second is degraded on any network. |
| `alert_host_cpu_pct` / hold | 90 % / 300 s | keep | Sustained, so spikes do not alert. |
| `alert_host_memory_pct` | 90 % | **done 2026-11-13** | The SNMP collector counted the page cache and buffers (net-snmp lists them as separate rows) as used memory. They are now subtracted. Proxmox memory comes from the PVE API and is unchanged. |
| `alert_host_disk_pct` | 90 % | review | A fixed percentage is too late on small volumes and too early on multi-terabyte ones. Keep as is; a "days until full" rule is a later feature. |
| `alert_icmp_loss_pct` | 40 % | keep | Checked against the sampling: a sample is 5 pings, so loss moves in steps of 20 %. 20 % would be a single lost ping (routine ICMP rate limiting); 40 % (two lost pings) is the smallest meaningful level. |
| `alert_icmp_latency_ms` | 150 ms | open | A fixed value is wrong for intercontinental targets (over 150 ms normally) and too lax for a LAN switch (over 5 ms is odd). Proposal: per-device threshold with this value as the fallback. |
| `alert_pve_storage_pct` | 85 % | keep | |
| `alert_syslog_burst_*` | 50 messages / 60 s, factor 3 | keep | The factor against the sender's own usual rate is what makes it general. |
| `alert_flap_*` | 3 changes / 300 s | keep | |
| `alert_err_*` | 10 errors, 3 intervals / 300 s | keep | Counted by growth, not by the counter value. |
| `scenario.py` (`MININOC_ROUTER_IP`, internal nets, NOC IP, `MININOC_TZ`) | the original lab's addresses and Europe/Madrid | open (phase 5) | Defaults must be empty or neutral and first run must ask for them. Behaviour change, so it needs a release note. |

## `flow_new_destination` (changed on 2026-11-13)

What it detects: an internal host reaching an external address block it has not used before. It detects novelty, not
maliciousness. Measured behaviour that motivated the changes: CDN and cloud providers rotate addresses inside
their blocks all day, so address-level novelty fired on every rotation.

- **Unit = destination block** (/24 for IPv4, /48 for IPv6). A new address inside a block the host already used is
  the same destination. The addresses are still remembered and shown in the message.
- **A host is judged only after it has been observed for the learning period itself.** Before, only the device
  exporting the flows had to be old, so a laptop that joined the network an hour ago raised alerts.
- **Baseline counts blocks, not addresses**: 30 addresses in one block is a quiet host, not a browsing one.
- **Tiers kept**: a quiet host (fewer than `alert_new_dest_baseline` blocks) alerts on any new block; a browsing host
  only on a burst of `alert_new_dest_burst` new blocks.
- **Cooldown of 6 h per host** after an alert resolves (`NEW_DEST_COOLDOWN_SECONDS`; new blocks are still learned).
  It bounds a wandering host to a few notices a day, which is the target of the original design.
- The "resolved" message says the notice expired and nothing needs fixing.
- Still open: a quiet *workstation* looks like a quiet *server*. Without a role per device there is no way to tell,
  which is why the tiers and the cooldown exist. A per-host "ignore" or an ASN/organisation lookup would remove the
  rest of the noise; both need data this project does not have (see ROADMAP).

## Not done yet, and why

- **Per-device ICMP latency threshold.** Needs a per-device settings model (storage, API and UI); a global number cannot
  be right for both a LAN switch and an overseas host.
- **Neutral `scenario.py` defaults.** Over 100 files and the tests assume the original lab's addresses; changing them
  needs a first-run wizard (or a preflight that refuses to start without a router address) and a release note.

## Added after the first real deployment (2026-11-13)

- Plumbing interfaces (loopback, Docker/Kubernetes bridges, veth, libvirt and Proxmox firewall bridges, tap devices) are not watched:
  `DEFAULT_IGNORED_INTERFACES` in `alerts/engine.py` (same idea as LibreNMS's `bad_if_regexp`). First day without it: 24 false alerts.
- Discards-only interfaces need 100x the errors threshold (`DISCARD_THRESHOLD_FACTOR`): every host of a quiet LAN shows about 20 discards a
  minute (multicast and IPv6 noise it ignores). Errors keep their own threshold.
- SNMP memory excludes the page cache and buffers (net-snmp lists them as separate rows).
- Both constants above should become settings (per device); see ROADMAP, "Siguiente".
