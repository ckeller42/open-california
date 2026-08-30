"""``likec4-view`` — embed a view from the built LikeC4 model (served at <site>/model/) as an iframe.

Usage (rst)::

    .. likec4-view:: seqArmedWrite
       :height: 420px

or (myst markdown)::

    ```{likec4-view} index
    ```

The model viewer is built with hash history, so ``model/#/view/<id>/`` is a stable deep link from
any page depth. No build-time export, no browser dependency — the iframe just points at the model
that ``docs/build_site.sh`` already merges into the Pages artifact.
"""
from docutils import nodes
from docutils.parsers.rst import Directive, directives


class LikeC4View(Directive):
    required_arguments = 1            # the view id, e.g. seqArmedWrite
    option_spec = {"height": directives.unchanged}

    def run(self):
        view = self.arguments[0]
        height = self.options.get("height", "460px")
        env = self.state.document.settings.env
        depth = env.docname.count("/")          # pages under subdirs need ../ per level
        src = "../" * depth + f"model/#/view/{view}/"
        html = (
            f'<iframe class="likec4-view" src="{src}" loading="lazy" '
            f'style="width:100%;height:{height};border:1px solid rgba(120,120,120,.3);'
            f'border-radius:8px;" title="LikeC4 view {view}"></iframe>'
        )
        return [nodes.raw("", html, format="html")]


def setup(app):
    app.add_directive("likec4-view", LikeC4View)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
