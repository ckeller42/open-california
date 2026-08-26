"""Generate docs/protocol/vwcamper.lua from protocol/dictionary.yaml (protocol doc view #3).

Read-only projection: never hand-edit the generated file, regenerate it instead —
`python3 -m tools.gen_wireshark_dissector --out docs/protocol/vwcamper.lua`. See
docs/protocol/README.md. tests/test_protocol_views.py asserts the committed file matches
a fresh regen (fails CI on drift).

Emits a best-effort Wireshark Lua dissector: one ``Proto`` for the vendor protocol, with a
``ProtoField`` per positionally-resolved state field, bit-masked so a captured BTATT value
on a characteristic self-annotates. It is NOT runtime-tested against a live Wireshark
capture (no Wireshark in this repo's toolchain) — treat it as a well-typed starting point.

Bit convention (verified against calictl/protocol.py, `to_bits`/`get_field`, and reused
from tools/gen_frame_layouts.py): the codec is MSB-first — bit index 0 of a frame is bit 7
(the MSB) of byte 0 (``bits[i] = data[i // 8] >> (7 - i % 8) & 1``). A field at bit-offset
``o`` / width ``w`` therefore starts in byte ``o // 8``, at bit position ``7 - (o % 8)``
(counting 7=MSB..0=LSB within that byte) and descends for ``w`` bits. Every field in
dictionary.yaml today is either fully contained in one byte (w in {1,2,4,8} with
``o % 8 + w <= 8``) or exactly byte-aligned across whole bytes (``o % 8 == 0``, w in
{16,32}) — this generator masks the former within a single byte and spans the latter
across whole bytes; a field that doesn't fit either shape is skipped with a comment
(defensive; does not occur in the current dictionary).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tools._view_common import char_uuid, is_placed, load_functions

ROOT = Path(__file__).resolve().parent.parent
DICT_FILE = ROOT / "protocol" / "dictionary.yaml"
OUT_FILE = ROOT / "docs" / "protocol" / "vwcamper.lua"




def _state_char_short(services):
    """Mirrors calictl.protocol.load's state-char pick: prefer the short ending "02"
    (the state characteristic), else fall back to the 2nd listed short, else None."""
    if not services:
        return None
    shorts = [str(s).lower() for s in services]
    return next((s for s in shorts if s.endswith("02")), shorts[1] if len(shorts) > 1 else None)




def _layout(field):
    """Return (byte_offset, byte_length, mask, lua_ftype) for a placed field, or None if
    its offset/width doesn't fit a single-byte or whole-byte-aligned shape (see module
    docstring) — such a field is skipped with a comment rather than mis-masked."""
    offset, width = field["offset"], field["width"]
    # `signed: true` (structured key in dictionary.yaml, issue #108) -> ProtoField.intN so
    # e.g. the vehicle leveling angles and battery currents display as negative values.
    # Only whole-byte shapes carry it (all signed fields are 8/16-bit aligned); a signed
    # sub-byte field would fall back to uintN + the mask (none exist today).
    signed = bool(field.get("signed"))
    byte_off, bit_in_byte = offset // 8, offset % 8
    if width in (1, 2, 4, 8) and bit_in_byte + width <= 8:
        high_bit = 7 - bit_in_byte
        low_bit = high_bit - width + 1
        mask = ((1 << width) - 1) << low_bit
        ftype = "bool" if width == 1 else ("int8" if signed and width == 8 else "uint8")
        return byte_off, 1, mask, ftype
    if width in (16, 32) and bit_in_byte == 0:
        ftype = ("int16" if signed else "uint16") if width == 16 else ("int32" if signed else "uint32")
        return byte_off, width // 8, (1 << width) - 1, ftype
    return None


def _mask_literal(mask, byte_len) -> str:
    return "0x%0*x" % (byte_len * 2, mask)


def _lua_ident(*parts) -> str:
    return "_".join(parts)


def build(dict_path=None) -> str:
    """Render the Lua dissector source from dictionary.yaml. Returns the file text."""

    funcs = load_functions(dict_path)

    field_decls: list[str] = []
    char_entries: list[tuple[str, str, list[tuple[str, int, int]]]] = []  # (uuid, name, [(ident, byte, len)])
    ambiguous_notes: list[str] = []

    for name in sorted(funcs):
        spec = funcs[name]
        if not isinstance(spec, dict):
            continue
        state_fields = spec.get("state_fields") or []
        short = _state_char_short(spec.get("state_services"))
        uuid = char_uuid(short) if short else None

        field_decls.append("-- %s%s" % (name, " (state char %s)" % uuid if uuid else " (no resolvable state char)"))
        entries: list[tuple[str, int, int]] = []
        skipped = []
        for f in state_fields:
            fname = f.get("name", "—")
            if not is_placed(f):
                skipped.append(fname)
                continue
            layout = _layout(f)
            if layout is None:
                skipped.append(fname)
                continue
            byte_off, byte_len, mask, ftype = layout
            ident = _lua_ident("f", name, fname)
            abbr = "vwcamper.%s.%s" % (name, fname)
            mask_lit = _mask_literal(mask, byte_len)
            if ftype == "bool":
                field_decls.append(
                    'local %s = ProtoField.bool("%s", "%s", 8, nil, %s)' % (ident, abbr, fname, mask_lit))
            else:
                field_decls.append(
                    'local %s = ProtoField.%s("%s", "%s", base.DEC, nil, %s)'
                    % (ident, ftype, abbr, fname, mask_lit))
            entries.append((ident, byte_off, byte_len))
        if skipped:
            note = "-- %s: ambiguous offset (not registered as ProtoFields): %s" % (name, ", ".join(skipped))
            field_decls.append(note)
            ambiguous_notes.append(note)
        field_decls.append("")
        if uuid and entries:
            char_entries.append((uuid, name, entries))

    lines = [
        "-- GENERATED from protocol/dictionary.yaml — do not hand-edit; regenerate with",
        "-- `python3 -m tools.gen_wireshark_dissector`. See docs/protocol/README.md.",
        "--",
        "-- Best-effort Wireshark Lua dissector for the VW California T7 camper unit's vendor",
        "-- BLE GATT protocol (see CLAUDE.md / docs/business-logic/). NOT runtime-tested",
        "-- against a live Wireshark capture — a captured BTATT value read/notified on a",
        "-- matching characteristic UUID should self-annotate per field, but verify before",
        "-- relying on it.",
        "--",
        "-- Bit convention (matches calictl/protocol.py to_bits/get_field): MSB-first. A field",
        "-- at bit-offset `o`, width `w` starts in byte `o // 8`, at bit `7 - (o %% 8)` (7=MSB",
        "-- .. 0=LSB) within that byte, descending for `w` bits. Fields with an unresolved",
        '-- (`MERGED_AMBIGUOUS`) offset can\'t be masked positionally and are skipped, listed',
        "-- in a comment per function instead.",
        "",
        'local vwcamper_proto = Proto("vwcamper", "VW California Camper Unit (vendor BLE)")',
        "",
        "-- ProtoFields, grouped per function -----------------------------------------------",
        "",
    ]
    lines.extend(field_decls)

    all_field_idents = [ident for _, _, entries in char_entries for ident, _, _ in entries]
    lines.append("vwcamper_proto.fields = {")
    for ident in all_field_idents:
        lines.append("    %s," % ident)
    lines.append("}")
    lines.append("")

    lines.append("-- characteristic UUID -> {name, fields} used by the dissector below -------------")
    lines.append("local char_table = {")
    for uuid, name, entries in char_entries:
        lines.append('    ["%s"] = {' % uuid)
        lines.append('        name = "%s",' % name)
        lines.append("        fields = {")
        for ident, byte_off, byte_len in entries:
            lines.append("            { field = %s, byte = %d, len = %d }," % (ident, byte_off, byte_len))
        lines.append("        },")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    lines.extend([
        "-- Dissection: best-effort. There's no single documented Wireshark dissector table",
        "-- keyed on a BTATT 128-bit characteristic UUID across all Wireshark versions, so we",
        "-- try the most likely one and fall back to no-op registration (pcall-guarded).",
        "local function char_uuid_from_pinfo()",
        '    local ok, f = pcall(Field.new, "bluetooth.uuid128")',
        "    if not ok or not f then",
        "        return nil",
        "    end",
        "    local finfo = f()",
        "    if not finfo then",
        "        return nil",
        "    end",
        "    return tostring(finfo.value):lower()",
        "end",
        "",
        "function vwcamper_proto.dissector(buffer, pinfo, tree)",
        "    local uuid = char_uuid_from_pinfo()",
        "    local spec = uuid and char_table[uuid]",
        "    if not spec then",
        "        return",
        "    end",
        '    pinfo.cols.protocol = "VWCAMPER"',
        '    local subtree = tree:add(vwcamper_proto, buffer(), "VW Camper " .. spec.name .. " state")',
        "    for _, entry in ipairs(spec.fields) do",
        "        if buffer:len() >= entry.byte + entry.len then",
        "            subtree:add(entry.field, buffer(entry.byte, entry.len))",
        "        end",
        "    end",
        "end",
        "",
        "-- Best-effort registration against a UUID128 dissector table, if one exists in this",
        "-- Wireshark build; harmless no-op otherwise.",
        'local uuid_table = DissectorTable.get("bluetooth.uuid128") or DissectorTable.get("btatt.uuid128")',
        "if uuid_table then",
        "    for uuid, _ in pairs(char_table) do",
        "        pcall(function() uuid_table:add(uuid, vwcamper_proto) end)",
        "    end",
        "end",
    ])
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="generate the Wireshark Lua dissector")
    ap.add_argument("--out", default=str(OUT_FILE))
    ap.add_argument("--dict", default=str(DICT_FILE), help="path to dictionary.yaml")
    args = ap.parse_args(argv)
    text = build(args.dict)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
