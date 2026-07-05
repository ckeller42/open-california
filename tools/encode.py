#!/usr/bin/env python3
"""Clean-room reimplementation of the VW California Camper Unit BLE command
encoder, derived by reverse-engineering the official app's algorithm.

The unit's control characteristics take a bit-packed frame. Each field is
encoded low-bits-first-then-MSB-order and packed MSB-first into bytes. See
docs/protocol.md for the derivation and the per-service field maps.

This module is our own code; no third-party/app source is included here.
"""
from __future__ import annotations


def bits_msb_first(value: int, n: int) -> list[int]:
    """A field value -> its low `n` bits, most-significant-first.

    Mirrors the app's `u8.c(v, n)`: take the low n bits of v (LSB-first),
    then reverse. Verified against the decoder's LSB-first `sum(bit_i * 2**i)`.
    """
    lsb = [(value >> k) & 1 for k in range(32)]
    return lsb[:n][::-1]


def pack_msb(frame_bits: list[int]) -> bytes:
    """Bit array -> bytes, MSB-first (bit 0 -> byte0 0x80). Mirrors `u8.e`."""
    out = bytearray((len(frame_bits) + 7) // 8)
    for i, b in enumerate(frame_bits):
        if b:
            out[i // 8] |= 1 << (7 - (i % 8))
    return bytes(out)


def fridge_control(power: int, *, f0=3, g0=3, h0=3, j0=7, i0=7,
                   n0=31, k0=30, l0=62, m0=31) -> bytes:
    """Build the 6-byte fridge (service 0x1100) control frame.

    Field placement + defaults from the app's *fridge* control model (the 1101
    constructor). NOTE: the heater (1701) model shares this class with DIFFERENT
    defaults — do not mix them up (an earlier version did, producing the wrong
    frame `3d7b007f1f3f`; the fridge frame is `fd770f1e3e1f`).

    STRUCTURAL EXAMPLE ONLY — NOT a working command. Writing this default-filled
    frame was tested live and did NOT toggle power: the defaults form a garbage
    command (Level=7 is out of the 1-5 range; TimerStart/Cancel/NightTimerSet=3 =
    conflicting timer actions). A real power toggle sets `power`, sets the timer
    ACTION fields to 0, and carries the current Level/Mode (read from State 1102),
    not defaults. See docs/protocol.md and protocol/dictionary.yaml.

    `power` is 0=off / 1=on.
    """
    a = [0] * 48

    def put(field_bits: list[int], positions: list[int]) -> None:
        for bit, pos in zip(field_bits, positions):
            a[pos] = bit

    put(bits_msb_first(h0, 2), [0, 1])
    put(bits_msb_first(g0, 2), [2, 3])
    put(bits_msb_first(f0, 2), [4, 5])
    put(bits_msb_first(power, 2), [6, 7])           # POWER
    put(bits_msb_first(j0, 4), [8, 9, 10, 11])
    put(bits_msb_first(i0, 4), [12, 13, 14, 15])
    # bits 16-19 unused
    put(bits_msb_first(n0, 4), [20, 21, 22, 23])
    put(bits_msb_first(k0, 8), list(range(24, 32)))
    put(bits_msb_first(l0, 8), list(range(32, 40)))
    put(bits_msb_first(m0, 8), list(range(40, 48)))
    return pack_msb(a)


if __name__ == "__main__":
    # bit-math sanity (matches the app's u8.c/u8.e derived semantics)
    assert bits_msb_first(11, 4) == [1, 0, 1, 1]      # low 4 bits of 11, MSB-first
    print("fridge ON  (UNVERIFIED):", fridge_control(1).hex())   # fd770f1e3e1f
    print("fridge OFF (UNVERIFIED):", fridge_control(0).hex())   # fc770f1e3e1f
