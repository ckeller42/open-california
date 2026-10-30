# iOS app: real-radio checklist

The simulator has no Bluetooth, so the unit tests cover only the pure parts of the CoreBluetooth
transport (`CoreBluetoothTransport`): the disconnect mapping, the pairing errors, the Bluetooth
states, the choice of the unit and the bookkeeping of the calls in flight. This checklist covers
the link itself. Run it on a real iPhone at the van.

## How the transport works

- **Finding the unit.** The app connects only to the unit that it adopted, by the identifier that
  iOS gives it (iOS resolves the rotating address with the bond). Without an adopted unit it
  connects to nothing until the user starts the first pairing. The first pairing scans for 3
  seconds for the general service `00001000-6c77-4b7d-bbf6-a5e587701f3d` and lists each unit with
  the exact name `VWCAMPER`. The user picks one, also when there is only one.
- **Encryption.** After service discovery the app reads `1004`, the authenticated read, on every
  link. Only a successful read makes the link usable. A link without `1004`, or with a failed
  read, ends at once.
- **Adoption.** The app adopts a new unit only when the user picked it, and only when the
  authenticated read of `1004` succeeds. iOS runs the passkey pairing inside that read, or uses a
  bond that the iPhone already has. The app stores the unit only after that read. The pick is good
  for one connection: a link that ends without the adoption, a cancel or a timeout ends the first
  pairing.
- **Passkey rule.** The unit refuses Just Works pairing. A new pairing with the real unit always
  shows the iOS passkey dialog, and its 6-digit code must match the camper screen. A pairing
  prompt without a code is not the camper: cancel it.
- **Hang-up.** The unit ends a link with HCI 0x13. iOS reports it as
  `CBError.peripheralDisconnected`, and the session treats it as the unit's hang-up.
- **Writes** use "write with response". A write that went out before the link dropped reports
  "unconfirmed", never "failed".

## Before you start

1. Build and install the app on the iPhone from the Mac `bar` (the signing works only in its GUI
   session: start the build script with `ssh bar 'open -a Terminal <script>'`).
2. Make sure that the app's `Info.plist` has `NSBluetoothAlwaysUsageDescription` and
   `UIBackgroundModes` with `bluetooth-central`. Without them nothing below works.
3. Open the Console app on the Mac, select the iPhone, and filter on the app's process. Keep it
   open for every step.

## Checks

Do the checks in this order. Each check names what to see.

### Discovery and pairing with the passkey

1. On the iPhone, open *Settings → Bluetooth* and make sure that no bond to the unit exists.
2. On the camper panel, start the pairing.
3. Open the app and start the connection.
4. Check that the app lists the unit with the name `VWCAMPER`. If it does not, record the name
   that the unit advertises and whether it advertises the general service: the scan filter must
   then change.
5. Pick the unit. Check that iOS shows its own pairing dialog with a field for a 6-digit code.
6. Type the 6-digit passkey from the camper screen into the dialog.
7. Check that the app shows the unit's state after the pairing.
8. Check that the app connects for at least 60 seconds while the dialog is open, so that there is
   time to type the passkey.
9. Cancel the pairing dialog once (repeat steps 1–5). Check that the app shows "The camper unit
   asks for pairing.", does not connect in a loop, and needs a new pick for the next try.
10. Close the app and open it again. Check that it connects without a scan and without a dialog.

### A bond from the vendor app

1. Use an iPhone on which the vendor app is already paired with the unit, and no adopted unit in
   this app.
2. Start the first pairing in the app and pick the unit.
3. Check that no dialog appears, because the bond exists, and that the app adopts the unit and
   shows its state.

### No unit without proof

1. Run the fake unit peripheral without the `1004` characteristic.
2. Start the first pairing in the app and pick the fake unit.
3. Check that the app ends the link after the service discovery or the read, that it never shows
   a state, and that it stores no unit.

### The MTU answer (link with camping on)

1. Switch camping mode on.
2. Connect with the app and keep the app in the foreground.
3. Wait at least 60 seconds.
4. Check that the link stays up after 30 seconds. iOS answers the unit's MTU request; a link that
   drops at about 30 seconds means it did not.

### A write while the satellite holds its own link

1. Make sure that the satellite is connected to the unit and runs its heartbeat.
2. Connect with the app.
3. Switch the lights on from the app, then off.
4. Check that the lights change and that the app reports the result from the unit's own push.
5. Check in the satellite's log that its link stays up.

### The hang-up with camping off

1. Switch camping mode off. Make sure that no shore power is connected.
2. Connect with the app.
3. Check that the unit ends the link about 30 seconds after the connection.
4. Check that the app does not redial by itself.
5. Start a command from the app. Check that it connects again and that the command goes out.
6. Start a command a few seconds before the expected hang-up. Check that the result is
   "unconfirmed" or "failed" and never "done".

### The background hold with the screen locked

1. Switch camping mode on and connect with the app.
2. Put the app in the background and lock the screen.
3. Wait 5 minutes.
4. Run a Siri or Shortcuts action (for example lights on). Check that it reaches the unit in well
   under one second.
5. Walk out of range for 2 minutes and come back. Check that iOS reconnects by itself.

### State restoration after the app is killed

1. Switch camping mode on and connect with the app.
2. Stop the app from Xcode (*Debug → Stop*), or end its process from the Mac. Do not swipe it
   away in the app switcher: iOS turns state restoration off for an app that the user closed.
3. Switch the lights on at the camper panel, or move out of range and back.
4. Check in the Console that iOS relaunches the app in the background and calls
   `willRestoreState`.
5. Open the app. Check that it shows the state of the restored link with its age, and that the
   controls stay disabled until a fresh read.

## Report

Record each check as "passed" or "failed" with the Console lines of a failure. A failed check is
a defect of the transport, not of the unit.
