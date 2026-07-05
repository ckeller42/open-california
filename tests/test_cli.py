def test_package_imports():
    import california
    assert california.__version__ == "0.1.0"


from california.cli import build_parser


def test_parser_has_subcommands():
    p = build_parser()
    args = p.parse_args(["codegen", "--map", "m.yaml", "--address", "AA:BB", "--out", "r.py"])
    assert args.cmd == "codegen"
    assert args.address == "AA:BB"
