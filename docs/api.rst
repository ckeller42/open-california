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

.. autofunction:: calictl.history.append
.. autofunction:: calictl.history.load
.. autofunction:: calictl.history.trim
.. automethod:: calictl.serve.ServeBackend.history

Device / reads
--------------

.. autofunction:: calictl.device._read_char_with_retry
.. automethod:: calictl.device.CamperDevice._arm
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
.. autofunction:: calictl.control._int_range
.. autofunction:: calictl.postcheck.set_check
.. autofunction:: calictl.control.commit_for

Automation
----------

.. autofunction:: calictl.automation.auto_camper_restore_decide
.. autoclass:: calictl.automation.AutoCamper
   :members: step, set_enabled, snapshot, to_state_dict, load
.. automodule:: calictl.observer
   :no-members:
.. autoclass:: calictl.observer.CampingObserver
   :members: observe, on_push, poll_interval
.. automodule:: calictl.firmware
   :no-members:
.. autofunction:: calictl.firmware.write_snapshot
.. autofunction:: calictl.firmware.changed
.. automodule:: calictl.anchors
   :no-members:
.. autofunction:: calictl.anchors.check
.. automodule:: calictl.session
   :no-members:
.. autoclass:: calictl.web._NoResolveHTTPServer
   :no-members:
.. autoclass:: calictl.session.SessionSupervisor
   :members: attach, live_session, note_activity, set_mode, nudge, supervise, mode

Tests
-----

.. autofunction:: tests.test_calictl.test_vehicle_decode_char_1004
.. autofunction:: tests.test_calictl.test_airheater_control_frame
.. autofunction:: tests.test_mock_integration.test_read_all_heartbeat_refreshes_stale_read
.. autofunction:: tests.test_mock_integration.test_lighting_applies_without_preamble
.. autofunction:: tests.test_automation.test_no_loop_full_cycle_engine_shed_then_park_then_refused
.. autofunction:: tests.test_automation.test_autocamper_step_restores_via_injected_actuate
.. autofunction:: tests.test_web_serve.test_observer_logs_transitions_and_bursts_on_engine_start
.. autofunction:: tests.test_control_extra.test_int_range_helper_validates_and_traces
.. autofunction:: tests.test_persistent_session.test_read_char_retry_reports_disconnect_on_successful_read
.. autofunction:: tests.test_device.test_actuate_arms_then_writes
.. autofunction:: tests.test_web_serve.test_supervise_releases_session_when_ui_idle
.. autofunction:: tests.test_firmware_anchors.test_firmware_snapshot_captures_raw_frames
.. autofunction:: tests.test_firmware_anchors.test_anchors_flag_implausible_decode
.. autofunction:: tests.test_web_serve.test_web_server_binds_without_reverse_dns

Cross-language codec (issue #156)
---------------------------------

.. automodule:: tools.gen_codec_vectors
.. automodule:: tools.gen_c_dict
.. automodule:: tests.test_codec_vectors
.. automodule:: tests.test_codec_parity
.. automodule:: tests.test_gen_c_dict
.. automodule:: tests.test_ports_parity

Guided pairing (issue #154, #157)
----------------------------------

.. automodule:: calictl.pairing
.. automodule:: calictl.pairing_bluez
.. automodule:: tests.test_pairing_sm
