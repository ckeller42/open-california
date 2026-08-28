# UI design rationale — why the interface looks the way it does

`calictl`'s control interface (`ui/prototype.html`, generated from `ui/screens/*.yaml`)
is an **independent, hardware-function-driven** interface. Its information architecture is
dictated by the camper's physical configuration and BLE control surface — not copied from the
vendor app's expressive design. This document records, per screen, *why* each screen and
widget exists (the function it serves), and traces it back to the reverse-engineering evidence.

It serves two purposes at once:

1. **Design rationale** — every screen maps to an installed subsystem, and every widget maps to
   a protocol field or a hardware/firmware-imposed interaction. The structure is functional: given
   the same hardware, a limited set of sensible interfaces exists (a compressor fridge needs an
   on/off + a level; a pop-top roof needs open/stop/close; tanks need fresh + waste readouts).
2. **RE traceability** — the "Trace" column cites the decompiled symbol (`mapping.enigma` name →
   obfuscated `file:line`) and the `protocol/dictionary.yaml` field each control derives from. This
   traceability is kept here (and in `ui/screens/*.yaml`), as RE evidence — **by citation, never by
   reproducing vendor code or artwork**.

## Independent vs. hardware-driven — the dividing line

| Independently designed (ours) | Hardware/firmware-driven (functional) |
|---|---|
| appearance, colour system, typography | which screens exist (one per installed subsystem) |
| all wording / labels (neutral placeholders) | which screens are hidden (per-function `Installed` bit) |
| icons (vendor drawables are **not** committed; neutral placeholders) | control *type* per field (toggle for a bit, slider for a 0–N level) |
| layout / composition / navigation shell | value ranges + units (from the protocol field width/scale) |
| — | safety confirmations imposed by the firmware (roof, heater) |

The vendor's trademarks appear only nominatively, to identify the vehicle the interface controls
(see `DISCLAIMER.md`). No vendor icon, string, or layout is reproduced.

## Per-screen map (design + traceability)

Char bases per subsystem are in `docs/sphinx/hardware.rst`; field bit-layout is
`protocol/dictionary.yaml`; per-feature RE notes are `docs/business-logic/*.md`.

| Screen | Subsystem / `Installed` source | Core widgets → why (functional) | Trace (protocol field · decompile/enigma) |
|---|---|---|---|
| Cooler | `cooler` char `1100`/`1102`, `Installed` bit | on/off toggle (compressor is on/off); level slider 1–5 (setpoint is a small enum); quiet-mode toggle + schedule; cooling-timer start + Arm/Cancel | `State`/`Level`/`Mode`/`TimerHourSet`·`TimerMinSet`/`NightTimerHourOn`·`Off` · CoolerViewModel `vf/c.e()`/`g()` |
| Camping mode | `campingmode` char `1200`/`1202` (always shown — `Installed` decoded but not wired, mirrors the app) | master toggle; interior/outside light toggles; USB toggle (each an on/off load) | `State`/`InteriorLight`/`OutsideLight`/`UsbCharger` · `tf/a.java` (`b4()` StateFlow hardcodes visibility TRUE) |
| Lighting | `lighting` char `1500`/`1502`, `Installed` bit | per-zone brightness sliders (each zone is a 0–10 enum); profile selection | `BrightnessLOne…LOneSix`/`ProfileNumber`/`LightValue` · `eg/a.java`, brightness enum `dg/i.java` |
| Water | `water` char `1300`/`1302`, `Installed` bit | separate fresh + waste readouts (two independent tank sensors) — read-only | `Level`/`Volume` (fresh + waste) · water decode notes, `docs/business-logic/value-freshness.md` |
| Energy | `energy` char `1600`/`1602`, `Installed` bit | battery/DC-DC/shore/solar readouts; charge-mode select (firmware exposes a mode enum) | `EnergyModeSet` + battery/current fields · `xf/d.d4()`, `pg/a.java` |
| Air heater | `airheater` char `1700`/`1702`, `Installed` bit | power toggle; heating-level slider; timer/runtime (firmware exposes level + timer) | `NormalOperation`/`HeatingLevel`/timer fields · `rf/b.java` |
| Roof (pop-top) | `roof` char `1400`/`1402`, live `Installed=1` | hold-to-move open / stop / close (firmware is press-and-hold with a dead-man safety counter) + **safety confirmation** | direction bytes open `0x01`/stop `0x00`/close `0x04`, SafetyCounter · `ig/c.java` `n2()`, `w8/a`+`b1/d` |

**Not-installed subsystems** (stairs, roof-A/C, satellite, living-room heater on the reference van)
have `Installed=0` and are hidden — their screens exist as specs only, never rendered.

## Out of scope (deliberately not modeled)

App-shell / account / onboarding / analytics / discover / travel screens from the vendor app are
**not** part of this interface — they are not required to monitor or control the camper hardware.
They are inventoried in `ui/README.md` for RE completeness only, never built as a screen.
