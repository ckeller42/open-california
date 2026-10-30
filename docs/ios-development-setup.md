# iOS development setup

This page lists what a Mac needs to build, test and install the iPhone app ({doc}`ios-app`), and
how an iPhone is prepared for development builds.

## The Mac

- macOS with Xcode 27 or later, with Swift 6.4 and an iOS simulator runtime. Accept the license
  and install the first-launch components:

  ```sh
  sudo xcodebuild -license accept
  sudo xcodebuild -runFirstLaunch
  ```

- The command line tools come from that Xcode. `xcode-select -p` prints a path inside
  `Xcode.app`.
- MacPorts supplies the build tools. The project's Macs use MacPorts, not Homebrew:

  ```sh
  sudo port selfupdate
  sudo port install xcodegen xcbeautify swiftlint
  ```

  {term}`XcodeGen` generates the Xcode project, `xcbeautify` makes the `xcodebuild` output
  readable, and `swiftlint` checks the Swift style. An outdated ports tree offers an old
  `xcbeautify` that does not build with Swift 6.4; `port selfupdate` brings a current binary
  package. Without `xcbeautify`, use `xcodebuild -quiet`.
- The formatter is the toolchain's `swift format`. It needs no extra install.
- Python 3.11 or later with the repository's development dependencies (`tools/ci.sh dev`). The
  Swift tests run `python3 -m tools.gen_c_dict --check`, which checks that the generated copies
  of the C headers and of `semantics.js` are fresh.
- Docker builds the documentation and runs the C tests in CI's compiler (`tools/ci.sh linux`).
  App-only work does not need it.

## The repository

- The Swift package `CaliKit` is `Package.swift` in the repository root. It links the shared C
  code (`csrc/codec.c`, `csrc/ports.c`, `firmware/components/cali_core/control.c`) as it is.
  `swift test` runs its tests on the Mac, and `tools/ios_sanitize.sh` runs them again with the
  address and undefined-behaviour sanitizers.
- SwiftPM writes its products to a path that depends on the build system. Ask for it with
  `swift build --show-bin-path`; never write a `.build/…` path into a script.
- The Xcode project is generated from `ios/project.yml` and is not in git. Never edit a
  `project.pbxproj` by hand; change `project.yml` and generate again:

  ```sh
  xcodegen generate --spec ios/project.yml
  ```

- The app's tests run in the simulator. Pick an iPhone from
  `xcrun simctl list devices available`:

  ```sh
  xcodebuild test -project ios/Camper.xcodeproj -scheme Camper \
    -destination 'platform=iOS Simulator,name=iPhone 17' | xcbeautify
  ```

  The UI tests start the app with the launch argument `-UITestFixture <name>`. The app then uses
  a fake camper unit, never real Bluetooth. The simulator has no Bluetooth: the Bluetooth tests
  run on a real iPhone, against the fake unit peripheral or the van
  ({doc}`ios-app-radio-checklist`).
- Signing values are personal and stay out of git. Copy `ios/Config/Local.xcconfig.example` to
  `ios/Config/Local.xcconfig` and set `DEVELOPMENT_TEAM` (your team ID) and `BUNDLE_ID_PREFIX`
  (a prefix of your own). A simulator build needs neither. Never commit certificates,
  provisioning profiles, `xcuserdata` or `DerivedData`.

## The Apple account

A free Apple ID is enough for development: Xcode makes it a {term}`Personal Team`.

1. Open Xcode → Settings → Accounts and add the Apple ID.
2. Build for the iPhone once. Automatic signing creates the "Apple Development" certificate and a
   team provisioning profile.

A Personal Team has limits: an installed app runs for 7 days and must then be installed again, and
there are no App Groups (widgets), no Hotspot Configuration and no TestFlight. Siri and Shortcuts
(App Intents) and the `bluetooth-central` background mode work.

The first build for a new iPhone must name that iPhone as the destination, so that Xcode registers
it in the team. A generic iOS destination fails with "Your team has no devices":

```sh
xcodebuild build -project ios/Camper.xcodeproj -scheme Camper \
  -destination 'platform=iOS,id=<UDID>' \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration
```

If Xcode reports "The login details for account … were rejected", sign in again in Settings →
Accounts.

## The iPhone

1. Connect the iPhone to the Mac with a cable and trust the Mac.
2. Turn on Settings → Privacy & Security → {term}`Developer Mode`, and let the iPhone restart.
3. Turn on Settings → Developer → Enable UI Automation, for UI tests on the iPhone.
4. After the first install, trust the developer profile in Settings → General → VPN & Device
   Management.

Keep the iPhone unlocked during an install, or the install stops with "The device was still
locked".

## Building on a Mac next to the iPhone

Xcode finds iPhones with Bonjour, which Tailscale does not carry. Build and install on a Mac that
is next to the iPhone (cable or the same WiFi).

Code signing needs the user's login keychain. Over a plain SSH session, `codesign` fails with
`errSecInternalComponent`, because that session cannot unlock the keychain. Run the build in the
logged-in desktop session of that Mac instead:

1. Write the build commands into a `.command` script that writes a log file.
2. Start the script with `ssh <mac> 'open -a Terminal <script>'`.
3. Read the log file.

To stop `codesign` from asking for keychain access, run this once in a terminal on that Mac. It
reads the login password in a hidden dialog, so the password never reaches the chat, a file or
the shell history:

```sh
pw=$(osascript -e 'text returned of (display dialog "macOS login password:" default answer "" with hidden answer)') && security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$pw" ~/Library/Keychains/login.keychain-db >/dev/null; unset pw
```

List the connected iPhones and install a built app:

```sh
xcrun devicectl list devices
xcrun devicectl device install app --device <CoreDevice id> <path>.app
```
