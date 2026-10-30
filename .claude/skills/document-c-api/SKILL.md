---
name: document-c-api
description: Use when adding or changing a public C function, type, macro or header in firmware/components/cali_core/include — the Doxygen comment, a R_FW_* requirement held in the header, the docs/firmware-api.rst page, and the strict docs gate.
---

# Document the firmware C API

Public headers in `firmware/components/cali_core/include/` are rendered by Doxygen + Breathe on
`docs/firmware-api.rst`. `firmware/Doxyfile` is **strict** (`WARN_AS_ERROR`, `INPUT` = the whole
include dir): one undocumented public declaration fails `tools/ci.sh docs` and CI's `docs` job.

## Comment template (Javadoc, in the header only)

```c
/**
 * @brief One line: what it does.
 * @param buf  What it holds (same name as in the declaration).
 * @param len  Capacity of buf in bytes.
 * @return What 0, negative or a count means. Omit for void.
 */
int cali_thing_fmt(char *buf, size_t len);
```

Every macro, enum, typedef, struct member and extern needs a comment too. Never document the same
function in the `.h` and the `.c`. Do not mass-document `.c` internals.

## A requirement held in the header

Put it in the header's doc comment. Keep `leading-asterisk`: plain `embed:rst` loses `:id:`.

```c
 * @verbatim embed:rst:leading-asterisk
 * .. req:: Short title
 *    :id: R_FW_THING
 *    :status: implemented
 *
 *    The firmware shall ...
 * @endverbatim
```

Do not also keep it in `docs/firmware.md` (move it; leave a pointer under "Traceability"). The
verifying test is a `.. test::` with `:links: R_FW_THING` in a Python test docstring.
`tests/test_needs_docstrings.py` reads `:id: R_*` from the headers.

## Where it renders

`docs/firmware-api.rst`. A **new header** needs a `.. doxygenfile:: cali_thing.h` line in the
right section; a changed header needs nothing.

## Gates (run all three)

```sh
tools/ci.sh docs     # Docker: doxygen + sphinx -W; must end `site -> /tmp/site`, no warnings
tools/ci.sh linux    # CI's gcc -Werror compiles the headers (catches -Wcomment)
tools/ci.sh needs-trace   # every need defined and autodoc'd
```

Trace check: `sphinx -b needs`, then `links_back` of `R_FW_THING` in `needs.json` must list the
test (command in `docs/building-the-docs.md`).

## Warnings seen and fixes

| Symptom | Fix |
|---|---|
| Breathe: `Invalid C++ declaration: Expected identifier in nested name` | anonymous `enum {…}`: give it a tag, `enum cali_x_e {…}` |
| `Need could not be created: No ID defined` | `embed:rst` → `embed:rst:leading-asterisk` |
| gcc `-Wcomment` error | no `/*` or `*/` inside a comment (`/api/*` → "the /api/ routes") |
| `Unsupported xml/html tag <word>` | Doxygen reads `<word>` as HTML: write `MIN` or a code span |
| `X is not documented` for a macro | macros in an `#else` branch need their own comment |
| duplicate / conflicting doc for a function | remove the `.c` comment, keep the header |
| `parameters of member … not documented`, `argument … not found` | one `@param` per argument, exact names |
| `Problems running dot` | keep `HAVE_DOT = NO` in the Doxyfile |

Deeper reference: `docs/building-the-docs.md`.
