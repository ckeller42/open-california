# Set up the app lab

The {term}`app lab <App lab>` runs the vendor's CaliforniaOnTour Android app in an emulator and
connects it to a {term}`fake unit <Mock unit>` instead of the van. The emulator's virtual Bluetooth
controller (netsim) is bridged to a Python peripheral on the [Bumble](https://google.github.io/bumble/)
stack, and that peripheral serves calictl's own protocol model. You see every app screen in any
state you inject, and every frame the app writes lands in the fake's log for a diff against
`control.build()`.

- What the fake unit is and how to drive it: {doc}`mock-unit-guide`.
- Where the lab sits among the test layers, and how app recordings are made and replayed:
  {doc}`simulation-and-testing`.
- Every variable, the scenario console, recording with `walk.py` and the known netsim limits:
  [`tools/applab/README.md`](https://github.com/ckeller42/open-california/blob/main/tools/applab/README.md).

The lab is local tooling. Nothing from it goes into the repository (see [Privacy](#privacy)).

## Prerequisites

The preferred lab host is a Linux x86_64 machine with KVM. There the emulator runs the
`google_apis;x86_64` Android 34 image natively and headless, and it runs the app's arm64 libraries
through the image's ARM translation. An Apple Silicon Mac works too (see
[On macOS](#on-macos)).

`tools/applab/setup_linux.sh` checks each of these and stops with the exact command when one is
missing:

| Requirement | Why | How to fix it |
|---|---|---|
| Linux on x86_64 | the image is x86_64 | — |
| `/dev/kvm` exists | hardware acceleration | enable virtualisation in the firmware setup |
| your user is in group `kvm` | read/write access to `/dev/kvm` | `sudo usermod -aG kvm $USER`, then log in again |
| linger is on | netsim keeps `netsim.ini` in `$XDG_RUNTIME_DIR`, which systemd removes when the last ssh session ends | `sudo loginctl enable-linger $USER` |
| Java 17, `unzip`, `Xvfb`, `rsync` | SDK tools, the headless display, the lab copy | installed by the script when `sudo -n` needs no password; otherwise `sudo apt-get install -y openjdk-17-jre-headless unzip xvfb rsync` |

You also need ssh access to `pi@buspi` (the script copies the app's split APKs from
`~/apks/<version>/` there; `APK_SRC` names another `scp` source) or to an existing lab host (see
[Move a lab](#move-a-lab-to-another-host)).
The SDK and one system image need about 5 GB of disk.

## Set up a lab on Linux

Run the setup script from an open-california checkout on the lab host:

```sh
tools/applab/setup_linux.sh
```

The script is idempotent: every step checks before it acts, so you can run it again at any time.
It installs everything under `~/android-lab` (`LAB_DIR`):

| Item | Where |
|---|---|
| Android SDK, emulator, platform tools, the Android 34 `google_apis;x86_64` image | `sdk/` |
| the AVD `lab34` (a `pixel_6`) | `avd/` |
| a Bumble venv with a pinned Bumble, `grpcio`, `protobuf` and `pyyaml` | `venv-bumble/` |
| the app's split APKs of `APK_VERSION` (default: the version pinned in `setup_linux.sh`) | `apks/<version>/` |
| the environment file | `env.sh` |

`env.sh` exports `LAB_DIR`, `AVD`, `BUMBLE_PY`, the Android SDK variables and `PATH`, and
`FAKE_UNIT_KEYSTORE=$LAB_DIR/fake_unit_keys.json`. The fake unit keeps its bond keys in the lab,
because the bond belongs to the AVD and not to a repository checkout.

If buspi is offline, the script skips the APK copy and prints the `scp` command to run later.

A fresh lab has no VIN yet. Choose any syntactically valid 17-character VIN for the test vehicle
and store it in the lab, so that `labctl.sh` can read it:

```sh
install -m 600 /dev/null ~/android-lab/vin
$EDITOR ~/android-lab/vin       # one line: the VIN you type into the app
```

Never print the VIN and never commit it. Then install the app once:

```sh
. ~/android-lab/env.sh
tools/applab/labctl.sh install  # boots the emulator, installs the newest APK version
```

Do the [first pairing](#first-pairing) next.

## Move a lab to another host

To move an onboarded lab, give the old host's ssh name in `LAB_FROM`. Stop the emulator there first
(`tools/applab/labctl.sh down`), then run on the new host:

```sh
LAB_FROM=<old-host> tools/applab/setup_linux.sh
```

The script copies the AVD, `vin`, `fake_unit_keys.json` and `apks/` from `~/android-lab` on the old
host with `rsync`, and points the copied AVD at the new path. The app keeps its onboarding and its
bond with the fake unit, so no new pairing is necessary. The copy happens only when the AVD does not
exist yet on the new host.

## Daily use

Source `env.sh` once per shell, then use `tools/applab/labctl.sh`:

```sh
. ~/android-lab/env.sh
tools/applab/labctl.sh up
```

| Command | What it does |
|---|---|
| `labctl.sh up` | starts the emulator if it is down, starts the fake unit if it is down, launches the app; idempotent |
| `labctl.sh status` | shows whether the emulator, the fake unit and `netsimd` run, and the scenario-console command line |
| `labctl.sh fake` | restarts only the fake unit (`SIGTERM`, then a new start) |
| `labctl.sh install` | boots the emulator if necessary, installs the newest APK version as an update, grants the location permission |
| `labctl.sh down` | stops the fake unit with `SIGTERM`, kills the emulator, then stops `netsimd` |

On Linux, `labctl.sh` boots the emulator headless with `-gpu swangle_indirect` (ANGLE over
SwiftShader Vulkan) on a private X display `Xvfb :120`, which it starts when it is not up.
`LAB_GPU` and `LAB_DISPLAY` override both. The other GPU modes crash the emulator when the app
renders its home screen; the README has the details.

The fake unit's state lives in `${TMPDIR:-/tmp}/applab/`: `fake_unit.log` (every `READ` and
`WRITE`, the `WRITE` lines are the app's real frames), `fake_unit.pid`, `emulator.log` and the
scenario console FIFO `fake_unit.in`:

```sh
echo "set vehicle TerminalOneFive=1" > "${TMPDIR:-/tmp}/applab/fake_unit.in"   # ignition on
```

Always stop the lab with `labctl.sh down`, never with `kill -9`. A hard-killed fake leaves a ghost
radio in netsim, and the app then connects to the dead one.

## First pairing

Pair the app with the fake unit once per AVD. With the lab up (`labctl.sh up`):

1. In the app, finish the onboarding, open the **Vehicle** tab and tap **Add vehicle**.
2. Enter the VIN from `~/android-lab/vin`. The online check fails; choose the model **California**
   and the equipment **Ocean** by hand.
3. Tap **Set up remote control**, allow the nearby-devices permission, and tap **Connect now**.
4. Android posts a **Pairing request** notification. You have about 30 seconds: open the
   notification shade and tap it.
5. Read the passkey from the line `### PASSKEY nnnnnn` in `${TMPDIR:-/tmp}/applab/fake_unit.log`.
6. Type the passkey and tap **OK**.

The app then reads every function of the fake unit. Over adb the same steps are:

```sh
adb shell cmd statusbar expand-notifications; python3 tools/applab/adbui.py tap "Pairing request"
adb shell input tap 536 988; adb shell input text <passkey>; python3 tools/applab/adbui.py tap "^OK$"
```

`tools/applab/pair_wizard.py` walks the wizard sheets and types the passkey for you.

The bond persists on both sides: in Android's bond store and in the fake's keystore
`~/android-lab/fake_unit_keys.json`. After `labctl.sh fake` or an app restart the app reconnects
without a new passkey. If the app asks for a passkey again ("Connection failure" and a new
`### PASSKEY` line in the log), tap **Try again** and repeat steps 4 to 6.

## Update the app

The lab installs the same split APKs that the owner's phone runs. They are on buspi in
`~/apks/<version>/` (`base.apk` and the `split_config.*.apk` files). A fresh `setup_linux.sh`
copies the version in `APK_VERSION`. For a newer version, copy its directory to the lab host
through a machine that reaches both (buspi cannot ssh to the lab hosts):

```sh
scp -3 'buspi:~/apks/<version>/*.apk' <lab-host>:android-lab/apks/<version>/
```

Then install it on the lab host as an update, so the app keeps its data (the onboarding and the
VIN):

```sh
tools/applab/labctl.sh install                         # the newest version directory
APK_VERSION=<version> tools/applab/labctl.sh install   # a specific one
```

The command prints `versionName` and `primaryCpuAbi` (`arm64-v8a`, through the ARM translation).
The fake unit needs no change. The Android bond can need a new pairing after an update.

## On macOS

An Apple Silicon Mac runs the `google_apis;arm64-v8a` image instead. There is no setup script: the
README's "One-time setup (macOS, Apple Silicon)" section installs the SDK, the AVD `cali34` and
the Bumble venv by hand. `labctl.sh` uses these defaults on macOS:

| Setting | macOS default |
|---|---|
| `LAB_DIR` | `/Volumes/External/android-lab` |
| `AVD` | `cali34` |
| `LAB_GPU` | `swiftshader_indirect` (no Xvfb) |

`labctl.sh up`, `status`, `fake`, `install` and `down` work the same way. Without a `vin` file in
`LAB_DIR`, export `FAKE_UNIT_VIN` before `labctl.sh up`. macOS can revoke the terminal's access to
an external volume during a session. Then grant the terminal app *Files and Folders → Removable
Volumes* again.

## Privacy

- **Never commit the APK, the VIN or anything from the emulator.** The APKs, the SDK, the AVD and
  the VIN stay in `LAB_DIR`, outside the repository. The pre-commit hook refuses a 17-character VIN
  in a tracked file.
- **App screenshots go only to the owner's private reverse-engineering repository.** Keep
  `APPLAB_SHOTS` (the screenshot directory of `adbui.py`) outside the checkout. open-california
  gets text and links, never app screenshots.
- Use the lab only with your own account and your own vehicle data.
