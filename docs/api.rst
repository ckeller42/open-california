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

Daemon state
------------

.. automethod:: calictl.serve.Server._save_last
.. automethod:: calictl.serve.Server._pull_lighting_config

Device / reads
--------------

.. autofunction:: calictl.device._read_char_with_retry
.. autofunction:: calictl.device._later_pushes
.. automethod:: calictl.device.CamperDevice._arm
.. automethod:: calictl.device.CamperDevice.read_all
.. automethod:: calictl.device.CamperDevice.actuate
.. automethod:: calictl.device.CamperDevice.actuate_roof
.. automethod:: calictl.device.CamperDevice._session
.. automethod:: calictl.device.CamperDevice._subscribe_all
.. automethod:: calictl.device.CamperDevice._actuate_on
.. autoclass:: calictl.device.PersistentSession
   :members:
.. autoclass:: calictl.device.RoofStream

Protocol codec
--------------

.. autofunction:: calictl.protocol.check_value

Control frames
--------------

.. autofunction:: calictl.control._airheater
.. autofunction:: calictl.control._int_range
.. autofunction:: calictl.control._cooler
.. autofunction:: calictl.control._lighting
.. automodule:: calictl.postcheck
   :no-members:
.. autofunction:: calictl.postcheck.set_check
.. autofunction:: calictl.control.command_precondition
.. autofunction:: calictl.control.commit_for
.. autofunction:: calictl.control.preface_for
.. autofunction:: calictl.control.next_wakeup_epoch
.. autofunction:: calictl.control.wakeup_request
.. autofunction:: calictl.semantics.lighting_config
.. autofunction:: calictl.semantics.wakeup_config
.. automodule:: tests.test_lighting_commands
.. autofunction:: tests.test_web_serve.test_wakeup_write_is_not_latched_only_the_units_own_frames_are
.. autofunction:: tests.test_web_serve.test_a_pushed_unit_frame_latches_the_lighting_config
.. autofunction:: tests.test_web_serve.test_wakeup_edit_pulls_the_config_with_request_config_then_proceeds

Sinks (MQTT / Home Assistant)
-----------------------------

.. autofunction:: calictl.mqtt.command_topics
.. autofunction:: calictl.mqtt.render_discovery

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
   :members: attach, live_session, note_activity, set_mode, claim_intent, nudge, supervise, mode

Tests
-----

.. autofunction:: tests.test_calictl.test_vehicle_decode_char_1004
.. autofunction:: tests.test_calictl.test_airheater_control_frame
.. autofunction:: tests.test_calictl.test_airheater_timer_time_matches_app_frame
.. autofunction:: tests.test_calictl.test_airheater_timer_start_and_cancel_match_app_frames
.. autofunction:: tests.test_calictl.test_roof_position_name_and_infopopup_alert
.. autofunction:: tests.test_calictl.test_water_stale_latch_guard
.. autofunction:: tests.test_calictl.test_cli_set_check_all_rows
.. autofunction:: tests.test_calictl.test_cooler_quiet_mode_and_schedule_frames
.. autofunction:: tests.test_web_serve.test_serve_state_meta_offline_online_and_persistence
.. autofunction:: tests.test_mock_integration.test_read_all_heartbeat_refreshes_stale_read
.. autofunction:: tests.test_mock_integration.test_read_all_read_after_subscribe_wins_over_stale_push
.. autofunction:: tests.test_mock_integration.test_read_all_later_notify_overrides_the_read
.. autofunction:: tests.test_mock_integration.test_read_all_lighting_config_push_never_replaces_the_read
.. autofunction:: tests.test_mock_integration.test_lighting_applies_without_preamble
.. autofunction:: tests.test_automation.test_no_loop_full_cycle_engine_shed_then_park_then_refused
.. autofunction:: tests.test_automation.test_autocamper_step_restores_via_injected_actuate
.. autofunction:: tests.test_web_serve.test_observer_logs_transitions_and_bursts_on_engine_start
.. automodule:: tests.test_control_extra
.. automodule:: tests.realstack.rig
.. automodule:: tests.test_persistent_session
.. automodule:: tests.test_roof
.. automodule:: tests.test_history
.. automodule:: tests.test_ha
.. autofunction:: tests.e2e.test_gui.test_wakeup_light_time_and_switch_reach_the_unit
.. autofunction:: tests.e2e.test_gui.test_door_contact_switch_round_trips
.. autofunction:: tests.e2e.test_gui.test_all_lights_master_stays_on
.. autofunction:: tests.e2e.test_gui.test_favourite_save_then_activate
.. autofunction:: tests.e2e.test_gui.test_favourite_tiles_are_a_b_c_d_mapped_to_1_5_6_7
.. autofunction:: tests.e2e.test_gui.test_favourite_tile_tap_activates_and_empty_tiles_are_disabled
.. autofunction:: tests.e2e.test_gui.test_lighting_functions_and_settings_are_collapsed_with_a_summary
.. autofunction:: tests.e2e.test_gui.test_wakeup_areas_use_the_t7_labels_and_ranges
.. autofunction:: tests.e2e.test_gui.test_wakeup_areas_are_a_vertical_checkbox_list
.. autofunction:: tests.e2e.test_gui.test_door_contact_row_hidden_on_grand_california
.. autofunction:: tests.e2e.test_satellite.test_controls_are_live_and_post_calictls_command_shape
.. autofunction:: tests.e2e.test_satellite.test_a_command_is_confirmed_from_the_units_own_state
.. autofunction:: tests.e2e.test_satellite.test_an_unconfirmed_write_is_watched_across_the_reconnect
.. autofunction:: tests.e2e.test_satellite.test_wakeup_card_is_live_and_sends_the_browsers_wall_clock
.. autofunction:: tests.e2e.test_satellite.test_wizard_pairs_the_satellite
.. autofunction:: tests.e2e.test_satellite.test_wizard_over_the_setup_hotspot_via_app
.. autofunction:: tests.test_device.test_actuate_arms_then_writes
.. autofunction:: tests.test_device.test_actuate_roof_stops_at_limit_position
.. autofunction:: tests.test_web_serve.test_supervise_releases_session_when_ui_idle
.. autofunction:: tests.test_web_serve.test_roof_move_skips_the_session_warmup
.. autofunction:: tests.test_web_serve.test_roof_move_reuses_a_live_session
.. autofunction:: tests.test_mock_integration.test_roof_move_runs_inside_the_live_session_with_the_heartbeat_ticking
.. autofunction:: tests.test_mock_integration.test_roof_opens_and_closes_on_the_mock_with_the_heartbeat_ticking
.. autofunction:: tests.test_mock_integration.test_roof_release_while_the_press_waits_on_the_lock_never_moves
.. autofunction:: tests.test_mock_integration.test_standalone_roof_stop_without_a_session_has_no_arm_delay
.. autofunction:: tests.test_mock_integration.test_roof_auto_stops_at_the_limit_inside_the_live_session
.. autofunction:: tests.test_mock_integration.test_roof_view_streams_stop_frames_and_a_press_continues_the_counter
.. autofunction:: tests.test_mock_integration.test_roof_checklist_then_a_fresh_press_moves_through_serve
.. autofunction:: tests.test_mock_integration.test_roof_view_lapses_without_a_refresh
.. autofunction:: tests.test_mock_integration.test_roof_view_leave_ends_a_held_move_with_stop
.. autofunction:: tests.test_firmware_anchors.test_firmware_snapshot_captures_raw_frames
.. autofunction:: tests.test_firmware_anchors.test_anchors_flag_implausible_decode
.. autofunction:: tests.test_web_serve.test_web_server_binds_without_reverse_dns

Cross-language codec (issue #156)
---------------------------------

.. automodule:: tools.gen_codec_vectors
.. automodule:: tools.gen_semantics_vectors
.. automodule:: tools.gen_control_vectors
.. automodule:: tools.gen_c_dict
.. automodule:: tools.wifi_consts
.. automodule:: tests.test_codec_vectors
.. automodule:: tests.test_codec_parity
.. automodule:: tests.test_semantics_js_parity
.. automodule:: tests.test_gen_c_dict
.. automodule:: tests.test_finding_artifact_sync
.. automodule:: tests.test_app_bundle
.. automodule:: tests.test_control_vectors
.. automodule:: tests.test_wifi_consts
.. automodule:: tests.test_ports_parity

BLE trace recorder + replay (real-unit evidence for the mock)
-------------------------------------------------------------

.. automodule:: calictl.trace
   :members:

.. automodule:: tools.trace_compare
   :members:

.. automodule:: tests.test_trace
.. automodule:: tests.test_trace_compare
.. automodule:: tests.test_mock_fidelity

.. automodule:: tools.capture_diff
.. automodule:: tests.test_app_recordings

Daemon logging
--------------

.. automodule:: calictl.log
   :members:

.. automodule:: tests.test_log

Guided pairing (issue #154, #157)
----------------------------------

.. automodule:: calictl.pairing
.. automodule:: calictl.pairing_bluez
.. automodule:: tests.test_pairing_sm
.. automodule:: tests.test_pairing_runner
.. automodule:: tests.test_pairing_bluez_transport
.. automodule:: tests.test_pairing_link
.. autofunction:: tests.test_web_serve.test_pairing_start_waits_for_an_in_flight_ble_operation

Firmware (issue #154)
----------------------

.. automodule:: tests.firmware.test_pairing_sm_parity
.. automodule:: tests.firmware.test_runner_fake
.. automodule:: tests.firmware.test_session_fake
.. automodule:: tests.firmware.test_control_parity
.. automodule:: tests.firmware.test_host_e2e
.. automodule:: tests.firmware.test_sdkconfig_gatt_server
.. automodule:: tests.firmware.test_json
.. automodule:: tests.firmware.test_wifi_sm_parity
.. automodule:: tests.firmware.test_http_core
.. automodule:: tests.firmware.test_captive_dns
.. automodule:: tests.firmware.test_web_handlers
.. automodule:: tests.firmware.test_net_host
.. automodule:: tests.firmware.test_web_e2e
.. automodule:: tests.firmware.test_pairing_web_e2e
.. automodule:: tests.firmware.test_control_e2e
.. automodule:: tests.firmware.test_qemu_boot
.. automodule:: tests.test_wifi_sm_ref
.. automodule:: tests.test_web_strings
.. automodule:: tests.test_ux_gallery_esp_fixtures
.. automodule:: tests.firmware.test_display_model
.. automodule:: tests.test_display_font
.. automodule:: tests.test_esp_shot

Fake unit peripheral (Bumble, shared by the app lab + pairing tests)
----------------------------------------------------------------------

.. automodule:: tools.fake_unit_peripheral
.. automodule:: tests.test_fake_unit_peripheral
