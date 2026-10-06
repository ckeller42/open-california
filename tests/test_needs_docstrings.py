"""Guard: the sphinx-needs objects authored in docstrings are well-formed and actually render.

``sphinx -W`` only sees needs in objects that ``docs/api.rst`` autodocs; a ``.. test::`` in a
module nobody autodocs (or whose options cleandoc pushed out of the directive) silently drops out
of the trace. This walks every docstring under calictl/, tools/, tests/ with ``ast`` (no imports)
and checks, after ``inspect.cleandoc`` (what autodoc's ``prepare_docstring`` does):

- each ``.. req::``/``.. test::`` has an indented ``:id:`` option with the R_/T_ prefix,
- ids are unique, every ``:links:`` target is an existing R_ id,
- the docstring's owner is rendered by an ``auto*::`` directive in docs/api.rst.
"""

import ast
import inspect
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIRECTIVE = re.compile(r"^(\s*)\.\. (req|test)::")
OPTION = re.compile(r"^\s*:(\w+):\s*(.*)$")
PREFIX = {"req": "R_", "test": "T_"}


def _docstrings(tree):
    """Yield ``(qualname, docstring)`` for the module (qualname ``""``) and every def/class."""

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = prefix + child.name
                doc = ast.get_docstring(child, clean=False)
                if doc:
                    yield qual, doc
                yield from walk(child, qual + ".")

    doc = ast.get_docstring(tree, clean=False)
    if doc:
        yield "", doc
    yield from walk(tree, "")


def _needs(doc):
    """Parse the needs in one docstring -> list of (kind, options-dict)."""
    lines = inspect.cleandoc(doc).splitlines()
    out = []
    for i, line in enumerate(lines):
        m = DIRECTIVE.match(line)
        if not m:
            continue
        indent, opts = len(m.group(1)), {}
        for nxt in lines[i + 1 :]:
            if not nxt.strip() or len(nxt) - len(nxt.lstrip()) <= indent:
                break  # options must be indented under the directive, before the first blank line
            om = OPTION.match(nxt)
            if om:
                opts[om.group(1)] = om.group(2).strip()
        out.append((m.group(2), opts))
    return out


def _collect():
    needs = []  # (where, module, qualname, kind, opts)
    for top in ("calictl", "tools", "tests"):
        for path in sorted((ROOT / top).rglob("*.py")):
            rel = path.relative_to(ROOT)
            module = ".".join(rel.with_suffix("").parts)
            for qual, doc in _docstrings(ast.parse(path.read_text(encoding="utf-8"))):
                for kind, opts in _needs(doc):
                    needs.append(("%s:%s" % (rel, qual or "<module>"), module, qual, kind, opts))
    return needs


def _api_directives():
    """docs/api.rst -> list of (directive, target, options-dict)."""
    out = []
    for line in (ROOT / "docs" / "api.rst").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\.\. auto(\w+):: (\S+)", line)
        if m:
            out.append((m.group(1), m.group(2), {}))
        elif out and (om := OPTION.match(line)) and line.startswith(" "):
            out[-1][2][om.group(1)] = om.group(2).strip()
    return out


def _rendered(module, qual, api):
    """Is the docstring at ``module``/``qual`` emitted by some autodoc directive in api.rst?"""
    target = module + ("." + qual if qual else "")
    public = not any(p.startswith("_") for p in qual.split(".") if p)
    for kind, tgt, opts in api:
        if tgt == target:
            return True
        if kind == "module" and tgt == module and (not qual or ("no-members" not in opts and public)):
            return True
        if kind == "class" and qual and target.startswith(tgt + ".") and "members" in opts:
            listed = [m.strip() for m in opts["members"].split(",") if m.strip()]
            if (not listed and public) or qual.split(".")[-1] in listed:
                return True
    return False


NEEDS = _collect()


def test_needs_exist():
    assert len(NEEDS) > 50, "parser found suspiciously few needs: %d" % len(NEEDS)


def test_every_need_has_a_prefixed_id():
    bad = [
        "%s: .. %s:: has %s"
        % (
            w,
            k,
            "no :id: (options not indented under the directive?)"
            if "id" not in o
            else "id %r without the %s prefix" % (o["id"], PREFIX[k]),
        )
        for w, _m, _q, k, o in NEEDS
        if not o.get("id", "").startswith(PREFIX[k])
    ]
    assert not bad, "malformed sphinx-needs in docstrings:\n" + "\n".join(bad)


def test_need_ids_are_unique():
    seen, dupes = {}, []
    for w, _m, _q, _k, o in NEEDS:
        if "id" in o:
            if o["id"] in seen:
                dupes.append("%s: %s (also %s)" % (w, o["id"], seen[o["id"]]))
            seen[o["id"]] = w
    assert not dupes, "duplicate need ids:\n" + "\n".join(dupes)


def _doc_reqs():
    """R_ ids authored directly in the rendered docs pages (firmware.md, cross-language-codec.rst)."""
    ids = set()
    for p in (ROOT / "docs").rglob("*"):
        if p.suffix in (".rst", ".md") and not {"_build", "business-logic", "superpowers"} & set(p.parts):
            ids |= set(re.findall(r"^\s*:id:\s*(R_\w+)", p.read_text(encoding="utf-8"), re.M))
    return ids


def test_links_resolve_to_requirements():
    reqs = {o["id"] for _w, _m, _q, k, o in NEEDS if k == "req" and "id" in o} | _doc_reqs()
    bad = [
        "%s: %s links to unknown %s" % (w, o.get("id"), t)
        for w, _m, _q, _k, o in NEEDS
        for t in re.split(r"[,\s]+", o.get("links", ""))
        if t and t not in reqs
    ]
    assert not bad, "dangling :links: targets:\n" + "\n".join(bad)


def test_every_need_is_autodocd_in_api_rst():
    api = _api_directives()
    bad = sorted({"%s (%s)" % (w, o.get("id")) for w, m, q, _k, o in NEEDS if not _rendered(m, q, api)})
    assert not bad, (
        "needs whose docstring docs/api.rst never renders (add an auto*:: directive, else the "
        "need silently drops out of the sphinx-needs trace):\n" + "\n".join(bad)
    )
