API & requirements (from docstrings)
====================================

Requirements (``.. req::``) and test cases (``.. test::``) are declared in the
docstrings of the code they describe and are collected here by autodoc, so
traceability is generated from the source, not maintained separately.

Semantics
---------

.. autofunction:: calictl.semantics.vehicle

Device / reads
--------------

.. automethod:: calictl.device.CamperDevice.read_all

Control frames
--------------

.. autofunction:: calictl.control._airheater

Forecast
--------

.. autofunction:: calictl.forecast.days_left

Tests
-----

.. autofunction:: tests.test_calictl.test_vehicle_decode_char_1004
.. autofunction:: tests.test_calictl.test_airheater_control_frame
.. autofunction:: tests.test_mock_integration.test_read_all_heartbeat_refreshes_stale_read
.. autofunction:: tests.test_forecast.test_steady_drain_gives_rate_and_days
