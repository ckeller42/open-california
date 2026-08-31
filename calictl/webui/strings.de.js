// German (de) translations for the web UI, keyed by the English source string used in app.js.
// A missing key falls back to the English source (see t() in app.js), so partial coverage is safe.
// Register: informal "du". Keep keys byte-identical to the literals in app.js.
window.STRINGS_DE = {
  // --- topbar / navigation / chrome ---------------------------------------------------------
  "Vehicle": "Fahrzeug",
  "live": "live",
  "offline": "offline",
  "Sending…": "Wird gesendet…",

  // --- ⋮ menu --------------------------------------------------------------------------------
  "Bluetooth pairing…": "Bluetooth-Kopplung…",
  "Unpair…": "Entkoppeln…",
  "Unpair removes the working bond; telemetry stops until re-paired. Continue?":
    "Entkoppeln entfernt die bestehende Verbindung; die Telemetrie stoppt bis zur erneuten Kopplung. Fortfahren?",

  // --- word producers (onoff / yn / positions / small values) -------------------------------
  "On": "An",
  "Off": "Aus",
  "off": "aus",
  "yes": "ja",
  "no": "nein",
  "none": "keine",
  "not installed": "nicht verbaut",
  "Open": "Geöffnet",
  "Closed": "Geschlossen",
  "Middle": "Mittelstellung",
  "Parked": "Geparkt",
  "Ignition on": "Zündung an",
  "level": "Stufe",
  "Running": "Läuft",
  "Fresh": "Frisch",
  "(last meas.)": "(zuletzt gem.)",
  "Battery": "Batterie",
  "def": "Std",
  "unknown": "unbekannt",
  "just now": "gerade eben",

  // --- feature titles (tiles + screen headers) ----------------------------------------------
  "Cooler": "Kühlbox",
  "Camping mode": "Campingmodus",
  "Lighting": "Beleuchtung",
  "Air heater": "Luftstandheizung",
  "Water": "Wasser",
  "Energy": "Energie",
  "Roof": "Aufstelldach",

  // --- cooler --------------------------------------------------------------------------------
  "Refrigerator": "Kühlbox",
  "Cooling level": "Kühlstufe",
  "Quiet mode": "Flüstermodus",
  "Normal": "Normal",
  "Quiet": "Flüstermodus",
  "Timer quiet": "Timer-Flüstermodus",
  "Quiet from": "Flüstermodus ab",
  "Quiet until": "Flüstermodus bis",
  "Timer start at": "Timer-Start um",
  "Cooling timer": "Kühl-Timer",
  "Arm": "Aktivieren",
  "Cancel": "Abbrechen",
  "Fridge door": "Kühlschranktür",
  "⚠ Open": "⚠ Offen",
  "Timer": "Timer",
  "Quiet schedule": "Flüsterzeitplan",
  "⚠ Fridge door is open": "⚠ Kühlschranktür ist offen",
  "⚠ Cooler in emergency operation": "⚠ Kühlbox im Notbetrieb",
  "⚠ Cooler error": "⚠ Kühlbox-Fehler",
  "Set the cooler's quiet mode? Not yet verified on the van. Continue?":
    "Flüstermodus der Kühlbox setzen? Am Fahrzeug noch nicht verifiziert. Fortfahren?",
  "Set quiet-schedule start to {h}:00? Not verified on the van. Continue?":
    "Flüsterzeitplan-Start auf {h}:00 setzen? Am Fahrzeug nicht verifiziert. Fortfahren?",
  "Set quiet-schedule end to {h}:00? Not verified on the van. Continue?":
    "Flüsterzeitplan-Ende auf {h}:00 setzen? Am Fahrzeug nicht verifiziert. Fortfahren?",
  "Set the cooling-timer start to {t}? Not yet verified on the van. Continue?":
    "Kühl-Timer-Start auf {t} setzen? Am Fahrzeug noch nicht verifiziert. Fortfahren?",
  "{b} the cooling timer? Not yet verified on the van. Continue?":
    "Kühl-Timer {b}? Am Fahrzeug noch nicht verifiziert. Fortfahren?",

  // --- camping mode --------------------------------------------------------------------------
  "Interior + outside lights": "Innen- und Außenbeleuchtung",
  "Rear USB ports": "Hintere USB-Anschlüsse",
  "Ignition (terminal-15)": "Zündung (Klemme 15)",
  "Restore camping after you park": "Campingmodus nach dem Parken wiederherstellen",
  "⟳ will restore on park": "⟳ wird beim Parken wiederhergestellt",
  ["The unit drops camper mode when the engine starts and won't allow it back on while driving. "
    + "This turns camper mode + rear USB back on once you park (ignition off), if the engine had "
    + "shed it. Respects a manual off, and stands down on low battery so it never fights "
    + "the unit's power saving."]:
    "Die Einheit deaktiviert den Campingmodus beim Motorstart und lässt ihn während der Fahrt nicht "
    + "wieder zu. Diese Funktion schaltet Campingmodus + hintere USB-Anschlüsse wieder ein, sobald "
    + "du parkst (Zündung aus), falls der Motor sie abgeschaltet hatte. Berücksichtigt ein manuelles "
    + "Aus und hält sich bei schwacher Batterie zurück, um nie gegen die Energiesparfunktion der "
    + "Einheit zu arbeiten.",
  "Auto camper on": "Auto-Campingmodus an",
  "Auto camper off": "Auto-Campingmodus aus",
  "Couldn't change auto camper": "Auto-Campingmodus konnte nicht geändert werden",

  // --- lighting ------------------------------------------------------------------------------
  "All lights": "Alle Lichter",
  "Activate profile": "Profil aktivieren",
  "Choose…": "Auswählen…",
  "Save current as": "Aktuelles speichern als",
  "Favorite…": "Favorit…",
  "Favorite": "Favorit",
  "Interior light": "Innenlicht",
  "Wake-up light": "Wecklicht",
  "Reading lights": "Leselichter",
  "Kitchen": "Küche",
  "Pop-roof": "Aufstelldach",
  "Ambient / outside": "Ambiente / außen",
  "Left": "Links",
  "Right": "Rechts",
  "Front": "Vorne",
  "Ambient": "Ambiente",
  "Cabinet": "Schrank",
  "Cooking": "Kochen",
  "Reading": "Lesen",
  "Rear surround": "Heck-Umfeld",
  "Entrance": "Eingang",
  "roof open only": "nur bei offenem Dach",
  "roof must be open": "Dach muss offen sein",
  ["Overwrite Favorite {n} with the current lamp levels? This writes to the unit and is not yet "
    + "verified on the van. Continue?"]:
    "Favorit {n} mit den aktuellen Lampenwerten überschreiben? Dies schreibt auf die Einheit und ist "
    + "am Fahrzeug noch nicht verifiziert. Fortfahren?",

  // --- air heater ----------------------------------------------------------------------------
  "Parking heater": "Standheizung",
  "Heating level (10 = HI)": "Heizstufe (10 = HI)",
  "Run time": "Laufzeit",
  "Start at": "Start um",
  "Level": "Stufe",
  "Running time": "Laufzeit",
  "Timer start": "Timer-Start",
  "Error code": "Fehlercode",
  "Start the fuel-burning parking heater ({w})? It is not live-verified. Continue?":
    "Kraftstoffbetriebene Standheizung starten ({w})? Nicht live-verifiziert. Fortfahren?",

  // --- water ---------------------------------------------------------------------------------
  "Fresh water": "Frischwasser",
  "Waste water": "Grauwasser",
  "Grey water": "Grauwasser",
  "🕒 Showing the LAST MEASURED water level": "🕒 Es wird der ZULETZT GEMESSENE Wasserstand angezeigt",
  "🕒 Showing the LAST MEASURED water level{ago} — the BLE level only refreshes while the van's water system is running, so it lags until the pump next runs. It's read correctly, just not live.":
    "🕒 Es wird der ZULETZT GEMESSENE Wasserstand angezeigt{ago} — der BLE-Wert aktualisiert sich nur, während die Wasseranlage des Fahrzeugs läuft, und hinkt daher hinterher, bis die Pumpe das nächste Mal läuft. Er wird korrekt gelesen, nur nicht live.",

  // --- energy --------------------------------------------------------------------------------
  "Energy mode": "Energiemodus",
  "Max charge": "Maximale Ladung",
  "Eco": "Eco",
  "Living battery": "Wohnraumbatterie",
  "Living voltage": "Wohnraum-Spannung",
  "Living current": "Wohnraum-Strom",
  "Time remaining": "Restzeit",
  "Starter battery": "Starterbatterie",
  "Starter voltage": "Starter-Spannung",
  "Starter current": "Starter-Strom",
  "DC-DC charger": "DC-DC-Wandler",
  "Shore power": "Landstrom",
  "Solar": "Solar",
  "Warnings": "Warnungen",
  "Starter data age": "Alter der Starterdaten",
  "Second battery": "Zweitbatterie",
  // charger/source state words (dcdc/shore/solar), composed as "<state> (W · A)"
  "active": "aktiv",
  "inactive": "inaktiv",
  "standby": "Bereitschaft",
  "on": "an",
  "error": "Fehler",
  "🕒 stale (starter asleep)": "🕒 veraltet (Starter im Ruhezustand)",
  "🕒 Starter-battery values are stale — that subsystem only measures with the engine on, so it holds the last reading while parked. The leisure battery stays live.":
    "🕒 Die Werte der Starterbatterie sind veraltet — dieses Subsystem misst nur bei laufendem Motor und hält daher im geparkten Zustand den letzten Messwert. Die Wohnraumbatterie bleibt live.",
  // energy chart
  "Leisure battery — last 24 h": "Wohnraumbatterie — letzte 24 h",
  "Voltage (V)": "Spannung (V)",
  "Current (A)": "Strom (A)",
  "now": "jetzt",
  "No data in the last 24 h — van asleep since {clock}.":
    "Keine Daten in den letzten 24 h — Fahrzeug seit {clock} im Ruhezustand.",
  "No data yet — history builds while the van is awake.":
    "Noch keine Daten — der Verlauf entsteht, während das Fahrzeug wach ist.",
  ["Set the energy management mode? This control is derived from the app and not yet verified on "
    + "the van. Continue?"]:
    "Energiemanagement-Modus setzen? Diese Funktion ist abgeleitet und am Fahrzeug noch nicht "
    + "verifiziert. Fortfahren?",

  // --- roof ----------------------------------------------------------------------------------
  "Position": "Position",
  "Safety valid": "Sicherheit gültig",
  "Alert": "Warnung",
  "⚠ Roof child lock active": "⚠ Dach-Kindersicherung aktiv",
  "⚠ Roof error": "⚠ Dach-Fehler",
  "⚠ Roof sensor error": "⚠ Dach-Sensorfehler",
  "⚠ Roof emergency-locked": "⚠ Dach notverriegelt",
  "⚠ Roof operation not possible right now": "⚠ Dachbetätigung derzeit nicht möglich",
  "⚠ Battery too low to operate roof": "⚠ Batterie zu schwach für Dachbetätigung",
  "Roof control is safety-sensitive and not live-verified.":
    "Die Dachsteuerung ist sicherheitskritisch und nicht live-verifiziert.",
  "open": "öffnen",
  "close": "schließen",
  "stop": "stopp",
  "Roof {dir}: hold to move the pop-top (UNVERIFIED on this vehicle). Release to stop. Path clear?":
    "Dach {dir}: halten, um das Aufstelldach zu bewegen (an diesem Fahrzeug UNVERIFIZIERT). "
    + "Loslassen zum Stoppen. Weg frei?",

  // --- vehicle -------------------------------------------------------------------------------
  "Ignition": "Zündung",
  "Leveling (roll / pitch)": "Nivellierung (Roll / Nick)",
  "roll": "Roll",
  "pitch": "Nick",
  "level ✓": "eben ✓",
  "Vehicle clock": "Fahrzeuguhr",
  "Firmware (amb · cm · comm)": "Firmware (Umg. · CM · Komm)",
  "  ⚠ untested": "  ⚠ ungetestet",
  "  ✓ tested": "  ✓ getestet",

  // --- banners (firmware / anchors) + energy chart -------------------------------------------
  ["⚠ Untested firmware — unit reports amb {amb} · comm {comm} (this project was validated on {tested}). "
    + "Decode/semantics may have drifted; treat readings with care."]:
    "⚠ Ungetestete Firmware — Einheit meldet Umg. {amb} · Komm {comm} (dieses Projekt wurde auf {tested} "
    + "validiert). Dekodierung/Semantik könnte abweichen; Werte mit Vorsicht behandeln.",
  "⚠ Implausible reading(s): {list} — possible decode drift.":
    "⚠ Unplausible Messwerte: {list} — mögliche Dekodierungsabweichung.",
  "History unavailable.": "Verlauf nicht verfügbar.",
  "Loading…": "Wird geladen…",
  "Connect the fast BLE session (warm it before controlling)":
    "Schnelle BLE-Sitzung verbinden (vor dem Steuern vorwärmen)",
  "Disconnect — free the BLE slot for the phone app":
    "Trennen — den BLE-Platz für die Telefon-App freigeben",

  // --- toasts / status / command feedback ----------------------------------------------------
  "✓ Applied": "✓ Übernommen",
  "Sent — check the lamp": "Gesendet — prüfe die Lampe",
  "Sent — the unit didn't confirm it": "Gesendet — die Einheit hat es nicht bestätigt",
  "Command failed": "Befehl fehlgeschlagen",
  "Read-only mode — writes are disabled": "Nur-Lesen-Modus — Schreibzugriffe sind deaktiviert",
  "Enter exactly 6 digits": "Genau 6 Ziffern eingeben",
  "Pairing request failed": "Kopplungsanfrage fehlgeschlagen",

  // --- banners -------------------------------------------------------------------------------
  "🔒 Read-only — control is disabled on this daemon.":
    "🔒 Nur-Lesen — die Steuerung ist auf diesem Dienst deaktiviert.",
  "Offline — van asleep. Last data {clock} ({ago}).":
    "Offline — Fahrzeug im Ruhezustand. Letzte Daten {clock} ({ago}).",
  "Offline — no data yet (van asleep since the monitor started). Checked {clock}.":
    "Offline — noch keine Daten (Fahrzeug seit dem Start im Ruhezustand). Geprüft {clock}.",
  "{m} min ago": "vor {m} Min",
  "{h} h ago": "vor {h} Std",
  "{d} d ago": "vor {d} T",

  // --- session pill --------------------------------------------------------------------------
  "🔌 Disconnected": "🔌 Getrennt",
  "🟢 Live · fast": "🟢 Live · schnell",
  "🟡 Connecting": "🟡 Verbindung wird aufgebaut",
  "⚪ Asleep — tap to wake": "⚪ Ruhezustand — zum Aufwecken tippen",

  // --- pairing wizard ------------------------------------------------------------------------
  "Bluetooth setup": "Bluetooth-Einrichtung",
  "Close": "Schließen",
  "On the camper panel open Bluetooth → ‘Gerät verbinden’.":
    "Öffne am Bedienfeld des Campers Bluetooth → ‘Gerät verbinden’.",
  "I'm on that screen": "Ich bin auf diesem Bildschirm",
  "Start": "Starten",
  "Searching for the camper unit…": "Suche nach der Camper-Einheit…",
  "Connecting…": "Verbindung wird aufgebaut…",
  "Read it from the camper's screen — a fresh code each attempt.":
    "Lies ihn vom Bildschirm des Campers ab — bei jedem Versuch ein neuer Code.",
  "Send": "Senden",
  "Pairing…": "Kopplung läuft…",
  "Verifying…": "Wird geprüft…",
  "Resetting…": "Wird zurückgesetzt…",
  "✓ Paired — ": "✓ Gekoppelt — ",
  "bonded — address cache unavailable (see logs)":
    "gekoppelt — Adress-Cache nicht verfügbar (siehe Logs)",
  "Saved to the daemon — survives a reboot. No further action needed.":
    "Im Dienst gespeichert — übersteht einen Neustart. Keine weitere Aktion nötig.",
  ["Advanced: only needed if you reflash the Pi (a fresh install wipes the saved bond). "
    + "Set it in /etc/buspi/calictl.env to survive that."]:
    "Fortgeschritten: nur nötig, wenn du den Pi neu aufsetzt (eine Neuinstallation löscht die "
    + "gespeicherte Verbindung). Trage es in /etc/buspi/calictl.env ein, damit es das übersteht.",
  "Error: ": "Fehler: ",
  "Timed out waiting for the camper unit.": "Zeitüberschreitung beim Warten auf die Camper-Einheit.",
  "Pairing failed.": "Kopplung fehlgeschlagen.",
  "Could not verify the bond.": "Verbindung konnte nicht verifiziert werden.",
  "Retry": "Erneut versuchen",
  "Bluetooth reset / re-pair": "Bluetooth zurücksetzen / neu koppeln",
  "This removes the working bond; telemetry stops until re-paired. Continue?":
    "Dies entfernt die bestehende Verbindung; die Telemetrie stoppt bis zur erneuten Kopplung. Fortfahren?",

  // --- language toggle -----------------------------------------------------------------------
  "Deutsch": "Deutsch",
  "English": "English"
};
