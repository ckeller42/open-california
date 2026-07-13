open-california — requirements & API
====================================

.. danger::

   **Use at your own risk — no warranty, no liability.** This software sends control
   writes to a real vehicle (heaters, roof, electrical loads) and can damage your vehicle,
   void your warranty, or create unsafe conditions. It is provided "AS IS" with **NO
   WARRANTY and NO LIABILITY** — if it breaks your car, that is your responsibility. Use it
   only on a vehicle you own. Independent reverse-engineering project, **not affiliated with
   Volkswagen**; no VW intellectual property is distributed. MIT-licensed; see ``LICENSE``
   and ``DISCLAIMER.md``.


Requirements are authored as ``sphinx-needs`` objects **inside code docstrings**
(``.. req::``) and traced to the tests that verify them (``.. test:: … :links:``),
so a failing or missing link surfaces at doc-build time next to the code.

.. toctree::
   :maxdepth: 2

   protocol-sequences
   screenshots
   api

Requirement traceability
------------------------

Protocol sequence diagrams (:doc:`protocol-sequences`) are ``spec`` objects that link to the
requirement each one depicts, so the table below shows the diagram → requirement → test chain.

.. needtable::
   :types: req, spec, test
   :columns: id, title, status, outgoing, incoming
   :style: table
