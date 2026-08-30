open-california — documentation
===============================

.. danger::

   **Use at your own risk — no warranty, no liability.** This software sends control
   writes to a real vehicle (heaters, roof, electrical loads) and can damage your vehicle,
   void your warranty, or create unsafe conditions. It is provided "AS IS" with **NO
   WARRANTY and NO LIABILITY** — if it breaks your car, that is your responsibility. Use it
   only on a vehicle you own. Independent reverse-engineering project, **not affiliated with
   Volkswagen**; no VW intellectual property is distributed. MIT-licensed; see ``LICENSE``
   and ``DISCLAIMER.md``.

Reverse-engineered control + monitoring for the VW California T7 camper unit over BLE.
Start with :doc:`architecture` for the five-minute map; the requirement traceability at the
bottom of this page is generated from ``sphinx-needs`` objects authored **inside code
docstrings** (``.. req::``) and traced to the tests that verify them (``.. test:: … :links:``),
so a failing or missing link surfaces at doc-build time next to the code.

.. toctree::
   :maxdepth: 1
   :caption: Getting started

   architecture
   raspberry-pi-setup

.. toctree::
   :maxdepth: 1
   :caption: Reference

   hardware
   protocol
   protocol-sequences
   protocol/reference
   protocol/frame-layouts
   protocol/signal-matrix
   screenshots
   api

.. toctree::
   :maxdepth: 1
   :caption: Appendix

   UI-DESIGN-RATIONALE
   building-the-docs

Reverse-engineering lab notes
-----------------------------

The dated RE lab notes — how each protocol fact was established, with captures, decompile
citations and dead ends — are **evidence, not product documentation**. They are published
separately from these docs: `Reverse-engineering notes <business-logic/index.html>`_
(also browsable `in the repository <https://github.com/ckeller42/open-california/tree/main/docs/business-logic>`_).

There is also an **interactive LikeC4 model** of the runtime — the architecture plus the eight
protocol sequences as explorable dynamic views: `open the full model <_likec4/index.html>`_ (source:
``docs/likec4/*.c4``, validated in the docs build; the mermaid diagrams in
:doc:`protocol-sequences` stay canonical and carry the requirement links).

.. likec4-view:: index
   :height: 520px

Requirement traceability
------------------------

Protocol sequence diagrams (:doc:`protocol-sequences`) are ``spec`` objects that link to the
requirement each one depicts, so the table below shows the diagram → requirement → test chain.

.. needtable::
   :types: req, spec, test
   :columns: id, title, status, outgoing, incoming
   :style: table
