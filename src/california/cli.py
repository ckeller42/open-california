from __future__ import annotations
import argparse, glob as _glob
from california.ingest import load_capture
from california.classify import classify_transport
from california.ingest import run_tshark
from california.diff import isolate
from california.protocol import load_map, save_map, merge_candidates, replay_safe
from california.codegen import generate

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="california")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify"); c.add_argument("capture")
    d = sub.add_parser("diff"); d.add_argument("glob"); d.add_argument("--out", default="protocol.yaml")
    k = sub.add_parser("check"); k.add_argument("glob")
    g = sub.add_parser("codegen")
    g.add_argument("--map", required=True); g.add_argument("--address", required=True)
    g.add_argument("--out", default="replay.py")
    return p

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "classify":
        print(classify_transport(run_tshark(args.capture)))
    elif args.cmd == "diff":
        caps = [load_capture(f) for f in sorted(_glob.glob(args.glob))]
        existing = load_map(args.out) if _glob.glob(args.out) else {"actions": {}}
        merged = merge_candidates(existing, isolate(caps))
        save_map(args.out, merged)
        print(f"wrote {args.out} ({len(merged['actions'])} actions)")
    elif args.cmd == "check":
        caps = [load_capture(f) for f in sorted(_glob.glob(args.glob))]
        by_action: dict[str, list] = {}
        for c in caps:
            by_action.setdefault(c.action, []).append(c)
        for action, reps in by_action.items():
            if len(reps) < 2:
                print(f"{action}: SKIP (need >=2 repeats)")
            else:
                print(f"{action}: {'PASS' if replay_safe(reps) else 'FAIL (nonce/crypto?)'}")
    elif args.cmd == "codegen":
        src = generate(load_map(args.map), args.address)
        with open(args.out, "w") as f:
            f.write(src)
        print(f"wrote {args.out}")
    return 0
