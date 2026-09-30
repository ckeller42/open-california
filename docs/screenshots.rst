Web UI
======

``calictl serve --web`` serves a browser replica of the camper's Vehicle-tab controls — a
dashboard with an at-a-glance status card, then per-feature screens — from the **same** daemon,
with no extra BLE connection. The images below are rendered automatically in CI against the mock
unit (``tools.ux_gallery``), so they always reflect the current build.

Dashboard
---------

The status overview (fresh/grey water + leisure battery) plus feature tiles.

.. image:: screenshots/light_00_dashboard.png
   :width: 300
   :alt: Dashboard — California Status overview and feature tiles

Energy
------

Leisure + starter battery, voltages, **currents**, and the DC-DC / shore / solar sources.

.. image:: screenshots/light_06_Energy.png
   :width: 300
   :alt: Energy screen

Water
-----

.. image:: screenshots/light_05_Water.png
   :width: 300
   :alt: Water screen — fresh and grey tanks

Lighting
--------

.. image:: screenshots/light_03_Lighting.png
   :width: 300
   :alt: Lighting screen — profile + per-lamp control

Vehicle
-------

.. image:: screenshots/light_07_Vehicle.png
   :width: 300
   :alt: Vehicle screen — ignition, leveling, clock

Every screen also renders in dark mode (``docs/screenshots/dark_*.png``).

ESP32 satellite page
--------------------

The status/setup page the ESP32 firmware serves (#154) — setup-hotspot mode and station mode. Rendered
by ``python -m tools.ux_gallery --esp`` from the generated page bytes plus canned API responses (no
firmware or radio involved); see :doc:`howto-esp-wifi-setup`.

.. image:: screenshots/esp-setup-page.png
   :width: 300
   :alt: ESP32 setup page — network list, password, Connect

.. image:: screenshots/esp-status-page.png
   :width: 300
   :alt: ESP32 status page — device state and camper-unit functions
