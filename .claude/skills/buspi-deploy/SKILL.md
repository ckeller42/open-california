---
name: buspi-deploy
description: Sync calictl changes to the buspi Raspberry Pi (the BLE owner), restart the daemon, and live-verify over BLE. Use when testing a semantics/control/device change on the real vehicle, or reading live status that needs the free BLE slot.
---

# Deploy + live-verify on buspi

buspi (`ssh buspi`, also Tailscale (LAN address redacted)) is the **single BLE owner**. It runs
`calictl serve` as a systemd unit under `~/solix-env`; the van allows buspi + the phone app
connected at once, but only ONE controller writes reliably.

## Deploy (git pull + restart)

buspi's `~/open-california` **is a git checkout** (verified HEAD tracks `main`), so deploying
merged code is a pull + a scoped, passwordless restart — no scp, no interactive sudo:

```sh
ssh buspi 'cd ~/open-california && git pull --ff-only && \
           sudo -n systemctl restart calictl.service && sleep 2 && systemctl is-active calictl.service'
```

The restart/stop/start of `calictl.service` are the ONLY passwordless sudo verbs (scoped rule in
`deploy/calictl-restart.sudoers`); every other `sudo` (editing `/etc/buspi/*.env`, installing
files) still prompts for the box password.

## Live read / actuate loop (needs the BLE slot)

```sh
# 1) a live read/set needs the single BLE slot — stop the daemon first
ssh buspi 'sudo -n systemctl stop calictl.service && sleep 1'

# 2) read or actuate (phone app CLOSED so buspi is sole controller)
ssh buspi 'cd ~/open-california && ~/solix-env/bin/python -m calictl get energy'
ssh buspi 'cd ~/open-california && ~/solix-env/bin/python -m calictl set cooler power on'

# 3) ALWAYS restart the daemon afterwards
ssh buspi 'sudo -n systemctl start calictl.service && sleep 2 && systemctl is-active calictl.service'
```

## BLE address resolution + persistence

The real BLE address is NOT in the code (placeholder `AA:BB:CC:DD:EE:FF`). `device.resolve_addr()`
resolves it, in order: **1.** `CALICTL_ADDR` env → **2.** the pairing cache
`~/.local/state/calictl/pairing.json` (XDG_STATE_HOME — durable state, NOT `~/.cache`; written by
the guided-pairing wizard's `persist_bond()`, cleared by unpair — reboot-durable) → **3.**
placeholder. The systemd unit currently loads `CALICTL_ADDR`
from `/etc/buspi/calictl.env`; a manual `calictl` run has no env, so source it (without printing
it) first, or rely on the cache once migrated:
```sh
export CALICTL_ADDR=$(sudo grep -oP "(?<=CALICTL_ADDR=).*" /etc/buspi/calictl.env)
~/solix-env/bin/python -m calictl get cooler      # or pass --addr "$CALICTL_ADDR"
```

### One-time migration: env → cache (APPLIED on buspi 2026-08-31; procedure kept for re-provisioning)
So that the wizard's unpair actually survives a reboot (env-first would otherwise re-target the
removed bond), make the pairing cache the source and stop setting the env var. Already done on the
current buspi (cache seeded, `CALICTL_ADDR` removed, daemon polling from cache; env backed up at
`/etc/buspi/calictl.env.bak-pre-cache-migration`). Re-run only on a fresh box:
```sh
# 1) seed the cache from the current env addr (cache is home-owned; the read needs the box
#    sudo password, so this is interactive) — additive, no risk
ssh -t buspi 'A=$(sudo grep -oP "(?<=CALICTL_ADDR=).*" /etc/buspi/calictl.env) && \
           mkdir -p ~/.local/state/calictl && printf "{\"address\": \"%s\"}" "$A" > ~/.local/state/calictl/pairing.json'
# 2) verify resolution still finds it with NO env set
ssh buspi 'cd ~/open-california && env -u CALICTL_ADDR ~/solix-env/bin/python -c \
           "from calictl.device import resolve_addr; print(resolve_addr()[:8]+\"…\")"'
# 3) drop the CALICTL_ADDR line from the unit env (needs the box sudo password — interactive)
ssh -t buspi 'sudo sed -i "/^CALICTL_ADDR=/d" /etc/buspi/calictl.env && sudo -n systemctl restart calictl.service'
```
`CALICTL_ADDR` then reverts to a pure manual override (dev boxes, forcing a target).

## Notes
- Long BLE ops (`get`, `set`, actuate) take ~10-60 s; run detached with a log + poll, or use
  `timeout 70`. `set` competes with the daemon for the slot — stop the daemon for clean reads.
- buspi can drop off the network (travel router); if `ssh buspi` times out, try the Tailscale
  IP, and if still down, report and wait rather than retrying forever.
- The APK lives at `buspi:~/apks/de.volkswagen.CaliforniaOnTour.apk` (gitignored source).
- `sudo` on buspi needs the box password EXCEPT restart/stop/start of `calictl.service` (passwordless, scoped `deploy/calictl-restart.sudoers`); secrets in `/etc/buspi/*.env` (root, 0600).
- Grafana dashboards don't auto-update; push from buspi with `deploy/push_dashboard.py`.
- The web UI **can** be served over **HTTPS on the tailnet** via `tailscale serve` (proxying
  `127.0.0.1:8088`) at `https://<pi-name>.<tailnet>.ts.net/` — tailnet-only, valid LE cert,
  persistent across reboots. **Currently DISABLED** (owner turned it off). Enable:
  `sudo tailscale serve --bg 8088`; status: `sudo tailscale serve status`; disable:
  `sudo tailscale serve --https=443 off` (all need sudo/the box password). **Never `tailscale
  funnel`** it (that publishes the unauthenticated, write-capable UI to the public internet;
  check `AllowFunnel` is None). User-facing setup guide: `docs/raspberry-pi-setup.md` →
  "Remote access over Tailscale".
