.. _firmware-api:

Firmware C API
==============

The public headers of the satellite's platform-free C core (``firmware/components/cali_core``),
rendered from their Javadoc comments by Doxygen and Breathe. Requirements for C code sit in the
header comments next to the declarations they constrain.

Snapshot
--------

.. doxygenfile:: cali_snapshot.h

Protocol core
-------------

Session
~~~~~~~

.. doxygenfile:: cali_session.h

Pairing runner
~~~~~~~~~~~~~~

.. doxygenfile:: cali_runner.h

Device status
~~~~~~~~~~~~~

.. doxygenfile:: cali_status.h

Control
~~~~~~~

.. doxygenfile:: cali_control.h

Transport, pairing & WiFi
-------------------------

Transport
~~~~~~~~~

.. doxygenfile:: cali_transport.h

Pairing state machine
~~~~~~~~~~~~~~~~~~~~~

.. doxygenfile:: cali_pairing_sm.h

WiFi state machine
~~~~~~~~~~~~~~~~~~

.. doxygenfile:: cali_wifi_sm.h

WiFi runtime
~~~~~~~~~~~~

.. doxygenfile:: cali_wifi_run.h

Captive portal
~~~~~~~~~~~~~~

.. doxygenfile:: cali_captive.h

Network
~~~~~~~

.. doxygenfile:: cali_net.h

Web server & console
--------------------

Web handlers
~~~~~~~~~~~~

.. doxygenfile:: cali_web.h

HTTP core
~~~~~~~~~

.. doxygenfile:: cali_http.h

Console
~~~~~~~

.. doxygenfile:: cali_console.h

JSON writer
~~~~~~~~~~~

.. doxygenfile:: cali_json.h
