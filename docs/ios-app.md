# iOS app (planned)

A native iPhone app talks **directly to the {term}`camper unit` over Bluetooth**, looks like a
real iOS app, and takes voice commands through Siri and Shortcuts ({term}`App Intents`). Voice
control is what it adds over the vendor app. A user installs it and pairs it with the camper, with no
extra hardware. A user who later wants remote access (internet, later LoRa) and data while nobody
is at the van adds the {term}`satellite`. The app then finds the satellite and sets it up.

This page collects the owner's requirements and the architecture decisions for the app. The app
is not built yet: every requirement is `open` until its implementation and test exist.

## Phases

1. **Bluetooth app.** Direct link to the unit. Status and controls for everything this van has,
   except the pop-top roof, plus Siri and Shortcuts.
2. **Satellite upgrade.** Set up a freshly flashed satellite by QR code. From then on the app
   picks the best path by itself: Bluetooth near the van, the satellite over the van WiFi or
   Tailscale (later LoRa) far away.
3. **Later.** The pop-top roof (after its live test at the van), widgets, and distribution beyond
   the owner's iPhone.

## Requirements

### Connection and pairing

```{req} Direct Bluetooth link to the unit
:id: R_IOS_BLE_DIRECT
:status: open
:tags: ios

The app connects to the camper unit directly over Bluetooth Low Energy (CoreBluetooth). It needs
no satellite, no Pi and no network for any phase-1 function.
```

```{req} Guided first pairing
:id: R_IOS_PAIRING
:status: open
:tags: ios

The app guides the first pairing: it tells the user to start pairing on the camper panel and to
type the 6-digit passkey from the camper screen into the iOS pairing dialog. On a stale bond it
guides the user to *Settings → Bluetooth → Forget This Device* and back. (iOS runs the pairing and
owns the bond; an app cannot delete a bond itself.)
```

```{req} Protocol version 2 only
:id: R_IOS_V2_ONLY
:status: open
:tags: ios

The app reads the unit's CommunicationVersion. It supports version 2. On any other version it
shows "This camper unit is not supported yet" and sends no control write. It never writes the
version-3 characteristics `1603` or `F002`.
```

```{req} Respect the power-save hang-up
:id: R_IOS_POWER_SAVE
:status: open
:tags: ios

When the unit ends the link itself while camping mode is off and no shore power feeds it (the
{term}`power-save hang-up <Power-save hang-up>`), the app does not redial in a loop. It behaves
like the satellite's {term}`power-save rest <Power-save rest>`: a user action or a Siri command
starts a new connect.
```

```{req} Link held in the background while camping
:id: R_IOS_BACKGROUND_LINK
:status: open
:tags: ios

While camping mode is on, the app keeps the Bluetooth link open in the background (iOS
`bluetooth-central` background mode with state restoration). A command over the open link
reaches the unit in well under one second. When the iPhone leaves range, iOS reconnects by
itself when it comes back.
```

### Functions

```{req} Phase-1 functions
:id: R_IOS_FUNCTIONS
:status: open
:tags: ios

The app shows the unit's status (batteries, energy, water, temperatures, vehicle) and controls
camping mode (master, lights, rear USB), lighting (on/off, zones, favourites), the cooler, the air
heater and the energy mode, plus a device-status screen. It shows the same verified fields, with
the same labels and units, as the web UI.
```

```{req} No pop-top roof in phase 1
:id: R_IOS_NO_ROOF
:status: open
:tags: ios

Phase 1 has no roof control. Roof control comes after the roof is live-verified at the van.
```

```{req} Show only fitted equipment
:id: R_IOS_INSTALLED
:status: open
:tags: ios

The app shows a function only when the unit reports it as installed. It shows no "not installed"
rows or tiles.
```

```{req} Honest feedback
:id: R_IOS_HONEST
:status: open
:tags: ios

The app reports a change as applied only from the unit's truthful feedback. A write-through echo
of the state characteristic is never proof. When the unit cannot be reached, the app says so.
```

```{req} No VIN
:id: R_IOS_NO_VIN
:status: open
:tags: ios

The app never reads, shows, stores or sends the vehicle identification number.
```

### Siri and Shortcuts

```{req} Voice and Shortcuts actions
:id: R_IOS_SIRI
:status: open
:tags: ios

Siri and the Shortcuts app offer: camping mode on/off, lights on/off, light favourite A–D, and the
cooler on/off. The phrases exist in German and English.
```

```{req} No air heater by voice
:id: R_IOS_SIRI_NO_HEATER
:status: open
:tags: ios

Siri and Shortcuts offer no air-heater action. The fuel burner is switched only by a tap in the
app, under the app's heater gates, so a voice flow cannot cause short cycles.
```

```{req} Siri answers what really happened
:id: R_IOS_SIRI_HONEST
:status: open
:tags: ios

Siri confirms a command only when the unit confirmed it. Without an open link a command connects
first and takes a few seconds. When the van is out of reach, or the unit has just ended a link in
power-save, Siri says so ("try again in a few seconds") instead of failing silently.
```

### Look

```{req} Van-cockpit look
:id: R_IOS_LOOK
:status: open
:tags: ios

The start screen is a "cockpit": the second-battery level as a large ring, three small values
(fresh water, outside temperature, solar) and large buttons for camping mode, lights, cooler and
air heater, with tabs Cockpit / Energy / Water / Setup. Icons are {term}`SF Symbols`, never emoji.
Amber marks light, green marks "on". The app follows the iPhone's light and dark mode.
```

```{req} German and English
:id: R_IOS_I18N
:status: open
:tags: ios

Every text exists in English and German and uses the unit's own vocabulary (for example
*Flüstermodus*, *Sofortheizen*).
```

### Satellite upgrade (phase 2)

```{req} Satellite over its web API
:id: R_IOS_SATELLITE
:status: open
:tags: ios

The app finds a satellite on the local network by mDNS and talks to it over the same HTTP API as
the web UI (`/api/state`, `/api/command`, …). It picks the path in this order: Bluetooth when the
unit is in range, the satellite on the van WiFi, the satellite over Tailscale. Siri uses the same
order.
```

```{req} Set up a satellite by QR code
:id: R_IOS_QR_SETUP
:status: open
:tags: ios

A freshly flashed satellite shows a QR code on its screen. The user scans it with the app. The app
joins the satellite's first-time-setup WiFi hotspot and guides the WiFi setup, the camper pairing
and the sensors over it. The QR code carries no secrets: only the device id, the setup hotspot
(the standard `WIFI:` form with a one-time password) and the device's pairing endpoint.
```

```{req} Secrets only after a pairing code
:id: R_IOS_DEVICE_SECRETS
:status: open
:tags: ios

A device hands over its secrets (for example the keys for a LoRa network server) only after the
user enters the 6-digit code that the device shows on its screen for a short time. The device sends
them once; wrong codes and the timeout close the window. The app keeps the secrets in the iOS
Keychain. A setup link or QR code never overwrites stored secrets; only a new pairing does.
```

### Testing

```{req} Tested in tiers
:id: R_IOS_TESTED
:status: open
:tags: ios

The app is tested in tiers: the shared C core's existing tests; Swift unit tests for the bridge,
state decoding against the golden vectors, control frames against the control vectors, the
power-save rules against a fake transport and the Siri action resolution; UI and snapshot tests in
the simulator over a fake transport in light and dark; a real iPhone against the fake unit
peripheral; the van with an owner checklist; and a manual Siri checklist in both languages.
```

The checks of the Bluetooth link on a real iPhone are in {doc}`ios-app-radio-checklist`.
What a Mac needs to build and test the app is in {doc}`ios-development-setup`.

## Architecture decisions

```{decision} Bluetooth first, satellite as the upgrade
:id: AD_IOS_BLE_FIRST
:status: accepted
:tags: ios
:links: R_IOS_BLE_DIRECT, R_IOS_SATELLITE

The first version needs only the iPhone and the camper. The satellite is an optional upgrade
for remote access. The roof and a full replacement of the vendor app come last.
```

```{decision} Reuse the satellite's C core
:id: AD_IOS_REUSE_C
:status: accepted
:tags: ios
:links: R_IOS_BLE_DIRECT, R_IOS_TESTED

The app links the satellite's portable C code (`cali_core`) through a Swift package C target for
the codec, the control frames and the state decoding. So there is one protocol implementation for
the satellite and the app, tested by the same golden vectors. The session, power-save and pairing
flow are Swift code against CoreBluetooth, because iOS runs pairing and owns the bonds itself.
That code is tested against the same parity rules as the C session.
```

```{decision} The bond belongs to the iPhone
:id: AD_IOS_SHARED_BOND
:status: accepted
:tags: ios
:links: R_IOS_PAIRING

iOS keeps one bond per accessory for the whole phone. On an iPhone that already uses the vendor
app with this camper, the app uses that bond and needs no new pairing. A new user pairs once.
```

```{decision} SwiftUI, iOS 17 or later
:id: AD_IOS_SWIFTUI
:status: accepted
:tags: ios
:links: R_IOS_LOOK

The app is written in SwiftUI with Swift strict concurrency and targets iOS 17 or later (App
Intents with App Shortcuts, Observation).
```

```{decision} The app lives in this repository
:id: AD_IOS_SAME_REPO
:status: accepted
:tags: ios
:links: R_IOS_TESTED

The Xcode project lives under `ios/` in this repository, so one change can update the protocol,
the satellite and the app together, and CI tests them together. Personal signing data (team ID,
bundle-ID prefix) stays in a gitignored local configuration file. Certificates and provisioning
profiles are never committed.
```

```{decision} Same HTTP API for the satellite path
:id: AD_IOS_HTTP_API
:status: accepted
:tags: ios
:links: R_IOS_SATELLITE

The app uses the satellite's existing HTTP API, the one the web UI uses. No second protocol. The
satellite gets a dedicated mDNS service type so the app can tell it apart from other web devices.
The app's API types follow a checked API description, and a contract test runs the app's client
against the satellite's host build.
```

```{decision} The satellite is the only extra hardware
:id: AD_IOS_ESP_ONLY
:status: accepted
:tags: ios
:links: R_IOS_SATELLITE

The app's remote path targets the satellite only. The Raspberry Pi serves as a development and
test counterpart, not as a product path.
```

```{decision} Prototype on a free developer account
:id: AD_IOS_FREE_ACCOUNT
:status: accepted
:tags: ios
:links: R_IOS_QR_SETUP

The prototype runs as a development build on the owner's iPhone with a free Apple account: it is
re-installed every 7 days. App Intents and the Bluetooth background mode work without a paid
account. Widgets (App Groups) and joining a WiFi from the app (Hotspot Configuration) need the paid
account and come with it. Until then the setup QR code uses the standard `WIFI:` format, so the
iPhone camera can join the satellite's hotspot.
```

```{decision} Requirement tags in Swift doc comments
:id: AD_IOS_TRACE
:status: accepted
:tags: ios
:links: R_IOS_TESTED

Swift code names the requirements it implements in its doc comment with a `- Requirement:` line,
and each test names itself and what it verifies with `- Test:` and `- Verifies:` lines. Xcode shows
them in Quick Help. A repository tool reads the Swift sources and writes a needs file that the docs
build imports, so every `R_IOS_*` requirement links to its code and tests, with a link to the
source line. A guard test fails on an unknown requirement id. (The general code-linking extension
for sphinx-needs does not parse Swift.)

    /// Sends the camping-mode command over the best path.
    /// - Requirement: R_IOS_SIRI, R_IOS_SIRI_HONEST
    struct SetCampingModeIntent: AppIntent { … }

    /// - Test: T_IOS_SIRI_CAMPING
    /// - Verifies: R_IOS_SIRI
    @Test func campingPhraseResolvesToMasterOn() { … }
```

## Verified early

These points decide the shape of phase 1 and are checked on the real radio before anything else:

- A control write from the iPhone while the satellite holds its own link and runs its own `1003`
  heartbeat.
- That iOS answers the unit's ATT MTU request (the unit drops a central after 30 s without an
  answer).
- That App Intents and the Bluetooth background mode work on the free account.
