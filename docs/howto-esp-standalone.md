# How to run the satellite on its own

The ESP32 "satellite" — the M5Stack {term}`CoreS3 <Satellite>` board that pairs with your
{term}`camper unit` (see [ESP32 firmware](firmware.md)) — does not need the Raspberry Pi
({term}`buspi`). It updates itself from GitHub, keeps its own clock, writes every reading to its
SD card, and buspi collects that log later to fill gaps in its own history. This page is for the
everyday side: power, where to open it, updates, and what its status rows mean.

> **Status.** Running on its own since 2026-10-11: an update from GitHub and the automatic
> rollback were both tried on the board, its clock syncs over the internet, and the SD log works
> on a 32 GB card. buspi's collector for the log is merged (#320; set-up in
> [the Pi setup](raspberry-pi-setup.md#esp-ingest-gap-fill)). The background is in
> [ESP32 firmware → Running without buspi](firmware.md#running-without-buspi).

Before this page: the satellite has its firmware ([How to set up a new satellite](howto-esp-first-flash.md)),
is on your WiFi ([How to put the ESP32 satellite on your WiFi](howto-esp-wifi-setup.md)) and is
paired with the camper unit.

## Power it

Give the satellite its own **5 V USB-C supply** in the van (a USB-C phone charger or a 12 V → USB-C
socket adapter). It no longer needs a cable to the Pi.

The satellite **cannot tell you how much power it uses** — its power chip has no current meter. If
you want to know, put a small USB-C power meter between the supply and the board.

Put a **microSD card** in the CoreS3's card slot if you want the log (a FAT32-formatted card, as sold up
to 32 GB; a 32 GB card holds decades of it). Without a card everything else works; there is just no log.

## Where to open it

| Where you are | Open |
|---|---|
| In the van, on the van's WiFi | **<http://192.168.8.183>** (or **<http://calictl-esp.local>** if your device can open `.local` names) |
| Anywhere, with Tailscale on your phone or laptop | **<https://busnet.tail0e7f29.ts.net:8443/>** |

The **HTTPS address** goes through the van router's Tailscale, so it works only on your own
tailnet — nobody else on the internet can reach it. It also lets your phone install the page as an
app. If it doesn't load (the router is offline, or Tailscale is off on your phone), use the plain
address on the van's WiFi; that one always works there.

## Updating

Updates come over WiFi from the project's GitHub page — **only when you ask**; the satellite never
updates on its own.

1. Open the satellite's page, then **⋮ → Device status → Open setup**.
2. In the **Firmware update** row tap **Check for update**.
3. If a newer version is there, the button turns into **Install `<version>`**. Tap it.
4. The row counts up (*downloading … %*, *verifying …*, *installed, restarting …*). The satellite
   restarts; reload the page after a short while and it shows the new version.

**Just merged a change and it still says *up to date*?** GitHub sometimes hands out the previous
version for a minute or two after a change. Wait a moment and tap **Check for update** again.

Updates keep the pairing with the camper unit and the saved WiFi. They need the satellite on a
normal WiFi network with internet (not its own setup hotspot).

## What the status rows mean

You see these under **⋮ → Device status**, and on the **Open setup** page:

- **Storage** — the SD card. *SD 28.1 of 29.7 GB free · log #1042* means the card works; the
  number is the latest log entry, so it should go up while the camper unit is connected. *No SD
  card* means none is in (or it isn't detected — push it in again; the satellite checks every 5
  minutes). *SD card error* means a card is there but can't be used — reformat it as FAT32 on a
  computer, or try another card. *N records dropped* means the satellite couldn't write fast enough for a moment
  and some entries were lost; occasional small numbers are harmless.
- **Resources** — how busy the satellite is. *RAM 61 KB free (lowest 38)* is memory free now and
  the lowest since it started; *PSRAM … KB free* is its larger second memory; *CPU 4 %* is how busy
  the processor is. Nothing to do here unless something stops working — then these numbers help
  when reporting the problem.
- **Clock** (on the *Open setup* page) — where the satellite gets the time for its log. *Clock
  synced (SNTP)* is internet time, the best. *Clock from the camper unit* uses the camper unit's own
  clock, which is a few seconds behind — fine for the log. *Clock not synced yet* or *No clock
  source* happen briefly after a restart, or when there is no internet and the camper unit's clock
  is blank (it sometimes is for hours while the van is parked); entries from then get their time
  filled in later when possible. *boot 17* counts restarts.

## If it doesn't come back after an update

**It repairs itself.** A new version has to run cleanly for one minute before the satellite keeps
it. If it crashes or keeps restarting before then, the satellite goes back to the previous version
by itself. Give it two minutes, then reload the page: it shows the old version again. Tell the
project which version failed, and don't install that one again.

**Only if it is still dead after that** (the screen stays dark or it keeps restarting): it needs a
computer and a USB-C cable. Someone with the project checked out runs
`tools/esplab/flash_ci.sh <run-id> <port>` (described in `firmware/README.md`, "On-board
verification"). This writes the firmware over the cable and **keeps** the pairing and the WiFi.
Don't use the first-flash file `cali_fw_full.bin` for this — it erases both.
