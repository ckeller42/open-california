# How to pair your camper unit

This walks you — another T7 owner running open-california — through bonding your Raspberry Pi
("buspi" in the rest of the docs; call yours whatever you like) to your camper control unit over
Bluetooth LE, using the web UI's guided pairing wizard. It assumes you already installed
open-california (see [Raspberry Pi setup](raspberry-pi-setup.md)) and are standing at the van.

> **Note.** This page covers the **web UI wizard** — the way to pair (or re-pair) once the
> daemon is installed and running. The installer's own one-time pairing step (`install.sh`,
> driven from a terminal) is documented in
> [Raspberry Pi setup](raspberry-pi-setup.md#the-pairing-step); both end up writing the same
> bond to the same pairing cache, so you only need one of them.

## What you need

- open-california installed and running as a service (`systemctl status calictl` shows
  `active`).
- The web UI open in a browser on a device on the same network as the Pi (`http://<pi>:8080` by
  default, or whatever port you configured — see
  [Raspberry Pi setup → Web UI](raspberry-pi-setup.md#web-ui)).
- You, standing inside the van, with the camper control unit switched on.

## Before you start

The camper control unit accepts exactly **one** Bluetooth connection at a time, and pairing
needs the Pi's radio to be free to scan and connect without another Bluetooth client
interfering. Before you open the wizard:

- **Close the California On Tour app, or turn off your phone's Bluetooth.** If the phone is
  connected to the unit, the Pi cannot connect to it at all.
- **Pause any other Bluetooth software on the Pi for the few minutes pairing takes** — for
  example the Home Assistant Bluetooth integration, or any other BLE reader you have running
  (Anker, Victron, Govee, …). A client that keeps discovery running on the Pi's adapter after
  the wizard's own scan has stopped makes the next connection attempt likely to fail; see
  [Environment the wizard needs](business-logic/guided-pairing.md#environment-the-wizard-needs)
  for why.

## Pair, step by step

1. **On the camper control unit**, open **Einstellungen → Bluetooth → Gerät verbinden** (the
   unit's menus were only seen in German for this project — the English-UI wording is unknown;
   look for the equivalent "Bluetooth" / "connect device" screen if your unit is set to English).
   It shows **"Passcode: ---"** until buspi connects to it.
2. **In the web UI**, open the **⋮** menu (top right) → **Bluetooth pairing…**. Tick
   **"I'm on that screen"**, then press **Connect now**.
3. The unit's `---` changes to a **6-digit passcode**. Type that number into the web UI's
   passcode field and press **Send**. (A fresh passcode is shown for every attempt — always
   read the number currently on the unit's screen, not one you remember from a previous try.)
4. The wizard shows **"✓ Paired — "** followed by the unit's Bluetooth address. You're done —
   the bond is saved to the daemon and survives a reboot; no further action is needed.

## If something goes wrong

If pairing fails, the wizard shows one of a small set of messages, plus a hint for what to do
next:

| Wizard message | What it means | What to do |
|---|---|---|
| **"No vehicle found."** | The Pi never saw the unit advertising within the scan window. | Check that "Gerät verbinden" is open on the unit, that buspi is in range, and that no phone is connected to the unit. |
| **"Could not connect to the unit."** | The unit was found, but the Bluetooth connection itself failed. | The unit may be asleep, a phone may still hold its single connection, or another app on this Pi keeps Bluetooth scanning. Wake the unit at its panel, disconnect the phone, pause other Bluetooth apps, then try again. An existing bond is kept. |
| **"Pairing was refused."** | The unit rejected the passcode or the pairing request. | Wrong passcode, or the unit left pairing mode. Reopen "Gerät verbinden" on the unit and try again. |
| **"Could not verify the bond."** | Bluetooth-level pairing succeeded, but the unit didn't answer calictl's own follow-up reads. | The bond was made but the unit did not answer. Try again; if it repeats, use Bluetooth reset / re-pair. |
| **"Something went wrong. Try again."** | A fallback for an error code this build doesn't have specific wording for. | Try again; if it repeats, use Bluetooth reset / re-pair. |
| **Banner: "Another app on this Pi keeps Bluetooth scanning — pairing will likely fail until it stops (for example the Home Assistant Bluetooth integration)."** | Something else on the Pi is holding the radio in discovery. | Stop that other Bluetooth software (see *Before you start*), then press **Try again**. |

Each error step has a **Try again** button that restarts the flow from scanning, and (once a
bond exists to lose) a **Bluetooth reset / re-pair** button that clears calictl's half of a
broken bond and drops you back at the checklist to pair again.

## After the unit's Bluetooth was reset

If you (or a workshop) run **"Bluetooth zurücksetzen"** on the camper control unit itself, the
unit forgets every phone and Pi it ever bonded with — including buspi. The next time calictl
tries to connect it will fail (the unit no longer recognizes the old bond), but nothing is
broken: open the pairing wizard and pair again as above. calictl detects that its own stored
bond is now stale and clears it automatically before re-pairing, so you don't need to do
anything manually first.

## Why the address changes

The camper unit doesn't advertise from a fixed Bluetooth address — like most modern BLE
devices, it rotates to a private, changing address every so often for privacy. Only once
you've bonded does the unit expose its stable **identity** address, which is what calictl
learns and stores during pairing (in the pairing cache, shown in the wizard's "✓ Paired — "
line). Seeing the address change between pairing attempts, or between scans before you've
paired, is expected and needs no action — the identity address behind it is what stays fixed.
