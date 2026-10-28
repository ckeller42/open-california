// German (de) translations for the web UI, keyed by the English source string used in app.js.
// A missing key falls back to the English source (see t() in app.js), so partial coverage is safe.
// Register: informal "du". Keep keys byte-identical to the literals in app.js.
window.STRINGS_DE = {
  // --- topbar / navigation / chrome ---------------------------------------------------------
  "Vehicle": "Fahrzeug",
  "live": "live",
  "offline": "offline",
  "connecting…": "verbinde…",
  "unreachable": "nicht erreichbar",
  "Sending…": "Wird gesendet…",
  "New version — tap to reload": "Neue Version — zum Neuladen tippen",

  // --- ⋮ menu / Device-status screen ----------------------------------------------------------
  "Device status": "Gerätestatus",
  "Help": "Hilfe",
  "More in the documentation": "Mehr in der Dokumentation",
  "Camper unit": "Camper-Einheit",
  "Last update": "Letzte Aktualisierung",
  "not paired": "nicht gekoppelt",
  "online": "online",
  "Unit firmware": "Firmware der Einheit",
  "amb {amb} · comm {comm}": "amb {amb} · comm {comm}",
  "Please slow down (below 10 km/h)": "Bitte langsamer fahren (unter 10 km/h)",
  "Uptime": "Laufzeit",
  "{m} min": "{m} Min",
  "{h} h {m} min": "{h} Std {m} Min",
  "{d} d {h} h": "{d} Tg {h} Std",
  "Storage": "Speicher",
  "SD {free} of {total} GB free": "SD {free} von {total} GB frei",
  "log #{seq}": "Log Nr. {seq}",
  "No SD card": "Keine SD-Karte",
  "SD card error": "SD-Kartenfehler",
  "{n} records dropped": "{n} Einträge verworfen",
  "Resources": "Ressourcen",
  "RAM {free} KB free (lowest {min})": "RAM {free} KB frei (Minimum {min})",
  "PSRAM {free} KB free": "PSRAM {free} KB frei",
  "CPU {pct} %": "CPU {pct} %",
  "on USB": "an USB",
  "on USB, charging": "an USB, lädt",
  "on battery": "im Akkubetrieb",
  "~{time} left": "noch ~{time}",
  "Satellite settings ›": "Satellit-Einstellungen ›",
  "Connection": "Verbindung",
  "asleep": "im Ruhezustand",
  "Pairing": "Kopplung",
  "Paired ✓": "Gekoppelt ✓",
  "Not paired": "Nicht gekoppelt",
  "Details": "Details",
  "Firmware": "Firmware",
  "WiFi": "WLAN",
  "Backend": "Dienst",
  "Control writes": "Steuerbefehle",
  "Pair…": "Koppeln…",
  "Unpair…": "Entkoppeln…",
  "Unpair the camper unit? Data stops until you pair again.":
    "Camper-Einheit entkoppeln? Bis zum erneuten Koppeln kommen keine Daten.",

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
  "Auto camper on": "Auto-Campingmodus an",
  "Auto camper off": "Auto-Campingmodus aus",
  "Couldn't change auto camper": "Auto-Campingmodus konnte nicht geändert werden",

  // --- lighting ------------------------------------------------------------------------------
  "All lights": "Alle Lichter",
  "Profile": "Profil",
  "Save current as": "Aktuelles speichern als",
  "Profile…": "Profil…",
  "Interior lighting": "Innenlicht",
  "Wake-up light": "Wecklicht",
  "Wake-up time": "Weckzeit",
  "{n} min": "{n} Min.",
  "Wake-up settings not known yet — the unit has not reported them":
    "Wecklicht-Einstellungen noch nicht bekannt — die Einheit hat sie nicht gemeldet",
  "Lead time": "Vorlaufzeit",
  "Brightness": "Helligkeit",
  "Vehicle area": "Fahrzeugbereich",
  "Living area reading lights": "Leselichter Wohnraum",
  "Kitchen background lighting": "Ambientelicht Küche",
  "Pop-up roof reading lights": "Leselichter Aufstelldach",
  "Pop-up roof background lighting": "Ambientelicht Aufstelldach",
  "Lighting & sliding door": "Licht & Schiebetür",
  "Functions & settings": "Funktionen & Einstellungen",
  "Empty — save it under Functions & settings": "Leer — unter Funktionen & Einstellungen speichern",
  "Wake-up light {time}": "Wecklicht {time}",
  "Wake-up light off": "Wecklicht aus",
  "Sliding door light on": "Schiebetür-Licht an",
  "Sliding door light off": "Schiebetür-Licht aus",
  "Opening sliding door activates the rear interior lights.":
    "Beim Öffnen der Schiebetür wird die hintere Innenbeleuchtung eingeschaltet.",
  // no app string (the app hides ECO instead of refusing) — proposal
  "ECO needs a solar panel fitted — the unit reports none (or has not reported it yet), so the app would not offer ECO":
    "ECO braucht eine Solaranlage — die Einheit meldet keine (oder noch nichts), daher bietet die App ECO nicht an",
  "this favourite is empty on the unit — save it first":
    "Dieser Favorit ist auf der Einheit leer — speichere ihn zuerst",
  "the wake-up light needs at least one vehicle area":
    "Das Wecklicht braucht mindestens einen Fahrzeugbereich",
  // refusal reasons a wake-up edit can get (ESP clock / no config reported); no app string — owner-confirmed 2026-10-08
  "the wake-up light needs the time from the web page — set it there":
    "Das Wecklicht braucht die Uhrzeit der Webseite — dort einstellen",
  "wake-up config not known yet (the unit has not reported it): give on|off with the edit":
    "Wecklicht-Einstellungen noch nicht bekannt (die Einheit hat sie nicht gemeldet): Ein/Aus mit angeben",
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
  "Stop timer": "Timer stoppen",
  "Active • {n} min remaining": "Aktiv • Restlaufzeit: {n} min",
  "Inactive • Timer: {t}": "Inaktiv • Timer: {t}",
  "Active • Remaining time: {n} min • Timer starts: {t}": "Aktiv • Restlaufzeit: {n} min • Timer startet: {t}",
  "Active • Continuous heating • Timer starts in {t}": "Aktiv • Dauerbetrieb • Timer startet in {t}",
  "Turn off continuous heating? You can only turn continuous heating on again using the controls in your California.":
    "Dauerbetrieb ausschalten? Den Dauerbetrieb können Sie nur über die Bedienelemente in Ihrem California wieder einschalten.",
  "Apply new settings now? Resetting the duration. The heating will stay on for {n} minutes.":
    "Neue Einstellungen jetzt übernehmen? Die Laufzeit wird zurückgesetzt. Die Heizung bleibt {n} Minuten eingeschaltet.",
  "Continuous heating will turn off when the timer ends. Would you like to proceed?":
    "Der Dauerbetrieb endet, wenn der Timer abläuft. Möchten Sie fortfahren?",
  "Not available during continuous heating": "Im Dauerbetrieb nicht verfügbar",
  "Stop the timer first": "Zuerst den Timer stoppen",
  // server refusals (control.REASON_AIRHEATER_*), toasted through t()
  "the auxiliary air heater cannot be activated when the engine is running or when the auxiliary water heater is activated":
    "die Luftstandheizung kann bei laufendem Motor oder aktivierter Wasser-Standheizung nicht eingeschaltet werden",
  "the run time cannot be changed while continuous heating is on": "die Laufzeit kann im Dauerbetrieb nicht geändert werden",
  "the start time cannot be changed while the timer is on (stop the timer first)":
    "die Startzeit kann bei aktivem Timer nicht geändert werden (zuerst den Timer stoppen)",
  "⚠ Battery voltage too low — charge the second battery or connect external power": "⚠ Batteriespannung zu niedrig — Zweitbatterie laden oder externe Stromquelle anschließen",
  "⚠ Low fuel — the heater switched off, please refuel": "⚠ Kraftstoffstand niedrig — die Heizung wurde abgeschaltet, bitte tanken",
  "⚠ Auxiliary air heater fault — please contact your workshop": "⚠ Störung der Standheizung — bitte Werkstatt kontaktieren",
  "⚠ Emission limit exceeded — the heater switched off, drive ≥ 5 km/h to re-enable": "⚠ Emissionsgrenze überschritten — die Heizung wurde abgeschaltet, ab 5 km/h wieder einschaltbar",
  "⚠ Heater deactivated — not while the engine or the auxiliary water heater is running": "⚠ Standheizung deaktiviert — nicht bei laufendem Motor oder aktiver Wasser-Zusatzheizung",
  "⚠ Auxiliary air heater deactivated due to engine running.": "⚠ Luftstandheizung durch Motorlauf deaktiviert.",
  "⚠ Auxiliary air heater deactivated due to activation of the auxiliary heater.": "⚠ Luftstandheizung deaktiviert durch Aktivierung der Standheizung.",
  "⚠ Heater error": "⚠ Heizungsfehler",
  "⚠ Battery is flat. Charge now!": "⚠ Batterie ist leer. Jetzt laden!",
  "⚠ Something wrong with second battery. Please visit a workshop.": "⚠ Problem mit der Zweitbatterie. Bitte Werkstatt aufsuchen.",
  "⚠ The charging cable is still plugged into the vehicle's external socket. Please remove it.": "⚠ Das Ladekabel steckt noch in der Außensteckdose des Fahrzeugs. Bitte abziehen.",
  "⚠ Charging current is limited. Ambient temperature is too high.": "⚠ Ladestrom ist begrenzt. Umgebungstemperatur zu hoch.",
  "⚠ External power source device not functioning. Please visit a workshop.": "⚠ Gerät für externe Stromversorgung funktioniert nicht. Bitte Werkstatt aufsuchen.",
  "⚠ Charging restricted while the vehicle is moving. Please visit a workshop.": "⚠ Laden während der Fahrt eingeschränkt. Bitte Werkstatt aufsuchen.",
  "⚠ Solar inverter not working. Please visit a workshop.": "⚠ Solar-Wechselrichter funktioniert nicht. Bitte Werkstatt aufsuchen.",
  "⚠ Battery is not charging. Please reduce power consumption.": "⚠ Batterie lädt nicht. Bitte Stromverbrauch reduzieren.",
  "⚠ Additional battery: deep discharge protection is active. Please visit a workshop.": "⚠ Zweitbatterie: Tiefentladungsschutz ist aktiv. Bitte Werkstatt aufsuchen.",
  "⚠ Additional battery: charging required. Power consumers will switch off.": "⚠ Zweitbatterie: Ladung notwendig. Stromverbraucher werden abgeschaltet.",
  "⚠ External power source is currently unavailable.": "⚠ Externe Stromquelle derzeit nicht verfügbar.",
  "⚠ Second battery warning level 2": "⚠ Zweitbatterie Warnstufe 2",
  "⚠ Pump protection is active. Please fill up fresh water.": "⚠ Pumpenschutz ist aktiv. Bitte Frischwasser nachfüllen.",
  "⚠ Fill level sensor in fresh water tank. Emergency water pump operation possible. Please visit a workshop.": "⚠ Füllstandsensor im Frischwassertank. Notbetrieb der Wasserpumpe möglich. Bitte Werkstatt aufsuchen.",
  "⚠ Unknown fresh water error.": "⚠ Unbekannter Frischwasserfehler.",
  "⚠ Fresh water pump. Please visit a workshop.": "⚠ Frischwasserpumpe. Bitte Werkstatt aufsuchen.",
  "⚠ Fresh water is empty. Please refill the fresh water tank.": "⚠ Frischwasser ist leer. Bitte Frischwassertank nachfüllen.",
  "⚠ Grey water tank is full. Please empty.": "⚠ Grauwassertank ist voll. Bitte entleeren.",
  "⚠ Fill level sensor in waste water tank. Please visit a workshop.": "⚠ Füllstandsensor im Abwassertank. Bitte Werkstatt aufsuchen.",
  "⚠ General grey water error. Please visit a workshop.": "⚠ Allgemeiner Grauwasserfehler. Bitte Werkstatt aufsuchen.",
  "Start the fuel-burning auxiliary air heater ({w})? It is not live-verified. Continue?":
    "Kraftstoffbetriebene Luftstandheizung starten ({w})? Nicht live-verifiziert. Fortfahren?",

  // --- water ---------------------------------------------------------------------------------
  "Fresh water": "Frischwasser",
  "Waste water": "Grauwasser",
  "🕒 last measured": "🕒 zuletzt gemessen",
  "🕒 measured {ago}": "🕒 {ago} gemessen",

  // --- external sensors (the ESP's ext, R_FW_SENSORS) ---------------------------------------
  // Victron charge states in VictronConnect's German words (Bulk/Absorption/Float)
  "Sensors": "Sensoren",
  "Solar yield today": "Solarertrag heute",
  "Battery sensor": "Batteriesensor",
  "Temperature": "Temperatur",
  "Humidity": "Luftfeuchte",
  "Solar charger error {n}": "Solarladeregler-Fehler {n}",
  "bulk": "Konstantstrom",
  "absorption": "Konstantspannung",
  "float": "Erhaltungsladung",
  "storage": "Lagerung",
  "equalize": "Ausgleichsladung",
  "starting up": "Startet",
  "recondition": "Regeneration",
  "external control": "Externe Steuerung",
  "Charge phase": "Ladephase",
  "fault": "Fehler",
  "error {n}": "Fehler {n}",

  // --- energy --------------------------------------------------------------------------------
  "Energy mode": "Energiemodus",
  "Max": "MAX",
  "ECO": "ECO",
  "Second battery": "Zweitbatterie",
  "Second battery power": "Zweitbatterie-Leistung",
  "House load": "Bordverbrauch",
  "Time remaining": "Restzeit",
  "Starter battery": "Starterbatterie",
  "Starter power": "Starter-Leistung",
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
  "Please check before opening the pop-up roof: sufficient space above? Access board open? Window or vehicle door open?":
    "Bitte vor dem Öffnen des Aufstelldachs prüfen: Ausreichend Platz über dem Aufstelldach? Durchstiegsboard schon offen? Fenster oder Fahrzeugtür schon offen?",
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
  "Model": "Modell",
  "Level indicator (roll / pitch)": "Niveauanzeige (Roll / Nick)",
  "roll": "Roll",
  "pitch": "Nick",
  "Signal unavailable. Ignition is off.": "Signal nicht verfügbar. Zündung ist aus.",
  "Vehicle clock": "Fahrzeuguhr",
  "Firmware (amb · cm · comm)": "Firmware (Umg. · CM · Komm)",
  "  ⚠ untested": "  ⚠ ungetestet",
  "  ✓ tested": "  ✓ getestet",

  // --- banners (firmware / anchors) ---------------------------------------------------------
  "⚠ Untested firmware — unit reports amb {amb} · comm {comm} (validated on {tested}); readings may be off.":
    "⚠ Ungetestete Firmware — Einheit meldet Umg. {amb} · Komm {comm} (validiert auf {tested}); Werte können abweichen.",
  "⚠ Implausible reading(s): {list} — possible decode drift.":
    "⚠ Unplausible Messwerte: {list} — mögliche Dekodierungsabweichung.",
  "Loading…": "Wird geladen…",
  "Connect the fast BLE session (warm it before controlling)":
    "Schnelle BLE-Sitzung verbinden (vor dem Steuern vorwärmen)",
  "Disconnect — release this session now (it also auto-releases when idle)":
    "Trennen — diese Sitzung jetzt freigeben (im Leerlauf geschieht das automatisch)",

  // --- toasts / status / command feedback ----------------------------------------------------
  "✓ Applied": "✓ Übernommen",
  "Sent — check the lamp": "Gesendet — prüfe die Lampe",
  "Sent — the unit didn't confirm it": "Gesendet — die Einheit hat es nicht bestätigt",
  "Command failed": "Befehl fehlgeschlagen",
  "Read-only mode — writes are disabled": "Nur-Lesen-Modus — Schreibzugriffe sind deaktiviert",
  // the ESP satellite's own answers (app.js ELSEWHERE / CMD_ERRORS; the app has no such strings —
  // proposals, see the PR body).
  "Only via buspi or the app": "Nur über buspi oder die App",
  "Controls work only on your home WiFi — not over the setup hotspot":
    "Steuerung nur im eigenen WLAN — nicht über den Einrichtungs-Hotspot",
  "The satellite is still sending the previous command — try again in a moment":
    "Der Satellit sendet noch den vorherigen Befehl — gleich noch einmal versuchen",
  "Not connected to the camper unit yet — try again in a few seconds":
    "Noch nicht mit der Camper-Einheit verbunden — in ein paar Sekunden noch einmal versuchen",
  "The passcode has 6 digits": "Der Passcode hat 6 Ziffern",
  "Pairing request failed": "Kopplungsanfrage fehlgeschlagen",

  // --- banners -------------------------------------------------------------------------------
  "Satellite — display only": "Satellit — nur Anzeige",
  "🔒 Read-only — control is disabled on this daemon.":
    "🔒 Nur-Lesen — die Steuerung ist auf diesem Dienst deaktiviert.",
  "Offline — van asleep. Last data {clock} ({ago}).":
    "Offline — Fahrzeug im Ruhezustand. Letzte Daten {clock} ({ago}).",
  "Offline — no data yet (van asleep since the monitor started). Checked {clock}.":
    "Offline — noch keine Daten (Fahrzeug seit dem Start im Ruhezustand). Geprüft {clock}.",
  "Not paired yet — no data.": "Noch nicht gekoppelt — keine Daten.",
  "Connecting… Last data {clock} ({ago}).": "Verbinde … Letzte Daten {clock} ({ago}).",
  "Disconnected to save energy": "Verbindung getrennt, um Energie zu sparen",
  "Turn on camping mode to stay connected.": "Schalte den Campingmodus ein, um verbunden zu bleiben.",
  "Turn on camping mode to stay connected. Last data {clock} ({ago}).": "Schalte den Campingmodus ein, um verbunden zu bleiben. Letzte Daten {clock} ({ago}).",
  "Next reading in {next} min.": "Nächste Ablesung in {next} Min.",
  "Reconnect": "Neu verbinden",
  "Open camping mode": "Campingmodus öffnen",
  "power saving": "Energiesparen",
  "reconnecting…": "verbinde neu…",
  "Connecting…": "Verbinde …",
  "⚠ {name} not reachable since {since}. Last data {clock} ({ago}).":
    "⚠ {name} nicht erreichbar seit {since}. Letzte Daten {clock} ({ago}).",
  "⚠ {name} not reachable since {since}.": "⚠ {name} nicht erreichbar seit {since}.",
  "Satellite": "Satellit",
  "{m} min ago": "vor {m} Min",
  "{h} h ago": "vor {h} Std",
  "{d} d ago": "vor {d} T",

  // --- session pill --------------------------------------------------------------------------
  "🔌 Disconnected": "🔌 Getrennt",
  "🛰 Satellite mode": "🛰 Satellit-Modus",
  "🛰 Satellite mode — Pi data from {clock} ({ago}).": "🛰 Satellit-Modus — Pi-Daten von {clock} ({ago}).",
  "🟢 Live · fast": "🟢 Live · schnell",
  "🟡 Connecting": "🟡 Verbindung wird aufgebaut",
  "⚪ Asleep — tap to wake": "⚪ Ruhezustand — zum Aufwecken tippen",

  // --- pairing wizard ------------------------------------------------------------------------
  "Pair the camper unit": "Camper-Einheit koppeln",
  "Close": "Schließen",
  ["On the camper unit open Einstellungen → Bluetooth → Gerät verbinden. It shows "
    + "“Passcode: ---” until buspi connects."]:
    "Öffne an der Camper-Einheit Einstellungen → Bluetooth → Gerät verbinden. Dort steht "
    + "„Passcode: ---“, bis buspi sich verbindet.",
  ["Close the California On Tour app while pairing — its reconnects can collide with the "
    + "passkey window."]:
    "Schließe während der Kopplung die California-On-Tour-App — ihre Verbindungsversuche können "
    + "das Passcode-Fenster stören.",
  ["Stop other Bluetooth scanners on this Pi during pairing (for example the Home Assistant "
    + "Bluetooth integration)."]:
    "Beende während der Kopplung andere Bluetooth-Scanner auf diesem Pi (zum Beispiel die "
    + "Home-Assistant-Bluetooth-Integration).",
  // the satellite's own variants (R_FW_PAIRING_WIZARD)
  ["On the camper unit open Einstellungen → Bluetooth → Gerät verbinden. It shows "
    + "“Passcode: ---” until the satellite connects."]:
    "Öffne an der Camper-Einheit Einstellungen → Bluetooth → Gerät verbinden. Dort steht "
    + "„Passcode: ---“, bis der Satellit sich verbindet.",
  ["If a Raspberry Pi with calictl runs near the van, stop it during pairing — its Bluetooth "
    + "activity can collide with the passkey window."]:
    "Läuft ein Raspberry Pi mit calictl in der Nähe des Fahrzeugs, stoppe ihn während der "
    + "Kopplung — seine Bluetooth-Aktivität kann das Passcode-Fenster stören.",
  "Saved on the satellite — survives a restart. No further action needed.":
    "Auf dem Satelliten gespeichert — übersteht einen Neustart. Keine weitere Aktion nötig.",
  ["Check that “Gerät verbinden” is open on the unit, that the satellite is in range, and that "
    + "no phone is connected to the unit."]:
    "Prüfe, dass „Gerät verbinden“ an der Camper-Einheit geöffnet ist, dass der Satellit in "
    + "Reichweite ist und dass kein Handy mit der Camper-Einheit verbunden ist.",
  ["The unit may be asleep, or a phone or a Raspberry Pi with calictl may still hold its single "
    + "connection. Wake the unit at its panel, disconnect the phone, then try again. An existing "
    + "pairing is kept."]:
    "Die Camper-Einheit schläft vielleicht, oder ein Handy oder ein Raspberry Pi mit calictl belegt "
    + "noch ihre einzige Verbindung. Wecke die Camper-Einheit an ihrem Bildschirm, trenne das Handy "
    + "und versuche es erneut. Eine bestehende Kopplung bleibt erhalten.",
  "I'm on that screen": "Ich bin auf diesem Bildschirm",
  "Pair now": "Jetzt koppeln",
  "Searching…": "Suche…",
  "Connecting to the unit…": "Verbindung wird aufgebaut…",
  ["Enter the passcode now shown on the camper unit (it replaced ---). "
    + "A fresh code each attempt."]:
    "Gib den Passcode ein, der jetzt auf der Camper-Einheit steht (er hat --- ersetzt). "
    + "Bei jedem Versuch ein neuer Code.",
  "Send": "Senden",
  "Pairing…": "Kopplung läuft…",
  "Verifying…": "Wird geprüft…",
  "Resetting…": "Wird zurückgesetzt…",
  "✓ Paired — ": "✓ Gekoppelt — ",
  "paired — address cache unavailable (see logs)":
    "gekoppelt — Adress-Cache nicht verfügbar (siehe Logs)",
  "Saved to the daemon — survives a reboot. No further action needed.":
    "Im Dienst gespeichert — übersteht einen Neustart. Keine weitere Aktion nötig.",
  "Advanced": "Erweitert",
  ["Only needed if you reflash the Pi (a fresh install wipes the saved pairing). "
    + "Set it in /etc/buspi/calictl.env to survive that."]:
    "Nur nötig, wenn du den Pi neu aufsetzt (eine Neuinstallation löscht die gespeicherte "
    + "Kopplung). Trage es in /etc/buspi/calictl.env ein, damit es das übersteht.",
  "Error: ": "Fehler: ",
  "No vehicle found.": "Kein Fahrzeug gefunden.",
  "Something went wrong. Try again.": "Etwas ist schiefgelaufen. Versuche es erneut.",
  "Could not connect to the camper unit.": "Keine Verbindung zur Camper-Einheit.",
  "Pairing was refused.": "Kopplung abgelehnt.",
  "Could not verify the pairing.": "Kopplung konnte nicht verifiziert werden.",
  ["Check that “Gerät verbinden” is open on the unit, that buspi is in range, and that no "
    + "phone is connected to the unit."]:
    "Prüfe, dass „Gerät verbinden“ an der Camper-Einheit geöffnet ist, dass buspi in Reichweite "
    + "ist und dass kein Handy mit der Camper-Einheit verbunden ist.",
  ["The unit may be asleep, a phone may still hold its single connection, or another app on "
    + "this Pi keeps Bluetooth scanning. Wake the unit at its panel, disconnect the phone, pause "
    + "other Bluetooth apps, then try again. An existing pairing is kept."]:
    "Die Camper-Einheit schläft vielleicht, ein Handy belegt noch ihre einzige Verbindung, oder "
    + "eine andere App auf diesem Pi sucht weiter nach Bluetooth-Geräten. Wecke die Camper-Einheit "
    + "an ihrem Bildschirm, trenne das Handy, pausiere andere Bluetooth-Apps und versuche es "
    + "erneut. Eine bestehende Kopplung bleibt erhalten.",
  "Enter the 6-digit passcode shown on the unit.":
    "Gib den 6-stelligen Passcode ein, den die Camper-Einheit anzeigt.",
  "Confirm the unpair first.": "Bestätige zuerst das Entkoppeln.",
  "The daemon did not answer the pairing request. Try again.":
    "Der Dienst hat auf die Kopplungsanfrage nicht geantwortet. Versuche es erneut.",
  ["Wrong passcode, or the unit left pairing mode. Reopen “Gerät verbinden” on the unit and "
    + "try again."]:
    "Falscher Passcode, oder die Camper-Einheit hat den Kopplungsmodus verlassen. Öffne „Gerät "
    + "verbinden“ an der Camper-Einheit erneut und versuche es noch einmal.",
  ["The pairing was made but the unit did not answer. Try again; if it repeats, unpair and pair "
    + "again."]:
    "Die Kopplung wurde hergestellt, aber die Camper-Einheit hat nicht geantwortet. Versuche es "
    + "erneut; wenn das wiederholt passiert, entkopple und kopple neu.",
  "Try again": "Erneut versuchen",
  ["Another app on this Pi keeps Bluetooth scanning — pairing will likely fail until it stops "
    + "(for example the Home Assistant Bluetooth integration)."]:
    "Eine andere App auf diesem Pi sucht dauerhaft nach Bluetooth-Geräten — die Kopplung "
    + "schlägt wahrscheinlich fehl, bis sie stoppt (zum Beispiel die "
    + "Home-Assistant-Bluetooth-Integration).",
  "No camper unit is paired yet.": "Noch keine Camper-Einheit gekoppelt.",

  // --- disabled-control reasons --------------------------------------------------------------
  "Turn camping mode on first": "Zuerst Campingmodus einschalten",
  "Only possible when stationary": "Nur im Stand möglich",
  "Switch the refrigerator box on first": "Zuerst die Kühlbox einschalten",
  "Switch the refrigerator box off first": "Zuerst die Kühlbox ausschalten",
  "Roof move blocked": "Dachbewegung blockiert",

  // --- per-screen Help sheets (⋮ → Help, #314): keys = the HELP texts in app.js ----------------
  ["Each tile is one function of the camper unit; tap it for details. Only functions fitted to this van "
    + "are shown. A ⚠ on a tile means the unit reports a fault there.\n\n"
    + "Sensors: on the ESP satellite, add-on Bluetooth sensors you added in its Satellite settings join the "
    + "screens, named by the alias you gave them: Victron solar charger and battery sensor on Energy, a "
    + "Govee thermometer on Vehicle (its tile then shows the temperature). They are not part of the unit "
    + "and keep updating while it sleeps.\n\n"
    + "Offline: parked, the unit goes into deep sleep and stops answering until the van is used again. "
    + "The page then shows the last data and its age. 🕒 marks a value that was measured earlier, not live. "
    + "Still offline while the van is in use? Check the pairing in Device status.\n\n"
    + "Not paired: no camper unit is paired yet, so there is no data; tap Pair… on the "
    + "banner, or in Device status.\n\n"
    + "Not reachable: this page cannot reach calictl or the satellite (WiFi, Tailscale, power). The van "
    + "itself may be fine.\n\n"
    + "Read-only: control writes are switched off on calictl or the satellite; values still update. An untested-firmware "
    + "warning means the unit runs software this project was not validated on, so decoded values may be off."]:
    "Jede Kachel ist eine Funktion der Camper-Einheit; tippe darauf für Details. Es werden nur "
    + "Funktionen angezeigt, die in diesem Fahrzeug verbaut sind. Ein ⚠ auf einer Kachel heißt, dass "
    + "die Einheit dort eine Störung meldet.\n\n"
    + "Sensoren: Auf dem ESP-Satelliten erscheinen Bluetooth-Sensoren, die du in seinen Satellit-Einstellungen "
    + "hinzugefügt hast, mit dem vergebenen Alias auf den Bildschirmen: Victron-Solarladeregler und "
    + "-Batteriesensor unter Energie, ein Govee-Thermometer unter Fahrzeug (die Kachel zeigt dann die "
    + "Temperatur). Sie gehören nicht zur Einheit und aktualisieren sich auch, wenn sie schläft.\n\n"
    + "Offline: Geparkt fällt die Einheit in Tiefschlaf und "
    + "antwortet nicht mehr, bis das Fahrzeug wieder benutzt wird. Die Seite zeigt dann die letzten "
    + "Daten und ihr Alter. 🕒 kennzeichnet einen Wert, der früher gemessen wurde, nicht "
    + "live. Trotz benutztem Fahrzeug offline? Prüfe die Kopplung im Gerätestatus.\n\nNicht gekoppelt: "
    + "Es ist noch keine Camper-Einheit gekoppelt, daher gibt es keine Daten; kopple sie über den "
    + "Hinweis oder im Gerätestatus (jeweils Koppeln…).\n\nNicht erreichbar: Diese Seite erreicht calictl oder den "
    + "Satelliten nicht (WLAN, Tailscale, Strom). Dem Fahrzeug selbst geht es womöglich gut.\n\n"
    + "Nur-Lesen: Steuerbefehle sind auf calictl bzw. dem Satelliten abgeschaltet; die Werte aktualisieren "
    + "sich trotzdem. Eine Warnung zu ungetesteter Firmware heißt, dass die Einheit Software nutzt, "
    + "auf der dieses Projekt nicht validiert wurde; dekodierte Werte können abweichen.",
  ["Camper unit: Connection is online while the Bluetooth link to the unit works; asleep usually means the "
    + "parked unit sleeps; not paired means no unit is paired yet. Last update is the age of the newest data. "
    + "Pairing says whether calictl or the satellite is paired with a unit (Details shows its Bluetooth "
    + "address). Pair… and Unpair… are here. Still offline while the van is in use? Check the pairing "
    + "here: a reset of the unit's Bluetooth ends it; unpair, then pair again.\n\n"
    + "Satellite (on the ESP satellite): its firmware, WiFi network, uptime, clock, microSD log, battery "
    + "and free memory. Satellite settings › opens its settings: WiFi (change the network there), restore "
    + "camping after you park, screen, firmware update, usage data and sensors.\n\n"
    + "buspi (on the Pi): the calictl backend that serves this page, and whether control writes are on."]:
    "Camper-Einheit: Die Verbindung ist online, solange die Bluetooth-Verbindung zur Einheit steht; "
    + "im Ruhezustand heißt meist, dass die geparkte Einheit schläft; nicht gekoppelt heißt, dass noch "
    + "keine Einheit gekoppelt ist. Letzte Aktualisierung ist das Alter der neuesten Daten. Kopplung "
    + "zeigt, ob calictl oder der Satellit mit einer Einheit gekoppelt ist (Details zeigt ihre "
    + "Bluetooth-Adresse). Koppeln… und Entkoppeln… findest du hier. Trotz benutztem Fahrzeug offline? "
    + "Prüfe hier die Kopplung: ein Bluetooth-Reset der Einheit beendet sie; entkoppeln, dann neu koppeln.\n\n"
    + "Satellit (auf dem ESP-Satelliten): seine Firmware, sein WLAN, Laufzeit, Uhr, das microSD-Log, Akku "
    + "und freier Speicher. Satellit-Einstellungen › öffnet seine Einstellungen: WLAN (dort wechselst du das "
    + "Netzwerk), Campingmodus nach dem Parken wiederherstellen, Bildschirm, Firmware-Update, "
    + "Nutzungsdaten und Sensoren.\n\n"
    + "buspi (auf dem Pi): der calictl-Dienst, der diese Seite liefert, und ob Steuerbefehle an sind.",
  ["Switch the refrigerator box on or off and set its cooling level.\n\n"
    + "Quiet mode: Off, Manual or Automatic. Manual runs quiet mode for up to 23 hours; Automatic runs it "
    + "between the start and end hour you set. Quiet mode can only be set while the box is on.\n\n"
    + "The timer starts cooling at a set time. It can only be set while the box is off.\n\n"
    + "Quiet mode and timer are not yet verified on the van. Banners show what the unit reports: the door "
    + "is not fully closed, emergency mode, or an error for the workshop."]:
    "Schalte die Kühlbox ein oder aus und stelle die Kühlstufe ein.\n\nFlüstermodus: Aus, Manuell "
    + "oder Automatisch. Manuell läuft der Flüstermodus bis zu 23 Stunden; Automatisch zwischen der "
    + "eingestellten Start- und Endstunde. Den Flüstermodus kannst du nur bei eingeschalteter Kühlbox "
    + "einstellen.\n\nDer Timer startet das Kühlen zu einer festen Uhrzeit. Er lässt sich nur bei "
    + "ausgeschalteter Kühlbox einstellen.\n\nFlüstermodus und Timer sind am Fahrzeug noch nicht "
    + "verifiziert. Hinweise zeigen, was die Einheit meldet: Tür nicht ganz geschlossen, Notbetrieb "
    + "oder ein Fehler für die Werkstatt.",
  ["Camping mode is the unit's master switch for the camper functions. Exterior and interior lighting and "
    + "the rear USB ports only work while it is on; the rear USB ports are off whenever camping mode is off.\n\n"
    + "The unit turns camping mode off when the engine starts and does not allow it while the ignition is "
    + "on: it is only possible when stationary.\n\n"
    + "Restore camping after you park: turns camping mode and the rear USB ports back on once you park "
    + "(ignition off), if the engine had switched them off. It respects a manual off and stands down on "
    + "low battery, so it never fights the unit's power saving."]:
    "Der Campingmodus ist der Hauptschalter der Einheit für die Camper-Funktionen. Außen- und "
    + "Innenbeleuchtung und die hinteren USB-Anschlüsse funktionieren nur, solange er an ist; die "
    + "hinteren USB-Anschlüsse sind immer aus, wenn der Campingmodus aus ist.\n\nDie Einheit schaltet "
    + "den Campingmodus beim Motorstart aus und lässt ihn bei eingeschalteter Zündung nicht zu: Er ist "
    + "nur im Stand möglich.\n\nCampingmodus nach dem Parken wiederherstellen: schaltet Campingmodus "
    + "und hintere USB-Anschlüsse wieder ein, sobald du parkst (Zündung aus), falls der Motor sie "
    + "abgeschaltet hatte. Ein manuelles Aus wird berücksichtigt, und bei schwacher Batterie hält sich "
    + "die Funktion zurück, damit sie nie gegen die Energiesparfunktion der Einheit arbeitet.",
  ["All lights switches every lamp on or off. The favourites 1–4 recall light profiles stored on the "
    + "unit; save the current lamp levels to one under Functions & settings.\n\n"
    + "Each lamp has a brightness from 0 (off) to 10 (100 %). The pop-up roof reading light only works "
    + "while the pop-up roof is open.\n\n"
    + "Wake-up light: at the wake-up time the chosen vehicle areas light up to the set brightness; the lead "
    + "time adds a gentle ramp of that many minutes before it. At least one area stays selected.\n\n"
    + "Lighting & sliding door: when on, opening the sliding door switches on the rear interior lights."]:
    "Alle Lichter schaltet jede Leuchte ein oder aus. Die Favoriten 1–4 rufen Lichtprofile ab, die "
    + "auf der Einheit gespeichert sind; die aktuellen Helligkeiten speicherst du unter Funktionen & "
    + "Einstellungen in einem Favoriten.\n\nJede Leuchte hat eine Helligkeit von 0 (aus) bis 10 (100 "
    + "%). Das Leselicht im Aufstelldach funktioniert nur bei geöffnetem Aufstelldach.\n\nWecklicht: "
    + "Zur Weckzeit leuchten die gewählten Fahrzeugbereiche mit der eingestellten Helligkeit; die "
    + "Vorlaufzeit fügt davor ein sanftes Hochdimmen über so viele Minuten hinzu. Mindestens ein "
    + "Bereich bleibt ausgewählt.\n\nLicht & Schiebetür: Wenn an, schaltet das Öffnen der Schiebetür "
    + "die hintere Innenbeleuchtung ein.",
  ["Immediate heating starts the heater now for the set run time (10–120 min). Continuous heating runs "
    + "without a time limit. It can only be started from inside the vehicle; here you can only turn it "
    + "off, and the run time is locked while it runs.\n\n"
    + "Heating temperature is a level from 1 to 9 or HI, not degrees.\n\n"
    + "Timer: set Start heating at, then Start timer. The heater then starts at that time for the run "
    + "time. If continuous heating is on, it turns off when the timer ends.\n\n"
    + "The heater burns fuel, so every start asks first; it is not live-verified. A ⚠ banner shows why "
    + "the unit refused or stopped it, for example low battery, low fuel or a running engine."]:
    "Sofortheizen startet die Heizung jetzt für die eingestellte Laufzeit (10–120 min). Dauerbetrieb "
    + "läuft ohne Zeitbegrenzung. Er lässt sich nur im Fahrzeug starten; hier kannst du ihn nur "
    + "ausschalten, und die Laufzeit ist gesperrt, solange er läuft.\n\nDie Heizstufe ist eine Stufe "
    + "von 1 bis 9 oder HI, keine Gradzahl.\n\nTimer: Stelle Heizen startet um ein und tippe auf Timer "
    + "starten. Die Heizung startet dann zu dieser Zeit für die Laufzeit. Ist Dauerbetrieb an, "
    + "schaltet er sich am Ende des Timers aus.\n\nDie Heizung verbrennt Kraftstoff, daher fragt jeder "
    + "Start nach; sie ist nicht live verifiziert. Ein ⚠-Hinweis zeigt, warum die Einheit sie "
    + "abgelehnt oder gestoppt hat, zum Beispiel schwache Batterie, wenig Kraftstoff oder laufender "
    + "Motor.",
  ["Fresh and waste water are read-only. The unit measures the tanks only while the van's water system "
    + "is running. Parked, it keeps its last measurement, so the level can lag until the water system "
    + "runs again: it is read correctly, just not live.\n\n"
    + "🕒 marks such a held value and when it was measured. A drop to about 1 litre while parked is the "
    + "unit's stale value, not a real reading; the last plausible level is kept until a new measurement "
    + "arrives.\n\n"
    + "⚠ banners are the unit's own tank alerts, for example pump protection, tank empty or full, or a "
    + "sensor fault."]:
    "Frisch- und Grauwasser werden nur gelesen. Die Einheit misst die Tanks nur, während die "
    + "Wasseranlage des Fahrzeugs läuft. Geparkt behält sie die letzte Messung, daher kann der Stand "
    + "hinterherhinken, bis die Wasseranlage wieder läuft: Er wird korrekt gelesen, nur nicht "
    + "live.\n\n🕒 kennzeichnet so einen gehaltenen Wert und wann er gemessen wurde. Ein Abfall auf "
    + "etwa 1 Liter im geparkten Fahrzeug ist der veraltete Wert der Einheit, keine echte Messung; der "
    + "letzte plausible Stand bleibt stehen, bis eine neue Messung kommt.\n\n⚠-Hinweise sind die "
    + "eigenen Tankmeldungen der Einheit, zum Beispiel Pumpenschutz, Tank leer oder voll oder ein "
    + "Sensorfehler.",
  ["Second battery is the leisure battery; it is measured all the time. The starter battery is only "
    + "measured with the engine running, so parked it keeps its last values (🕒 stale).\n\n"
    + "The battery bars come from a coarse level shown in 10 % steps, as in the app, not from a precise "
    + "percentage. Voltages and currents are in volts and amps.\n\n"
    + "Vehicle power is the charger fed by the alternator while the engine runs. Shore and solar power "
    + "appear only if fitted. House load is derived: what the chargers deliver minus what reaches the "
    + "second battery. Issues lists the unit's own warnings.\n\n"
    + "The energy mode is not yet verified on the van, and the unit can lock it."]:
    "Die Zweitbatterie wird ständig gemessen. Die Starterbatterie wird nur bei laufendem Motor "
    + "gemessen, geparkt behält sie daher ihre letzten Werte (🕒 veraltet).\n\nDie Batteriebalken "
    + "beruhen auf einer groben Stufe, angezeigt in 10-%-Schritten wie in der App, nicht auf einem "
    + "genauen Prozentwert. Spannungen und Ströme sind in Volt und Ampere.\n\nFahrzeugstrom ist das "
    + "Ladegerät, das bei laufendem Motor von der Lichtmaschine gespeist wird. Landstrom und "
    + "Solarstrom erscheinen nur, wenn verbaut. Der Bordverbrauch ist abgeleitet: was die Ladegeräte liefern, abzüglich dessen, was "
    + "die Zweitbatterie erreicht. Probleme listet die eigenen Warnungen der "
    + "Einheit.\n\nDer Energiemodus ist am Fahrzeug noch nicht verifiziert, und die Einheit kann ihn "
    + "sperren.",
  ["Press and hold open or close: the roof moves while you hold and stops when you release (or press "
    + "stop). The ignition must be on. The unit may wait about 3 seconds before the motor starts, and a "
    + "new press within 1 second is ignored. The roof stops by itself at its end position.\n\n"
    + "⚠ banners are the unit's own roof alerts; most of them block open and close, stop always works.\n\n"
    + "Roof control is not live-verified: watch the roof while it moves. The ESP satellite has no roof "
    + "control."]:
    "Halte öffnen oder schließen gedrückt: Das Dach bewegt sich, solange du hältst, und stoppt beim "
    + "Loslassen (oder mit stopp). Die Zündung muss an sein. Die Einheit wartet eventuell etwa 3 "
    + "Sekunden, bevor der Motor anläuft, und ein erneuter Druck innerhalb von 1 Sekunde wird "
    + "ignoriert. In der Endlage stoppt das Dach von selbst.\n\n⚠-Hinweise sind die eigenen "
    + "Dachmeldungen der Einheit; die meisten sperren öffnen und schließen, stopp geht immer.\n\nDie "
    + "Dachsteuerung ist nicht live verifiziert: Beobachte das Dach, während es sich bewegt. Der "
    + "ESP-Satellit hat keine Dachsteuerung.",
  ["Ignition shows terminal 15 as the unit reports it.\n\n"
    + "Level indicator: roll and pitch of the van in degrees. They are only available with the ignition "
    + "on; with it off the gauges show -.-°. Above about 10 km/h the unit sends no reading and asks you "
    + "to slow down. Positive pitch means the front is up; the roll direction is not yet confirmed.\n\n"
    + "Vehicle clock is the unit's own clock, advanced live between reads. Firmware shows the unit's "
    + "software versions and whether this project was tested on them."]:
    "Zündung zeigt Klemme 15, wie die Einheit sie meldet.\n\nNiveauanzeige: Roll- und Nickwinkel des "
    + "Fahrzeugs in Grad. Sie gibt es nur bei eingeschalteter Zündung; sonst zeigen die Anzeigen -.-°. "
    + "Über etwa 10 km/h sendet die Einheit keinen Wert und bittet dich, langsamer zu fahren. "
    + "Positiver Nickwinkel heißt: Front oben; die Rollrichtung ist noch nicht "
    + "bestätigt.\n\nFahrzeuguhr ist die eigene Uhr der Einheit, zwischen den Abfragen live "
    + "weitergezählt. Firmware zeigt die Softwarestände der Einheit und ob dieses Projekt darauf "
    + "getestet wurde.",

  // --- Satellite settings + WiFi setup (the words of the satellite's former /device page)
  "Satellite settings": "Satellit-Einstellungen",
  "WiFi setup": "WLAN einrichten",
  "Connect to your WiFi": "Mit deinem WLAN verbinden",
  "Device status ›": "Gerätestatus ›",
  "Clock": "Uhr",
  "synced (SNTP) · boot {boot}": "synchronisiert (SNTP) · Start Nr. {boot}",
  "not synced yet · boot {boot}": "noch nicht synchronisiert · Start Nr. {boot}",
  "no source · boot {boot}": "keine Zeitquelle · Start Nr. {boot}",
  "from the camper unit · boot {boot}": "von der Camper-Einheit · Start Nr. {boot}",
  "Send usage data: on": "Nutzungsdaten senden: an",
  "Send usage data: off": "Nutzungsdaten senden: aus",
  "Sensors: on": "Sensoren: an",
  "Sensors: off": "Sensoren: aus",
  "Scan paused while pairing": "Suche pausiert während der Kopplung",
  "No other sensors nearby (the scan runs every minute).": "Keine weiteren Sensoren in der Nähe (Suche jede Minute).",
  "No sensors found yet (the scan runs every minute).": "Noch keine Sensoren gefunden (die Suche läuft jede Minute).",
  "Could not save the WiFi settings.": "Die WLAN-Einstellungen konnten nicht gespeichert werden.",
  "The satellite rejected the request.": "Der Satellit hat die Anfrage abgelehnt.",
  "Add": "Hinzufügen",
  "Back on {old} within a minute if the new password was wrong; otherwise join the setup hotspot {ap} (password {appsk}) and open:": "Bei falschem Passwort ist er binnen einer Minute zurück in {old}; sonst verbinde dich mit dem Einrichtungs-Hotspot {ap} (Passwort {appsk}) und öffne:",
  "Battery sense (Victron)": "Batteriesensor (Victron)",
  "Brighter": "Heller",
  "Brightness from room light": "Helligkeit nach Raumlicht",
  "Brightness {n}": "Helligkeit {n}",
  "Change network…": "Netzwerk wechseln…",
  "Channel": "Kanal",
  "Check for update": "Nach Update suchen",
  "Choose a network (1–{max} characters).": "Wähle ein Netzwerk (1–{max} Zeichen).",
  "Choose a network and enter its password.": "Wähle ein Netzwerk und gib sein Passwort ein.",
  "Connect": "Verbinden",
  "Connected as {ip}. Join {ssid} with this phone too, then open:": "Verbunden als {ip}. Verbinde auch dieses Handy mit {ssid} und öffne dann:",
  "Connected to {ssid}": "Verbunden mit {ssid}",
  "Connecting to {ssid}…": "Verbinde mit {ssid}…",
  "Could not join the network — try again.": "Verbindung nicht möglich — versuche es erneut.",
  "Couldn't change the channel": "Kanal konnte nicht geändert werden",
  "Couldn't change the screen setting": "Bildschirmeinstellung konnte nicht geändert werden",
  "Couldn't change the sensors": "Sensoren konnten nicht geändert werden",
  "Couldn't change the switch": "Schalter konnte nicht geändert werden",
  "Couldn't change usage data": "Nutzungsdaten konnten nicht geändert werden",
  "Darker": "Dunkler",
  "Reads add-on Bluetooth sensors (Govee, Victron) once a minute.": "Liest Bluetooth-Zusatzsensoren (Govee, Victron) einmal pro Minute.",
  "Encryption key (32 hex digits)": "Verschlüsselungsschlüssel (32 Hex-Zeichen)",
  "Firmware update": "Firmware-Update",
  "Install {sha}": "{sha} installieren",
  "Join {ssid} with this phone, then open:": "Verbinde dieses Handy mit {ssid} und öffne dann:",
  "Key does not match this device": "Schlüssel passt nicht zu diesem Gerät",
  "Key must be 32 hex digits": "Schlüssel muss 32 Hex-Zeichen haben",
  "Name (a–z, 0–9, -, _)": "Name (a–z, 0–9, -, _)",
  "Name already used": "Name schon vergeben",
  "Nearby sensors appear below; add the ones that are yours.": "Sensoren in der Nähe erscheinen unten; füge deine hinzu.",
  "Network": "Netzwerk",
  "Network name": "Netzwerkname",
  "Network not found — check that it is in range, then try again.": "Netzwerk nicht gefunden — prüfe, ob es in Reichweite ist, und versuche es erneut.",
  "No answer from the satellite.": "Keine Antwort vom Satelliten.",
  "No networks listed — type the network's name.": "Keine Netzwerke in der Liste — gib den Namen des Netzwerks ein.",
  "Other network…": "Anderes Netzwerk…",
  "Password": "Passwort",
  "Reload this page in ~30 s, or open:": "Lade die Seite in ~30 s neu oder öffne:",
  "Remove": "Entfernen",
  "Remove {alias}?": "{alias} entfernen?",
  "Rename": "Umbenennen",
  "Retrying the connection…": "Verbindung wird erneut versucht…",
  "Running {sha}": "Läuft: {sha}",
  "Save": "Speichern",
  "Screen": "Bildschirm",
  "Search again": "Erneut suchen",
  "Searching for networks…": "Suche Netzwerke…",
  "Sensor list is full (16)": "Sensorliste ist voll (16)",
  "Show password": "Passwort anzeigen",
  "Solar charger (Victron)": "Solarladeregler (Victron)",
  "Switch network": "Netzwerk wechseln",
  "Switching networks": "Netzwerkwechsel",
  "The new network didn't work ({why}); back on {ssid}.": "Das neue Netzwerk hat nicht funktioniert ({why}); wieder mit {ssid} verbunden.",
  "The password needs {min}–{max} characters.": "Das Passwort braucht {min}–{max} Zeichen.",
  "The satellite leaves {ssid} now. If the new password is wrong, it returns to {ssid} by itself.": "Der Satellit verlässt {ssid} jetzt. Ist das neue Passwort falsch, kehrt er von selbst zu {ssid} zurück.",
  "Thermo-hygrometer (Govee)": "Thermo-Hygrometer (Govee)",
  "Use a–z, 0–9, - or _ (max. 16)": "Nur a–z, 0–9, - oder _ (max. 16)",
  "Wake on motion": "Bei Bewegung einschalten",
  // the Raspberry Pi box (R_FW_PI_WAKE)
  "Raspberry Pi": "Raspberry Pi",
  "Wake on ignition": "Bei Zündung wecken",
  "Wake Pi": "Pi aufwecken",
  "Wake signal sent": "Wecksignal gesendet",
  "Last wake: {n} s ago ({reason})": "Zuletzt geweckt: vor {n} s ({reason})",
  "Couldn't wake the Pi ({error})": "Pi konnte nicht geweckt werden ({error})",
  "Couldn't change the Pi wake": "Pi-Wecken konnte nicht geändert werden",
  "Wakes the Raspberry Pi through pin 5 and shuts it down through pin 11.": "Weckt den Raspberry Pi über Pin 5 und fährt ihn über Pin 11 herunter.",
  "Shut down Pi": "Pi herunterfahren",
  "Shut down the Raspberry Pi?": "Raspberry Pi herunterfahren?",
  "Shutdown signal sent": "Signal zum Herunterfahren gesendet",
  "Couldn't shut down the Pi ({error})": "Pi konnte nicht heruntergefahren werden ({error})",
  "Shut down after parking": "Nach dem Parken herunterfahren",
  "Shut down at low battery": "Bei niedriger Batterie herunterfahren",
  "Always connected": "Immer verbunden",
  "Read every {n} min": "Alle {n} min lesen",
  "Read while in power-save mode": "Im Energiesparmodus lesen",
  "Every {n} min": "Alle {n} min",
  "Every {n} h": "Alle {n} Std",
  "Between reads the satellite lets go of the unit; opening this app connects again within seconds.":
    "Zwischen den Lesevorgängen trennt der Satellit die Verbindung zur Einheit; beim Öffnen dieser App verbindet er sich in Sekunden neu.",
  "Couldn't change the connection": "Verbindung konnte nicht geändert werden",
  "Shutdown in {n} min": "Herunterfahren in {n} min",
  "Shut down {n} min ago ({reason})": "Heruntergefahren vor {n} min ({reason})",
  "Battery low": "Batterie niedrig",
  "Couldn't change the Pi shutdown": "Pi-Herunterfahren konnte nicht geändert werden",
  "Wake when a hand is near": "Bei Annäherung einschalten",
  "Wrong password — try again.": "Falsches Passwort — versuche es erneut.",
  "checking …": "suche …",
  "could not join": "Verbindung nicht möglich",
  "dimmed": "gedimmt",
  "downloading {pct} %": "lade {pct} %",
  "installed, restarting …": "installiert, startet neu …",
  "latest (every merge)": "neueste (jeder Merge)",
  "needs the home WiFi": "braucht das Heim-WLAN",
  "network not found": "Netzwerk nicht gefunden",
  "no light sensor reading": "kein Lichtsensor-Wert",
  "open network, not supported": "offenes Netz, nicht unterstützt",
  "stable (tested)": "stabil (getestet)",
  "up to date": "aktuell",
  "update failed ({error})": "Update fehlgeschlagen ({error})",
  "verifying …": "prüfe …",
  "wrong password": "falsches Passwort",
  "{sha} is available": "{sha} ist verfügbar",
  "⟳ restoring …": "⟳ wird wiederhergestellt …",

  ["WiFi: the network the satellite is on. Change network… lists the networks it finds (Search again "
    + "looks once more); Other network… takes a name. Before it switches it asks: it then leaves the "
    + "current network, and if the new password is wrong it comes back to the old one by itself and "
    + "says so here.\n\nCamper unit: whether a unit is paired; Pair… and Unpair… are in Device "
    + "status.\n\nRestore camping after you park: turns camping mode and the rear USB ports back on once "
    + "you park, if the engine had switched them off.\n\nScreen: the satellite's display. Brightness from "
    + "room light, wake when a hand is near or on motion, and a brightness offset (− / +).\n\nFirmware "
    + "update: the running build and its channel; stable is tested, latest gets every change. Check "
    + "looks for an update, Install loads it and restarts the satellite.\n\nAdvanced: Send usage data "
    + "sends which screens and controls are used (never values). Sensors turns on the scan for "
    + "add-on Bluetooth sensors; below it add the ones that are yours (a Victron needs its "
    + "encryption key), rename or remove them.\n\nOver the setup hotspot these switches need the home "
    + "WiFi."]:
    "WLAN: das Netzwerk, in dem der Satellit ist. Netzwerk wechseln… listet die Netzwerke, die er "
    + "findet (Erneut suchen sucht noch einmal); Anderes Netzwerk… nimmt einen Namen. Vor dem Wechsel "
    + "fragt er nach: er verlässt dann das aktuelle Netzwerk, und ist das neue Passwort falsch, kehrt "
    + "er von selbst zum alten zurück und sagt es hier.\n\nCamper-Einheit: ob eine Einheit gekoppelt ist; "
    + "Koppeln… und Entkoppeln… findest du im Gerätestatus.\n\nCampingmodus nach dem Parken "
    + "wiederherstellen: schaltet Campingmodus und die hinteren USB-Anschlüsse nach dem Parken wieder "
    + "ein, wenn der Motor sie abgeschaltet hatte.\n\nBildschirm: die Anzeige des Satelliten. Helligkeit "
    + "nach Raumlicht, Einschalten bei Annäherung oder Bewegung und eine Helligkeitskorrektur (− / "
    + "+).\n\nFirmware-Update: der laufende Stand und sein Kanal; stabil ist getestet, neueste bekommt "
    + "jede Änderung. Nach Update suchen prüft, ob es ein Update gibt; installieren lädt es und startet "
    + "den Satelliten neu.\n\nErweitert: Nutzungsdaten senden schickt, welche Seiten und Bedienelemente "
    + "genutzt werden (nie Werte). Sensoren schaltet die Suche nach Bluetooth-Zusatzsensoren ein; "
    + "darunter fügst du deine hinzu (ein Victron braucht seinen Verschlüsselungsschlüssel), "
    + "benennst sie um oder entfernst sie.\n\nÜber den Einrichtungs-Hotspot brauchen diese Schalter das "
    + "Heim-WLAN.",
  ["The satellite runs its setup hotspot: it has no home WiFi yet, or cannot reach it. Choose the "
    + "network, enter its password and tap Connect; the satellite then joins it and the hotspot closes. "
    + "Join that network with this phone too and open the address shown.\n\nPairing with the camper unit "
    + "works over the hotspot: tap Pair… and follow the steps. Controls of the unit work only once the "
    + "satellite is on the home WiFi."]:
    "Der Satellit betreibt seinen Einrichtungs-Hotspot: er hat noch kein Heim-WLAN oder erreicht es "
    + "nicht. Wähle das Netzwerk, gib sein Passwort ein und tippe Verbinden; der Satellit verbindet "
    + "sich dann damit und der Hotspot schließt. Verbinde auch dieses Handy mit dem Netzwerk und öffne "
    + "die angezeigte Adresse.\n\nDas Koppeln mit der Camper-Einheit klappt über den Hotspot: tippe "
    + "Koppeln… und folge den Schritten. Die Einheit steuern kannst du erst, wenn der Satellit im "
    + "Heim-WLAN ist.",

  // --- language toggle -----------------------------------------------------------------------
  "Deutsch": "Deutsch",
  "English": "English"
};
