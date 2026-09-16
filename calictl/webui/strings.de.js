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
  "Middle": "Zwischenposition",
  "Error": "Fehler",
  "Other": "Unbekannt",
  "Ignition on": "Zündung an",
  "Ignition off": "Zündung aus",
  "level": "Stufe",
  "Active": "Aktiv",
  "Inactive": "Inaktiv",
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
  "Refrigerator box": "Kühlbox",
  "Cooling level": "Kühlstufe",
  "Quiet mode": "Flüstermodus",
  "Normal": "Normal",
  "Manual": "Manuell",
  "Automatic": "Automatisch",
  "Quiet mode starts at": "Flüstermodus startet um",
  "Quiet mode ends at": "Flüstermodus endet um",
  "Cooling starts at": "Kühlen startet um",
  "Start timer": "Timer starten",
  "Cancel timer": "Timer abbrechen",
  "Cancel": "Abbrechen",
  "Refrigerator box door": "Kühlbox-Tür",
  "⚠ Open": "⚠ Offen",
  "Timer": "Timer",
  "Automatic quiet mode": "Automatischer Flüstermodus",
  "⚠ Please close the refrigerator box door fully": "⚠ Die Tür der Kühlbox bitte vollständig schließen",
  "⚠ Refrigerator box in emergency mode": "⚠ Kühlbox im Notbetrieb",
  "⚠ Refrigerator box error — please visit a workshop": "⚠ Kühlbox-Fehler — bitte Werkstatt aufsuchen",
  "Set the refrigerator box's quiet mode? Not yet verified on the van. Continue?":
    "Flüstermodus der Kühlbox setzen? Am Fahrzeug noch nicht verifiziert. Fortfahren?",
  "Set the automatic quiet mode start to {h}:00? Not verified on the van. Continue?":
    "Start des automatischen Flüstermodus auf {h}:00 setzen? Am Fahrzeug nicht verifiziert. Fortfahren?",
  "Set the automatic quiet mode end to {h}:00? Not verified on the van. Continue?":
    "Ende des automatischen Flüstermodus auf {h}:00 setzen? Am Fahrzeug nicht verifiziert. Fortfahren?",
  "Set the timer so cooling starts at {t}? Not yet verified on the van. Continue?":
    "Timer setzen, damit das Kühlen um {t} startet? Am Fahrzeug noch nicht verifiziert. Fortfahren?",
  "{b}? Not yet verified on the van. Continue?":
    "{b}? Am Fahrzeug noch nicht verifiziert. Fortfahren?",

  // --- camping mode --------------------------------------------------------------------------
  "Exterior and interior lighting": "Außen- und Innenbeleuchtung",
  "Rear USB ports": "Hintere USB-Anschlüsse",
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
  "Profile": "Profil",
  "Choose…": "Auswählen…",
  "Save current as": "Aktuelles speichern als",
  "Profile…": "Profil…",
  "Interior lighting": "Innenlicht",
  "Wake-up light": "Wecklicht",
  "Reading lights": "Leselichter",
  "Kitchen": "Küche",
  "Pop-up roof": "Aufstelldach",
  "Exterior light": "Außenlicht",
  "Left": "Links",
  "Right": "Rechts",
  "Front passenger": "Beifahrer",
  "Background lighting": "Ambientelicht",
  "Cabinet": "Schrank",
  "Cooking": "Kochen",
  "Reading light": "Leselicht",
  "Rear surroundings": "Umgebung hinten",
  "Entrance": "Eingang",
  "only when the pop-up roof is open": "nur bei geöffnetem Aufstelldach",
  ["Overwrite profile {n} with the current lamp levels? This writes to the unit and is not yet "
    + "verified on the van. Continue?"]:
    "Profil {n} mit den aktuellen Lampenwerten überschreiben? Dies schreibt auf die Einheit und ist "
    + "am Fahrzeug noch nicht verifiziert. Fortfahren?",

  // --- air heater ----------------------------------------------------------------------------
  "Immediate heating": "Sofortheizen",
  "Continuous heating": "Dauerbetrieb",
  "Can only be started from inside the vehicle": "Kann nur im Fahrzeug aktiviert werden",
  "Turn off continuous heating? It can only be turned back on from inside the vehicle. Continue?":
    "Dauerbetrieb ausschalten? Er kann nur im Fahrzeug wieder eingeschaltet werden. Fortfahren?",
  "Heating temperature": "Heizstufe",
  "Run time": "Laufzeit",
  "Remaining": "Restlaufzeit",
  "Start heating at": "Heizen startet um",
  "Level": "Stufe",
  "Timer start": "Timer-Start",
  "Active • {n} min remaining": "Aktiv • Restlaufzeit: {n} min",
  "Inactive • Timer: {t}": "Inaktiv • Timer: {t}",
  "⚠ Heater not started — battery low, run the engine": "⚠ Heizung nicht gestartet — Batterie schwach, Motorlauf durchführen",
  "⚠ Heater not started — fuel level too low": "⚠ Heizung nicht gestartet — Kraftstoffstand zu niedrig",
  "⚠ Heater fault — please visit a workshop": "⚠ Heizungsfehler — bitte Werkstatt aufsuchen",
  "⚠ Heating time exceeded — the heater switched off": "⚠ Heizdauer überschritten — die Heizung wurde abgeschaltet",
  "⚠ Heater currently unavailable": "⚠ Heizung zurzeit nicht verfügbar",
  "⚠ Heater error": "⚠ Heizungsfehler",
  "Start the fuel-burning auxiliary air heater ({w})? It is not live-verified. Continue?":
    "Kraftstoffbetriebene Luftstandheizung starten ({w})? Nicht live-verifiziert. Fortfahren?",

  // --- water ---------------------------------------------------------------------------------
  "Fresh water": "Frischwasser",
  "Waste water": "Grauwasser",
  "🕒 last measured": "🕒 zuletzt gemessen",
  "🕒 Showing the LAST MEASURED water level": "🕒 Es wird der ZULETZT GEMESSENE Wasserstand angezeigt",
  "🕒 Showing the LAST MEASURED water level{ago} — the BLE level only refreshes while the van's water system is running, so it lags until the pump next runs. It's read correctly, just not live.":
    "🕒 Es wird der ZULETZT GEMESSENE Wasserstand angezeigt{ago} — der BLE-Wert aktualisiert sich nur, während die Wasseranlage des Fahrzeugs läuft, und hinkt daher hinterher, bis die Pumpe das nächste Mal läuft. Er wird korrekt gelesen, nur nicht live.",

  // --- energy --------------------------------------------------------------------------------
  "Energy mode": "Energiemodus",
  "Max": "MAX",
  "ECO": "ECO",
  "Second battery": "Zweitbatterie",
  "Second battery voltage": "Zweitbatterie-Spannung",
  "Second battery current": "Zweitbatterie-Strom",
  "Time remaining": "Restzeit",
  "Starter battery": "Starterbatterie",
  "Starter voltage": "Starter-Spannung",
  "Starter current": "Starter-Strom",
  "Vehicle power": "Fahrzeugstrom",
  "Shore power": "Landstrom",
  "Solar power": "Solarstrom",
  "Issues": "Probleme",
  "Starter data age": "Alter der Starterdaten",
  // charger/source state words (dcdc/shore/solar), composed as "<state> (W · A)"
  "active": "aktiv",
  "inactive": "inaktiv",
  "standby": "bereit",
  "on": "an",
  "error": "Fehler",
  "🕒 stale (starter asleep)": "🕒 veraltet (Starter im Ruhezustand)",
  "🕒 Starter-battery values are stale — that subsystem only measures with the engine on, so it holds the last reading while parked. The leisure battery stays live.":
    "🕒 Die Werte der Starterbatterie sind veraltet — dieses Subsystem misst nur bei laufendem Motor und hält daher im geparkten Zustand den letzten Messwert. Die Zweitbatterie bleibt live.",
  // energy chart
  "Second battery — last 24 h": "Zweitbatterie — letzte 24 h",
  "Second battery {name}, last {hours} hours": "Zweitbatterie {name}, letzte {hours} Stunden",
  "voltage": "Spannung",
  "current": "Strom",
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
  "⚠ Roof moved too often — available again in a few minutes":
    "⚠ Aufstelldach wurde zu oft bewegt — in wenigen Minuten wieder verfügbar",
  "⚠ Pop-up roof error — please visit a workshop": "⚠ Fehler beim Aufstelldach — bitte Werkstatt aufsuchen",
  "⚠ Pop-up roof is open — close it before moving the vehicle":
    "⚠ Aufstelldach ist offen — vor Fahrtbeginn schließen",
  "⚠ Check the pop-up roof — jammed or blocked": "⚠ Aufstelldach prüfen — klemmt oder ist blockiert",
  "⚠ Secure the pop-up roof manually (see operating manual)":
    "⚠ Aufstelldach manuell sichern (siehe Bedienungsanleitung)",
  "⚠ Function currently unavailable": "⚠ Funktion zurzeit nicht möglich",
  "⚠ Battery low — run the engine": "⚠ Batterie schwach — Motorlauf durchführen",
  "Function currently in use": "Funktion wird gerade verwendet",
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
  "Level indicator (roll / pitch)": "Niveauanzeige (Roll / Nick)",
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
  "The passcode has 6 digits": "Der Passcode hat 6 Ziffern",
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
  "Set up remote control": "Fernsteuerung einrichten",
  "Close": "Schließen",
  "On the camper control unit open Settings → Bluetooth and press Pair.":
    "Wähle in der Camper-Bedieneinheit Setup → Bluetooth und drücke Verbinden.",
  "I'm on that screen": "Ich bin auf diesem Bildschirm",
  "Connect now": "Jetzt verbinden",
  "Searching…": "Suche…",
  "Connecting…": "Verbindung wird aufgebaut…",
  "Enter the passcode shown on the camper control unit — a fresh code each attempt.":
    "Gib den Passcode von der Camper-Bedieneinheit ein — bei jedem Versuch ein neuer Code.",
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
  "No vehicle found.": "Kein Fahrzeug gefunden.",
  "Connection failed.": "Verbindung fehlgeschlagen.",
  "Could not verify the bond.": "Verbindung konnte nicht verifiziert werden.",
  "Try again": "Erneut versuchen",
  "Bluetooth reset / re-pair": "Bluetooth zurücksetzen / neu koppeln",
  "This removes the working bond; telemetry stops until re-paired. Continue?":
    "Dies entfernt die bestehende Verbindung; die Telemetrie stoppt bis zur erneuten Kopplung. Fortfahren?",

  // --- disabled-control reasons --------------------------------------------------------------
  "Turn camping mode on first": "Zuerst Campingmodus einschalten",
  "Only possible when stationary": "Nur im Stand möglich",
  "Switch the refrigerator box on first": "Zuerst die Kühlbox einschalten",
  "Switch the refrigerator box off first": "Zuerst die Kühlbox ausschalten",
  "Roof move blocked": "Dachbewegung blockiert",

  // --- language toggle -----------------------------------------------------------------------
  "Deutsch": "Deutsch",
  "English": "English"
};
