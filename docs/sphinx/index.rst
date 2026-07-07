open-california — requirements & API
====================================

Requirements are authored as ``sphinx-needs`` objects **inside code docstrings**
(``.. req::``) and traced to the tests that verify them (``.. test:: … :links:``),
so a failing or missing link surfaces at doc-build time next to the code.

.. toctree::
   :maxdepth: 2

   api

Requirement traceability
------------------------

.. needtable::
   :types: req, test
   :columns: id, title, status, outgoing, incoming
   :style: table
