API & requirements (from docstrings)
====================================

Requirements (``.. req::``) and test cases (``.. test::``) are declared in the
docstrings of the code they describe and are collected here by autodoc, so
traceability is generated from the source, not maintained separately.

Semantics
---------

.. autofunction:: calictl.semantics.vehicle
.. autofunction:: calictl.semantics.roof
.. autofunction:: calictl.freshness.implausible_water_drop

Energy history
--------------

.. automodule:: calictl.history
.. autofunction:: calictl.history.append
.. autofunction:: calictl.history.load
.. autofunction:: calictl.history.trim
.. automethod:: calictl.serve.ServeBackend.history

Device / reads
--------------

.. automethod:: calictl.device.CamperDevice.read_all
.. automethod:: calictl.device.CamperDevice.actuate
.. automethod:: calictl.device.CamperDevice.actuate_roof
.. automethod:: calictl.device.CamperDevice._session
.. automethod:: calictl.device.CamperDevice._subscribe_all
.. automethod:: calictl.device.CamperDevice._actuate_on
.. autoclass:: calictl.device.PersistentSession
   :members:

Protocol codec
--------------

.. autofunction:: calictl.protocol.check_value

Control frames
--------------

.. autofunction:: calictl.control._airheater
.. autofunction:: calictl.control.commit_for
.. autofunction:: calictl.control.preamble_for

Tests
-----

.. autofunction:: tests.test_calictl.test_vehicle_decode_char_1004
.. autofunction:: tests.test_calictl.test_airheater_control_frame
.. autofunction:: tests.test_mock_integration.test_read_all_heartbeat_refreshes_stale_read
.. autofunction:: tests.test_mock_integration.test_lighting_applies_without_preamble
