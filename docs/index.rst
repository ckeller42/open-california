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
   :caption: Reverse-engineering notes
   :glob:

   business-logic/control-and-actuation
   business-logic/signals
   business-logic/evidence-ledger
   business-logic/DECISIONS
   business-logic/*

.. toctree::
   :maxdepth: 1
   :caption: Appendix

   UI-DESIGN-RATIONALE
   remaining-captures
   building-the-docs

Requirement traceability
------------------------

Protocol sequence diagrams (:doc:`protocol-sequences`) are ``spec`` objects that link to the
requirement each one depicts, so the table below shows the diagram → requirement → test chain.

.. needtable::
   :types: req, spec, test
   :columns: id, title, status, outgoing, incoming
   :style: table
