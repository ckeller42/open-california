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
| Power-save rest | "The camper unit saves power — try again in 7 seconds" | "Die Camper-Einheit spart Energie — in 7 Sekunden erneut versuchen" |
| Bluetooth off or not allowed | "Bluetooth is off or not allowed for this app" | "Bluetooth ist aus oder für diese App nicht erlaubt" |
| No answer within 9 seconds | "No answer from the van" | "Keine Antwort vom Fahrzeug" |
| Out of range | "The camper unit is not reachable" | "Die Camper-Einheit ist nicht erreichbar" |

The refusals use the app's own texts, for example "Turn camping mode on first".

## Before you start

1. Install the app on the iPhone and pair it with the unit.
2. On the iPhone, open *Settings → Apple Intelligence & Siri* and make sure that Siri is on.
3. Open the Shortcuts app. Check that the app's four actions show there with a title, a
   description and a symbol, in the iPhone's language.

## Checks

Do each check once with the iPhone in English and once in German (*Settings → General →
Language & Region*, and the Siri language). Use the phrases of the table above. After each
action, check the unit's own screen: it is the ground truth.

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
2. Close the app and wait at least 40 seconds. The unit ends the link to save power.
3. Say "Turn the cooler on in Camper". Check that the cooler switches on within about 9 seconds
   and that Siri answers.
4. Within 7 seconds after the unit ended a link, say the phrase again. Check that Siri says "The
   camper unit saves power — try again in N seconds" and that the app does not connect in a loop.

### Out of range

1. Walk away from the van until the app shows "unreachable".
2. Say "Turn camping mode on in Camper".
3. Check that Siri answers within about 10 seconds with "The camper unit is not reachable" or "No
   answer from the van", and never with "is on".

### With Bluetooth off

1. Turn Bluetooth off in *Settings → Bluetooth* (not only in the Control Center).
2. Force-close the app.
3. Say "Turn the lights on in Camper".
4. Check that Siri says "Bluetooth is off or not allowed for this app" within a few seconds.
5. Turn Bluetooth on again. In *Settings → Privacy & Security → Bluetooth*, turn the app off, and
   repeat steps 3 and 4.

### No air heater

1. Say "Turn the air heater on in Camper" and "Standheizung an in Camper".
2. Check that the app does not act and that the unit's heater stays off.
