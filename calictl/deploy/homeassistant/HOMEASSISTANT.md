# Home Assistant + HomeKit for the VW California (via calictl)

This turns `calictl serve` into a full Home Assistant integration: every
function (water, energy, cooler, campingmode, airheater, roof, …) shows up as
a device with sensors and, where writable, controls — and those controls are
exposed to Siri/Apple Home via HA's built-in HomeKit bridge. No custom HA
component needed: calictl publishes MQTT discovery, HA's `default_config`
auto-adds the entities, and `homekit:` re-exposes them.

**Ownership rule: HA never touches BLE.** `calictl serve` is the sole owner
of the Pi's one Bluetooth adapter (hci0) and the one BLE connection the
camper unit accepts. Home Assistant only ever talks MQTT (to the broker) and
HomeKit (to Apple Home) — it has no BLE role and should never be given one.
If you're tempted to add an HA Bluetooth integration for the same van,
don't: two BLE clients will fight over the single slot.

**Phone-app contention caveat:** the camper unit's BLE stack accepts exactly
one central connection. If the official VW app is connected on a phone at
the same time, calictl's connection attempts, polls, and commands will fail
(timeout/retry) until the app disconnects. This is expected, not a bug —
calictl retries and republishes state as soon as the slot frees up. Sensors
in HA will simply stop updating for that window rather than showing wrong
data.

## 0. Prerequisites

- buspi (or wherever you run the stack) already has Docker + docker-compose,
  and `/home/pi/open-california` checked out with `calictl/` deployed.
- The InfluxDB reader stack from `calictl/deploy/GRAFANA.md` is optional and
  independent of this — MQTT/HA doesn't need InfluxDB.

## 1. Copy the deploy assets

```bash
cd /path/to/your/monitoring/stack        # wherever your docker-compose.yml lives

mkdir -p mosquitto homeassistant
cp /home/pi/open-california/calictl/deploy/homeassistant/mosquitto.conf mosquitto/mosquitto.conf
cp /home/pi/open-california/calictl/deploy/homeassistant/configuration.yaml homeassistant/configuration.yaml
```

Then merge `docker-compose.snippet.yml` into your existing
`docker-compose.yml`: add the `mosquitto:` and `homeassistant:` services
under your top-level `services:`, and add `mosquitto-data:` under your
top-level `volumes:` (the snippet's trailing comment marks where).

## 2. Create the broker user

Mosquitto in this config requires auth (`allow_anonymous false`). Create the
password file the container will mount read-only:

```bash
docker run --rm -v "$PWD/mosquitto:/mosquitto/config" eclipse-mosquitto:2 \
  mosquitto_passwd -c -b /mosquitto/config/passwd calictl '<choose-a-password>'
```

This creates `mosquitto/passwd` with user `calictl`. Re-run without `-c` to
add additional users (e.g. one for Home Assistant itself) without wiping the
file.

## 3. Set credentials

**calictl side** — add to `/etc/buspi/calictl.env` (gitignored, instance-only):

```bash
MQTT_HOST=127.0.0.1
MQTT_PORT=1883
MQTT_USER=calictl
MQTT_PASSWORD=<the password you set above>
```

**Home Assistant side** — MQTT broker credentials are entered once through
the UI after first boot (Settings → Devices & Services → Add Integration →
MQTT → broker `127.0.0.1:1883`, same user/password), or via a `secrets.yaml`
in the `homeassistant/` config directory referenced by the commented-out
`mqtt:` block in `configuration.yaml`.

## 4. Bring up the containers

```bash
docker compose up -d mosquitto homeassistant
docker compose logs -f homeassistant   # wait for "Home Assistant initialized"
```

## 5. Install the calictl service

```bash
sudo cp /home/pi/open-california/calictl/deploy/calictl.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now calictl.service
journalctl -u calictl -f               # should show BLE connect + MQTT publish
```

`calictl serve` now polls the van over BLE, writes to InfluxDB (if
configured) *and* publishes MQTT discovery + state under `homeassistant/#`.

## 6. Entities auto-appear

Once both the broker and `calictl.service` are up, open Home Assistant →
Settings → Devices & Services → MQTT: a device per function (water, energy,
cooler, campingmode, airheater, roof, …) appears automatically with its
sensors and any controllable entities (switches/climate), no manual YAML
needed — that's what `default_config:` gives you.

## 7. Pair with Apple Home (HomeKit)

The `homekit:` block in `configuration.yaml` bridges `light`, `switch`, and
`climate` domains (the controllable van features) to HomeKit. After HA
restarts with that config:

1. Settings → Devices & Services → you'll see a "Home Assistant Bridge"
   integration with a **pairing PIN** (or check `docker compose logs
   homeassistant | grep -i homekit`).
2. On iPhone/iPad: Home app → Add Accessory → "I Don't Have a Code or Cannot
   Scan" → Home Assistant Bridge → enter the PIN.
3. Your van's controllable entities show up as HomeKit accessories, usable
   from the Home app or Siri ("Hey Siri, turn on the cooler").

Because HA runs `network_mode: host` (required in the compose snippet for
HomeKit's mDNS/Bonjour advertisement), no extra port mapping is needed.
