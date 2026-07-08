---
name: buspi-deploy
description: Sync calictl changes to the buspi Raspberry Pi (the BLE owner), restart the daemon, and live-verify over BLE. Use when testing a semantics/control/device change on the real vehicle, or reading live status that needs the free BLE slot.
---

# Deploy + live-verify on buspi

buspi (`ssh buspi`, also Tailscale `100.118.206.17`) is the **single BLE owner**. It runs
`calictl serve` as a systemd unit under `~/solix-env`; the van allows buspi + the phone app
connected at once, but only ONE controller writes reliably.

## Recurring loop

```sh
# 1) sync the changed runtime files (buspi's ~/open-california is a plain copy, NOT a git clone)
scp calictl/semantics.py calictl/control.py calictl/overrides.py protocol/dictionary.yaml \
    buspi:~/open-california/calictl/          # (dictionary.yaml -> ~/open-california/protocol/)

# 2) a live read/set needs the BLE slot — stop the daemon first (pw: raspberry)
ssh buspi 'echo raspberry | sudo -S systemctl stop calictl && sleep 1'

# 3) read or actuate (phone app CLOSED so buspi is sole controller)
ssh buspi 'cd ~/open-california && ~/solix-env/bin/python -m calictl get energy'
ssh buspi 'cd ~/open-california && ~/solix-env/bin/python -m calictl set cooler power on'

# 4) ALWAYS restart the daemon afterwards
ssh buspi 'echo raspberry | sudo -S systemctl start calictl && sleep 2 && systemctl is-active calictl'
```

## Manual runs need CALICTL_ADDR (post MAC-scrub)
The real BLE address is NOT in the code (it's a placeholder `AA:BB:CC:DD:EE:FF`); only the
systemd unit loads it from `/etc/buspi/calictl.env`. A manual `calictl` run gets the placeholder
and fails with `BleakDeviceNotFoundError`. Source it (without printing it) first:
```sh
export CALICTL_ADDR=$(echo raspberry | sudo -S grep -oP "(?<=CALICTL_ADDR=).*" /etc/buspi/calictl.env)
~/solix-env/bin/python -m calictl get cooler      # or pass --addr "$CALICTL_ADDR"
```

## Notes
- Long BLE ops (`get`, `set`, actuate) take ~10-60 s; run detached with a log + poll, or use
  `timeout 70`. `set` competes with the daemon for the slot — stop the daemon for clean reads.
- buspi can drop off the network (travel router); if `ssh buspi` times out, try the Tailscale
  IP, and if still down, report and wait rather than retrying forever.
- The APK lives at `buspi:~/apks/de.volkswagen.CaliforniaOnTour.apk` (gitignored source).
- `sudo` needs the Pi password `raspberry`; secrets in `/etc/buspi/*.env` (root, 0600).
- Grafana dashboards don't auto-update; push from buspi with `deploy/push_dashboard.py`.
