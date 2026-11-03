# iOS app: Siri checklist

The unit tests run each Siri and Shortcuts action ({term}`App Intents`) over the fake transport:
the command it sends, and what Siri says back in English and German. The simulator has no Siri,
so the spoken phrases are checked on a real iPhone at the van with this checklist.

## What Siri offers

The app's name is `Camper`. Each phrase contains it. Siri has no air-heater action: the heater is
switched only on its own screen in the app.

| Action | Command | English phrases | German phrases |
|---|---|---|---|
| Camping mode on or off | `campingmode master on` or `off` | "Turn camping mode on in Camper", "Camper camping mode off" | "Campingmodus an in Camper", "Camper Campingmodus aus" |
| All lights on or off | `lighting power on` or `off` | "Turn the lights on in Camper", "Camper lights off" | "Licht an in Camper", "Camper Licht aus" |
| Light favourite A, B, C or D (also 1 to 4) | `lighting profile 1`, `5`, `6` or `7` | "Light favourite B in Camper", "Camper favourite 2" | "Lichtfavorit B in Camper", "Camper Favorit 2" |
| Cooler on or off | `cooler power on` or `off` | "Turn the cooler on in Camper", "Camper cooler off" | "Kühlbox an in Camper", "Camper Kühlbox aus" |

## What Siri answers

| Result | English | German |
|---|---|---|
| The unit confirmed it | "Camping mode is on", "All lights are off", "Favourite B is on", "The cooler is on" | "Campingmodus ist an", "Alle Lichter sind aus", "Favorit B ist an", "Die Kühlbox ist an" |
| Sent, the unit did not confirm it | "Camping mode: sent, not confirmed" | "Campingmodus: gesendet, nicht bestätigt" |
| Lights sent | "Lighting: sent, check the lamp" | "Beleuchtung: gesendet, prüfe die Lampe" |
| Power-save rest, early in the unit's reconnect wait | "The camper unit saves power — try again in 5 seconds" | "Die Camper-Einheit spart Energie — in 5 Sekunden erneut versuchen" |
| Bluetooth off or not allowed | "Bluetooth is off or not allowed for this app" | "Bluetooth ist aus oder für diese App nicht erlaubt" |
| Out of range | "The camper unit is not reachable" | "Die Camper-Einheit ist nicht erreichbar" |
| Another command still runs | "The previous command is still running — try again in a moment" | "Der vorige Befehl läuft noch — bitte gleich noch einmal versuchen" |
| 9 seconds passed, a frame went out | "Camping mode: sent, but the camper unit did not confirm in time" | "Campingmodus: gesendet, aber die Camper-Einheit hat es nicht rechtzeitig bestätigt" |
| 9 seconds passed, nothing went out | "The camper unit did not answer in time" | "Die Camper-Einheit hat nicht rechtzeitig geantwortet" |

The refusals use the app's own texts, for example "Turn camping mode on first".

On a locked iPhone, Siri switches without Face ID.

The unit does not take a new link for about 7 seconds after it ended the last one. While more
than about 2 seconds of this wait are left, Siri answers "try again" and does not connect. Later,
Siri connects once and runs the action.

## Before you start

1. Install the app on the iPhone and pair it with the unit.
2. On the iPhone, open *Settings → Apple Intelligence & Siri* and make sure that Siri is on.
3. Open the Shortcuts app. Check that the app's four actions show there with a title, a
   description and a symbol, in the iPhone's language.

## Checks

Do each check once with the iPhone in English and once in German (*Settings → General →
Language & Region*, and the Siri language). Use the phrases of the table above. After each
action, check the unit's own screen: it is the ground truth.

### Locked iPhone

1. Lock the iPhone.
2. Say "Turn the cooler on in Camper".
3. Check that Siri does not ask for Face ID or the passcode.
4. Check that the cooler switches on and that Siri answers.

### With the link open

1. Open the app and wait until it shows "live".
2. Close the app (it stays in the background).
3. Say each phrase of the table, the "on" and the "off" variant of each.
4. Check that each action changes the unit within about 2 seconds.
5. Check that Siri says "is on" or "is off" only when the unit really changed, and "sent, not
   confirmed" or "check the lamp" otherwise.
6. Say "Light favourite 2 in Camper". Check that the unit shows favourite B.

### With the link closed (camping mode off)

1. Turn camping mode off and make sure that no shore power is connected.
2. Keep the app open and wait until the pill shows "Disconnected to save energy". The unit ended
   the link to save power.
3. Within 3 seconds, say "Turn the cooler on in Camper". Check that Siri says "The camper unit
   saves power — try again in N seconds" and that the app does not connect.
4. Close the app. Wait 10 seconds.
5. Say "Turn the cooler on in Camper". Start a stopwatch at the end of the phrase. Check that the
   cooler switches on and that Siri answers. Write down the time until the unit's screen changes:
   it must stay well under 10 seconds, the time that Siri gives an action.
6. Wait for the next "Disconnected to save energy", then 10 seconds. Repeat step 5 with "Turn the
   lights on in Camper", "Light favourite A in Camper" and "Turn camping mode on in Camper".
7. After camping mode on, check that the app keeps the link (the unit does not end it after 30
   seconds).

### Another command still runs

1. Open the app on the cooler screen while the link is open.
2. Switch the cooler and, while the banner shows "Sending…", say "Turn the lights on in Camper".
3. Check that Siri says "The previous command is still running — try again in a moment" and that
   the lights do not change.

### Out of range

1. Walk away from the van until the app shows "unreachable".
2. Say each of the four actions.
3. Check that Siri answers within about 10 seconds with "The camper unit is not reachable" or "The
   camper unit did not answer in time", and never with "is on".

### With Bluetooth off

1. Turn Bluetooth off in *Settings → Bluetooth* (not only in the Control Center).
2. Force-close the app.
3. Say each of the four actions.
4. Check that Siri says "Bluetooth is off or not allowed for this app" within a few seconds.
5. Turn Bluetooth on again. In *Settings → Privacy & Security → Bluetooth*, turn the app off, and
   repeat steps 3 and 4.

### No air heater

1. Say "Turn the air heater on in Camper" and "Standheizung an in Camper".
2. Check that the app does not act and that the unit's heater stays off.
