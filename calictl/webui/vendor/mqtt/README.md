# MQTT.js (vendored)

The remote app (`window.OC_REMOTE`, R_UI_REMOTE_APP) talks to the remote hub's MQTT broker over
`wss://` with [MQTT.js](https://github.com/mqttjs/MQTT.js) **5.16.1**, MIT licence (`LICENSE.md` in
this directory). Only the remote app loads it (`hub.js` uses its global `mqtt`): the ESP's web bundle
does not contain it.

`mqtt.min.js` is an unchanged copy of `dist/mqtt.min.js` from the npm registry tarball
`https://registry.npmjs.org/mqtt/-/mqtt-5.16.1.tgz`, whose integrity is
`sha512-9aEaiR/mLblsMAiynD8quhcGTaINdta2bdyknAtfVzESfoe6Ncdh/uEs5p+3zqcTEtgYErwL4pNk0QkAynXxlg==`
(`npm view mqtt@5.16.1 dist.integrity`).

| File | sha256 |
|---|---|
| `mqtt.min.js` | `75873d04a6400bbbac093b3602caf06f44ed92ee032925e1f4f21c03eba05a4e` |
| `LICENSE.md` | `81b51ef8ac2a8ac8f111ed34ab1a32d63e7f4f7de586bd65f5510fb1ba3a89eb` |

To update: run `npm pack mqtt@<version>` in an empty directory, compare the tarball's sha512 with
`npm view mqtt@<version> dist.integrity`, copy `dist/mqtt.min.js` and `LICENSE.md`, and update the
version and the hashes here.
