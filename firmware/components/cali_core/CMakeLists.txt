# Platform-free core: pairing SM, WiFi SM + runtime, runner, session, console + snapshot, JSON writer, HTTP core,
# captive DNS, web endpoints, OTA state machine, boot counter + SNTP/unit clock, SD log core, control twin + sequencer,
# auto camper (decide twin + controller), external BLE sensors. No NimBLE/ESP headers
# (test/ is host-only).
# energy + LoRa (R_FW_ENERGY_COUNTERS, R_FW_LORA); the LoRa port is cali_lora's lora_esp.c on the board,
# lora_port_none.c elsewhere (the QEMU tier: CONFIG_CALI_DISPLAY=n)
set(lora_port "")
if(NOT CONFIG_CALI_DISPLAY)
    set(lora_port "lora_port_none.c")
endif()
idf_component_register(SRCS "energy.c" "lora.c" ${lora_port} "pairing_sm.c" "runner.c" "session.c" "console.c" "snapshot.c" "status.c" "json.c" "wifi_sm.c" "wifi_run.c" "ap_identity.c" "http_core.c" "captive_dns.c" "web.c" "ota.c" "time.c" "sdlog.c" "diag.c" "display_model.c" "display_cfg.c" "battery.c" "sensors.c" "control.c" "control_run.c" "auto_camper.c" "thermostat.c" "thermostat_run.c" "auto_camper_run.c" "pi_wake.c" "ble_exp.c" "ble_exp_run.c"
                       INCLUDE_DIRS "include"
                       PRIV_INCLUDE_DIRS "../../web"
                       REQUIRES csrc
                       PRIV_REQUIRES platform
                       LDFRAGMENTS "linker.lf")
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
