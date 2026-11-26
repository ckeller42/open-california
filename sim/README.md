# coresim: a simulated M5Stack CoreS3

coresim runs ESP32-S3 firmware written for the M5Stack CoreS3 on a Linux host, against fakes of the board's
chips and modules. The firmware's own driver sources compile unchanged over the C shims in `shims/`:

- a FreeRTOS on a virtual clock (`sim_rtos.c`),
- UART, GPIO, the I2C master, the AW9523 `esp_io_expander` driver and the board-package calls of
  `espressif/m5stack_core_s3` 4.1.0 (`sim_hw.c`, `include/sim_idf.h`).

Each hardware action is one request line from the firmware to the Python fakes (`coresim/`). The fakes
answer in lockstep while the virtual clock stands still, so a run is deterministic, and hours of virtual
time take seconds.

coresim has no code of a particular firmware. A firmware supplies a small `main` for its sim build and a
subclass of `coresim.CoreS3` that says what it answers at boot and what a scheduled tick carries. The
first user is the open-california satellite (`firmware/host/sim/`, `tests/firmware/esp_sim.py`).

## Layout

| Path | What |
|---|---|
| `coresim/devices.py` | The device fakes: `Aw9523`, `Rak3172` |
| `coresim/board.py` | `CoreS3`: the wiring (internal I2C bus, the M-Bus module on UART1 with its GPIOs, the M-Bus 5 V) |
| `coresim/link.py` | `run()`: the lockstep link to a firmware binary |
| `shims/` | The C side: headers in place of the IDF/FreeRTOS/board-package ones, `sim_rtos.c`, `sim_hw.c` |
| `tests/` | The unit tests of the fakes, the replay of real captures, the virtual clock, the isolation guard |
| `tests/data/` | Real captures (redacted) |

## The link protocol

The firmware writes `HW <now_ms> <request>` and waits. The board answers with zero or more lines, then `END`.

- **Requests:** `BOOT`, `UART <line>`, `GPIO <pin> DIR in|out`, `GPIO <pin> LVL 0|1`, `PINS <tx> <rx>`,
  `I2C PROBE <addr>`, `I2C W <addr> <hex>`, `I2C R <addr> <written hex> <n>`, and the firmware's own
  requests.
- **Answers:** `ACK`, `NACK`, `DATA <hex>`, `EV <delay_ms> RX <text>` (UART bytes from the module after a
  delay), `EV <delay_ms> <event>` (the firmware's own events, e.g. `TICK`), `QUIT`.
- **Other firmware lines:** `T <ms>` means that the virtual clock moved on. Every other line is the
  firmware's log.

## Strict fakes

A fake fails the test loudly (`coresim.devices.Unmodelled`) when the firmware uses something that it does
not model: an AT command, a query or a register. It never answers with a made-up default. When new firmware
code trips this, extend the fake and give the evidence for the new behaviour in a comment.

## Data sources: verified and assumed

| Fake | Source | Status |
|---|---|---|
| RAK3172: answers and timing of `AT+DR=?` and `AT+SEND` (OK after 20 ms, `+EVT:TX_DONE` 8.2-8.9 s later at SF12) | The van satellite's console, `tests/data/rak3172-van-sf12-joined.log` (replayed by `tests/test_replay.py`) | verified |
| RAK3172: the other commands and settings (`AT`, `NWM`, `BAND`, `NJM`, `CFM`, `CLASS`, `ADR`, `DEVEUI`, `APPEUI`, `APPKEY`, `JOIN`) | RAKwireless RUI3 AT command manual | assumed (no capture yet) |
| RAK3172: join accept 5 s after `AT+JOIN` | LoRaWAN EU868 `JOIN_ACCEPT_DELAY1` | assumed |
| RAK3172: *latched silent*; RST and USB cuts do not help; the M-Bus 0/0 -> 1/1 step with TX and RST released does | Bench incident, 2026-11-22 | verified on one board |
| RAK3172: a power loss leaves it latched (`latch_on_dip`) | The van's 12 V dip, 2026-11-24 | observed once |
| RAK3172: refused send (`AT_BUSY_ERROR`) | The van's log behind the 120 s retry | verified |
| RAK3172: join accept never received | Six lost join accepts in a row, 2026-11-26 | observed; cause unknown |
| AW9523: register map (input 0x00/01, output 0x02/03, direction 0x04/05 with 0 = output, 0x11, 0x7F) | AW9523B datasheet; `espressif/esp_io_expander_aw9523` | datasheet |
| AW9523: power-on output registers 0x11/0x03 (both bus bits 0) | The owner's register probe of the bench CoreS3, 2026-11-22 | verified |
| AW9523: the state after a 0x7F soft reset (all outputs, all 0) | Assumption: the board package writes direction and output right after it | assumed |
| CoreS3: the M-Bus module has power while VBUS is there or both bus bits are 1 | M5Stack CoreS3 schematic (M5Unified's ext output) | assumed |

## Limits: what a green sim does not prove

- No analog behaviour: no brownout, no rail sag, no inrush, no slow 5 V ramp. A power loss is an instant
  on/off.
- No RF path: no airtime on air, no gateway, no lost downlink because of the radio environment.
- Module bugs that nobody has seen yet. The fakes model only what is in the table above.
- The shims re-implement the IDF UART driver, the GPIO calls, the AW9523 driver and the board-package
  error checks. A bug inside the real ones is not covered.
- The real I2C timing and the UART byte timing are not modelled. The UART is line-level.

A green sim run therefore never replaces a short test on a real board before a release reaches
esp-stable.

## Running

```sh
pip install -e sim
python -m pytest sim/tests -q
```

A firmware's sim build links `shims/sim_rtos.c` and `shims/sim_hw.c` with `-I sim/shims/include` and a
`main` that defines `sim_event()`. The satellite's sim build is `make -C firmware/host lora-sim`.
