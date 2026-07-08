# Disclaimer & Trademark Notice

**open-california** is an independent reverse-engineering project whose purpose is
**interoperability**: enabling the vehicle owner's **own** VW California camper control unit
to be read and controlled over Bluetooth LE from **Linux and other open platforms**. The
vendor app (`de.volkswagen.CaliforniaOnTour`) is **iOS/Android-only** (its Terms of Use §3.1
require iOS 12.1 / Android 8.0), so no first-party client exists for Linux; this project is an
independently-created program that interoperates with the unit's BLE protocol to fill that gap.

- **Not affiliated** with, endorsed, sponsored by, or connected to Volkswagen AG,
  Volkswagen Commercial Vehicles, or any of their subsidiaries or partners.
- **"Volkswagen", "VW", the VW logo, "California", and "CaliforniaOnTour"** are trademarks
  of Volkswagen AG. They are used here **only nominatively** — to identify the product this
  project interoperates with — not to imply any association or endorsement.
- **No VW intellectual property is redistributed.** This repository contains **no** VW
  application binary (APK), decompiled source, firmware, owner's manuals, or extracted
  artwork/icons. Such material is used locally for analysis only and is git-ignored;
  documentation cites it by reference (file/line, URLs), never by reproduction.
- The protocol facts documented here (BLE UUIDs, bit layouts, enums, observed behavior) are
  **independent observations** produced by black-box and static reverse engineering for
  interoperability. Facts and interfaces are not themselves copyrightable.

## Interoperability & the vendor Terms of Use

The app's Terms of Use (§4.2) restrict decompiling *"unless expressly permitted by mandatory
law."* In the EU, the **Software Directive 2009/24/EC, Art. 6** grants a **mandatory,
non-waivable right to decompile a program for the purpose of interoperability** with an
independently-created program — a right a contract term cannot override, and to which the ToU
itself defers. This project relies on that basis: the reverse engineering is done **only** to
achieve interoperability of the owner's own vehicle with Linux/open platforms.

The Art. 6 exception is conditional; this project is designed to stay within it:

- **Necessary & not otherwise available** — VW publishes no Linux client and no protocol spec.
- **Limited to what interoperability requires** — BLE protocol facts (UUIDs, bit layouts,
  enums, observed behavior) only.
- **Not a competing product** — a personal interop tool, not a reimplementation of the app.
- **No VW IP redistributed** — no APK, decompiled source, firmware, manuals, or artwork are
  included (they are analysed locally and cited by reference only).
- **Non-commercial** — consistent with ToU §4.1 (non-exclusive, non-commercial personal use).

This is background, **not legal advice**. RE / anti-circumvention / EULA law varies by
jurisdiction; if you publish or distribute this work, have a qualified lawyer confirm the Art. 6
conditions are met for your situation.

## Your responsibility

Reverse engineering, interoperability, and anti-circumvention law vary by jurisdiction, as do
the terms of the VW app's EULA and your vehicle's warranty. **You are responsible for your own
legal compliance.** Interoperating with, and especially *actuating*, a vehicle you do not own
may be unlawful and unsafe. Use only on your own vehicle, at your own risk.

## No warranty

This software is provided "as is", without warranty of any kind. Sending control writes to a
vehicle can have real-world, safety-relevant effects (heaters, roof, electrical loads). The
authors accept no liability for any damage, injury, or loss.

*This notice is not legal advice. If you intend to publish or distribute this work, consult a
qualified lawyer regarding the copyright, trademark, EULA, and anti-circumvention considerations.*
