# Web UI icons

The dashboard tiles in `app.js` (`ICONS`) use eight icons from
[Lucide](https://lucide.dev) **0.460.0** (`lucide-static@0.460.0`), copied as SVG path data:
`snowflake`, `tent`, `lightbulb`, `heater`, `droplet`, `battery-charging` (as `battery`),
`caravan` and `car`. Straight `<line>` elements are written as the same `<path>`; the shapes are
unchanged. Add a new icon from the same set and version, so that the tiles keep one style.

The icons are decorative (`aria-hidden="true"`): every tile carries its text label.

## Licence

```text
ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```
