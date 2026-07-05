"""Self-contained tests for the protocol extractor's parsing (no decompile needed).

A synthetic control-model class exercises the object-keyed extraction: field
name (from the debug log), width/default (from `new sg.a`), and bit offset
(from `f()`), plus the merged-branch ambiguity handling.
"""
import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "extract_protocol", pathlib.Path(__file__).parent.parent / "tools" / "extract_protocol.py")
ep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ep)


# A minimal control model: 2 fields, one placed consistently, one placed
# differently across two f() branches (should come out MERGED_AMBIGUOUS).
CONTROL = '''
public final class a {
    public final sg.a f1;
    public final sg.a f2;
    public a(jn.b b, xm.a c) {
        this.d0 = "00001101-6C77-4B7D-BBF6-A5E587701F3D";
        this.f1 = new sg.a(3, 4);
        this.f2 = new sg.a(7, 6);
    }
    public void B() {
        Object obj = this.f1.f398b;
        Object obj2 = this.f2.f398b;
        StringBuilder sb = ou.a.k("--> Sending Data:  Power: ", obj, "  Level: ", obj2, "  ");
        xm.a.b(this.c0, sb.toString(), 6);
    }
    public final Boolean[] f() {
        switch (this.Z) {
            case 0:
                Boolean[] boolArr = new Boolean[48];
                Boolean[] boolArr2 = (Boolean[]) this.f1.f399c;
                boolArr[6] = boolArr2[0];
                boolArr[7] = boolArr2[1];
                Boolean[] boolArr3 = (Boolean[]) this.f2.f399c;
                boolArr[8] = boolArr3[0];
                boolArr[9] = boolArr3[1];
                boolArr[10] = boolArr3[2];
                boolArr[11] = boolArr3[3];
                return boolArr;
            default:
                Boolean[] boolArr4 = new Boolean[48];
                Boolean[] boolArr5 = (Boolean[]) this.f1.f399c;
                boolArr4[6] = boolArr5[0];
                boolArr4[7] = boolArr5[1];
                Boolean[] boolArr6 = (Boolean[]) this.f2.f399c;
                boolArr4[20] = boolArr6[0];
                boolArr4[21] = boolArr6[1];
                boolArr4[22] = boolArr6[2];
                boolArr4[23] = boolArr6[3];
                return boolArr4;
        }
    }
}
'''


def test_constructor_widths_and_defaults():
    ctors = ep.constructors(CONTROL)
    assert len(ctors) == 1
    decls = {o: (int(d), ep.TYPECODE_WIDTH[int(tc)]) for o, d, tc in ep.SGA_RE.findall(ctors[0])}
    assert decls == {"f1": (3, 2), "f2": (7, 4)}   # typecode 4->2 bits, 6->4 bits


def test_obj_names_from_log():
    blogs = ep.obj_names(CONTROL, ep.SENDING_RE)
    assert blogs == [{"f1": "Power", "f2": "Level"}]


def test_offsets_consistent_vs_merged_ambiguous():
    ps = ep.field_offsets(CONTROL)
    # f1 placed at 6,7 in BOTH branches -> deduped [6,7], a width-2 run -> offset 6
    assert ps["f1"] == [6, 7]
    # f2 placed at 8-11 (case0) AND 20-23 (default) -> non-contiguous union -> ambiguous
    assert ps["f2"] == [8, 9, 10, 11, 20, 21, 22, 23]


def test_offset_width_validation():
    ps = ep.field_offsets(CONTROL)
    # the caller's rule: contiguous run == width -> resolved, else None
    def resolve(obj, width):
        p = ps[obj]
        return p[0] if (len(p) == width and p[-1] - p[0] + 1 == width) else None
    assert resolve("f1", 2) == 6            # power resolves
    assert resolve("f2", 4) is None         # level ambiguous across branches
