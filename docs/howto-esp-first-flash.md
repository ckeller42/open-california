# How to set up a new satellite

This gets the firmware onto a brand-new ESP32 "satellite" — the M5Stack {term}`CoreS3 <Satellite>`
board that pairs with your {term}`camper unit` on its own (see [ESP32 firmware](firmware.md)). You do
this **once per board**, over a USB cable. After that the satellite updates itself over WiFi.

> **Warning — only for a new (or wiped) board.** The full image below overwrites the board's
> settings area (NVS): the **Bluetooth pairing with the camper unit** and the **saved WiFi** are
> erased. That is fine on a new board. On a satellite that already works, **do not** use it:
>
> - to update, use the *Firmware update* row instead (below);
> - to recover a board over USB without losing its settings, use `tools/esplab/flash_ci.sh`
>   (see `firmware/README.md`, "On-board verification").

## What you need

- An **M5Stack CoreS3** (the ESP32-S3 model with **16 MB** flash). Other boards are not supported.
- A **USB-C cable that carries data** — some cables only charge. If the computer never sees the
  board, try another cable first.
- A computer with either **Chrome or Edge** (for the browser flasher), or **Python** (for the
  command line).

## 1. Download the firmware

Open the release page
[**esp-latest**](https://github.com/ckeller42/open-california/releases/tag/esp-latest) and
download **`cali_fw_full.bin`** (about 2 MB). It is rebuilt from the latest code on every change.

The other files there (`cali_fw.bin`, `manifest.json`) are for the satellite's own updates — not
for this step.

## 2. Flash it

Plug the CoreS3 into the computer with the USB-C cable. Then use **one** of these two ways.

### In the browser (Chrome or Edge)

1. Open Espressif's browser flasher: <https://espressif.github.io/esptool-js/>.
2. Click **Connect** and pick the CoreS3's port in the list the browser shows (on a Mac it is
   called something like `usbmodem…`, on Windows a `COM` port).
3. Set the flash address to **`0x0`**, choose the downloaded `cali_fw_full.bin` as the file, and
   click **Program**.
4. Wait until it says it is done (about a minute), then press the CoreS3's reset button once.

### On the command line

```
pip install esptool
esptool --chip esp32s3 write_flash 0x0 cali_fw_full.bin
```

esptool finds the board on its own. If you have several serial devices plugged in, name the port
with `-p` (for example `-p /dev/cu.usbmodem1101` on a Mac or `-p /dev/ttyACM0` on Linux). On Linux
your user needs to be in the `dialout` group to open the port.

### The port doesn't show up?

Put the board into **download mode** by hand. M5Stack's instructions for the CoreS3: press and hold
the **reset button** (about 3 seconds) until the **green LED** lights up, then let go — the green
LED goes off, and the board is now in download mode. Connect again (browser) or run the command
again. Afterwards press reset once more to start the new firmware.

## 3. Next steps

When the satellite starts, its screen shows the setup hotspot. Carry on with:

1. [How to put the ESP32 satellite on your WiFi](howto-esp-wifi-setup.md)
2. [How to pair your camper unit](howto-pair-your-camper.md) (the satellite has the same pairing
   wizard as the Pi — see "Pair the satellite with your camper unit" in the WiFi how-to)

## Updates afterwards

You don't need the cable again. On the satellite's page open ⋮ → **Device status** → **Open
setup**, and in the *Firmware update* row tap **Check for update**, then **Install `<version>`** when
one is available (updates need your home WiFi, not the setup hotspot). The update keeps your pairing and WiFi (details:
[Firmware update from GitHub](firmware.md#firmware-update-from-github-r_fw_ota)).
