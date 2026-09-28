# How to put the ESP32 satellite on your WiFi

This walks you through connecting the ESP32 "satellite" — the small M5Stack CoreS3 board that
pairs with your camper unit on its own, without the Raspberry Pi (see [ESP32 firmware](firmware.md))
— to your WiFi, so you can see its status page from a phone or laptop.

> **Status — read this first.** The CoreS3 board has not arrived yet, so none of this has run on
> real hardware. Everything below is proven on a Linux build of the same firmware with a
> simulated WiFi radio, and on an emulated chip. The steps describe how the firmware is built to
> behave; the list of things still to confirm on the board is in
> [ESP32 firmware → Network watch items](firmware.md#network-watch-items-board-only).

The satellite only **reads** the camper unit. Its page shows what the unit reports and the
device's own state (Bluetooth pairing, link, WiFi); it has no controls.

## What you need

- The satellite, powered (USB-C).
- A phone, tablet or laptop with WiFi.
- The name and password of the WiFi network you want the satellite on — a **2.4 GHz network with
  a password** (WPA2-Personal). The ESP32-S3 radio has no 5 GHz, and **open networks (no
  password) are not supported**: the password must have 8–63 characters.
- For the fallback only: a USB-C cable to a computer and a serial terminal (see
  [No phone? Use the USB console](#no-phone-use-the-usb-console)).

## First-time setup

A satellite with no saved WiFi opens its own setup hotspot as soon as it boots.

1. **Join the setup hotspot.** On your phone, open the WiFi settings and join
   **`calictl-esp-setup`**. Its password is **`calictl-setup`**.
2. **The setup page opens by itself.** Right after you join, your phone checks whether the network
   needs a sign-in and finds the satellite's page instead — a "Sign in to network" notice or a
   small browser window pops up. Tap it.

   **The page doesn't pop up?** Open a browser and go to **http://192.168.4.1** (plain `http`, no
   `s`). That is always the satellite's address on its own hotspot.
3. **Choose your network and type its password.** The page lists the networks the satellite can
   see. Pick yours, type the password, tap
   **Connect**. If your network isn't listed, tap **Search again**.

   ![The satellite's setup page on its hotspot: network list, password, Connect](screenshots/esp-setup-page.png)

4. **Wait for "Connected as …".** The page says *Connecting to …*, and once the satellite has
   joined your network it shows *Connected as 192.168.x.y. Join <your network> with this device
   too, then open:* followed by a link to **http://calictl-esp.local**.

   If the password was wrong, the page says *Could not connect — check the password and try
   again.* The satellite forgets the wrong password and keeps its setup hotspot open, so you can
   simply try again.
5. **Switch back.** About 30 seconds after the satellite joins your network it closes the setup
   hotspot. Put your phone back on your own WiFi (most phones do that on their own) and open
   **http://calictl-esp.local**.

From now on the satellite joins your network by itself every time it starts.

## Finding the satellite on your network

Open **http://calictl-esp.local** from any device on the same network. The page refreshes every
2 seconds:

![The satellite's status page on the home network: device state and the camper unit's functions](screenshots/esp-status-page.png)

*Device* shows the Bluetooth pairing, whether the link to the camper unit is up and how old the
last reading is, the WiFi network, IP address and signal, uptime and firmware version. *Camper
unit* lists each function the unit reports, with the raw field values (the figure shows two; the
real page lists every function the unit reports).

**The name doesn't resolve?** Some devices (older Android phones in particular) can't open
`.local` names. Find the satellite's address instead:

- in your **router's list of connected devices** — it registers as `calictl-esp`; or
- on the **USB console**, type `wifi status` — the answer contains `ip=192.168.x.y`.

Then open `http://192.168.x.y` directly.

## If your network is out of reach

When the saved network disappears (you drove off, the router restarted), the satellite keeps
trying to rejoin — after 1 s, then 2 s, 4 s and so on, up to once a minute — and never forgets the
saved network on its own. If it still hasn't rejoined after **5 minutes**, it **also** opens the
setup hotspot `calictl-esp-setup` again, while it keeps retrying the saved network in the
background. So at a new campsite you can join the hotspot and give it the new network (next
section) — or do nothing, and it rejoins your network when you're back in range (the hotspot then
closes 30 seconds later).

## Changing networks, or forgetting the WiFi

- **A different network:** join the setup hotspot when it is open (above) and pick the new network
  on the page — or, on the USB console, `wifi set <network> <password>` at any time. The new
  details replace the old ones and the satellite joins with them right away. If they are wrong it
  behaves like a first-time setup: it forgets them and opens the setup hotspot, and the old
  network is **not** restored.
- **Forget the WiFi completely:** on the USB console type `wifi forget`. The satellite drops the
  saved network and opens the setup hotspot, just like a fresh one.

## No phone? Use the USB console

Everything above can also be done over the USB-C cable. The CoreS3's USB-C port shows up on your
computer as a serial port — `/dev/cu.usbmodem…` on a Mac, `/dev/ttyACM0` (or similar) on Linux.
Open it with a serial terminal, for example `idf.py -p /dev/cu.usbmodemXXXX monitor` (from ESP-IDF)
or `screen /dev/cu.usbmodemXXXX` (quit `screen` with Ctrl-A then K). Type a command and press
Enter:

| Command | What it does |
|---|---|
| `wifi set <network> <password>` | Save the network and join it. The network name must be a single word here — a name with spaces can only be chosen on the setup page. |
| `wifi status` | One line: `LOG wifi: <setup\|station\|off> ssid=… ip=… rssi=… scan=<n>` (`-` where there is nothing to report). |
| `wifi forget` | Forget the saved network and reopen the setup hotspot. |
| `wifi scan` | Look for networks again (the setup page's list). |
| `status` | The Bluetooth pairing state, plus a `"wifi"` part with mode, network and IP. |

The satellite **echoes nothing you type** — you type blind — and **never prints the password
back**. The replies you'll see:

- `LOG wifi: joining <network>` then `LOG wifi: online 192.168.x.y` — it worked.
- `LOG wifi: failed auth` (wrong password), `LOG wifi: failed not_found` (network not in range),
  `LOG wifi: failed other`.
- `LOG wifi: bad psk` — the password is not 8–63 characters; `LOG wifi: bad ssid` — the name is
  longer than 32 characters; `LOG wifi: usage: wifi set <ssid> <psk>` — one of the two is missing
  or the name has a space.
- `LOG wifi: credentials replaced, reconnecting` — you replaced a saved network.
- `LOG wifi: setup hotspot up (calictl-esp-setup)` / `LOG wifi: setup hotspot closed`.

## What is stored, and where

- **On the satellite:** your network's name and password, in the board's flash memory (the
  settings area, NVS), **in plain text** — not encrypted. They stay there across restarts until
  you replace them or type `wifi forget` (or a newly typed password turns out wrong). Nothing
  about your WiFi is sent anywhere else.
- **The setup hotspot's password `calictl-setup` is the same on every satellite and printed in
  these docs.** While the hotspot is open (first setup, after `wifi forget`, or after 5 minutes
  without the saved network), anyone in range can join it and read or change the WiFi settings.
  The page and the USB console have no login. This follows the project owner's stance that every
  device on the local network (and near the van) is trusted.

## Troubleshooting

| What you see | What to do |
|---|---|
| No `calictl-esp-setup` network appears | The satellite already has a saved network and is on it (or still inside its first 5 minutes of retrying). Open http://calictl-esp.local, or type `wifi status` on the USB console; `wifi forget` reopens the hotspot. |
| Joined the hotspot, no page pops up | Open **http://192.168.4.1** in a browser. Some phones only show the sign-in notice once; others open it in a small window you have to tap. |
| Your network is missing from the list | Tap **Search again**. 5 GHz-only networks, and networks that hide their name, never appear — use a 2.4 GHz network; for a hidden one, try `wifi set` on the USB console (untested). |
| "The password needs 8–63 characters." | The password is too short or too long. An open network (no password) cannot be used. |
| "Could not connect — check the password and try again." | Wrong password, or the network was out of reach. The satellite is back on its setup hotspot; try again. |
| "Device not reachable" banner on the page | The page lost contact with the satellite: your phone left its network (e.g. the setup hotspot closed after the satellite joined your WiFi). Rejoin the right network and reload. |
| http://calictl-esp.local doesn't open | Use the IP address from your router's device list or `wifi status` (see [Finding the satellite](#finding-the-satellite-on-your-network)). |
| The page is slow with several tabs open | The satellite answers one request at a time. Keep one tab open. |
| *Link to the camper unit: not connected* | That is the Bluetooth side, not WiFi: the satellite is not paired yet, the camper unit is asleep, or another device (the Pi, the app) holds the unit's only Bluetooth connection. Pairing is done on the USB console (`pair`, then `passkey <code>`) — see [ESP32 firmware](firmware.md). |
