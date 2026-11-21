---
name: hub-setup-file
description: Use when the owner wants the satellite's remote-hub settings (broker wss URL, van name, van and app logins) as a file for the Hub form's "Load from file" button, instead of typing them on the phone — making oc-hub.json from ~/.env, putting it on a phone (iCloud Drive), or after a broker password change. Also use when the Hub form says "Not a hub setup file".
---

# Make the hub setup file (oc-hub.json)

The wizard's Remote access step and the **Remote hub** card have a **Load from file** button. It reads
`oc-hub.json` on the phone, fills the six fields, and the owner taps **Save** (`POST /api/hub`).
The format is `tools/make_hub_file.py` (`"format": "oc-hub/1"` + the `/api/hub` keys; `--lora` adds
`"lora": true`: Save also turns the LoRa switch on; `--ttn-cluster HOST` adds `"ttn": {"cluster", "key"}`
for the remote app's **Set up LoRa → Load from file**, the key from `OC_HUB_TTN_KEY`).

## Make it

The values come from the owner's `~/.env` (`OC_HUB_VAN`, `OC_HUB_VAN_USER`, `OC_HUB_VAN_PASS`,
`OC_HUB_APP_USER`, `OC_HUB_APP_PASS`); the broker URL is `--url` or `OC_HUB_URL`
(`docs/howto-remote-hub.md` §1: `wss://<id>.ala.<region>.emqxsl.com:8084/mqtt`).

Pipe it straight to where it is needed, so the passwords never touch the local disk or the chat:

```bash
set -a; . ~/.env; set +a
python3 -m tools.make_hub_file --url "$URL" --lora \
  | ssh wuerfel 'umask 077; cat > ~/Library/Mobile\ Documents/com~apple~CloudDocs/Downloads/oc-hub.json'
```

For the LoRa part, first make a short-lived full-rights **application** key with the owner's
`TTN_API_KEY` (a user key: never put that one in the file, the app deletes the key it gets):

```bash
exp=$(date -u -d '+2 days' +%FT%TZ)
OC_HUB_TTN_KEY=$(curl -s -H "Authorization: Bearer $TTN_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"name\":\"oc-setup-file\",\"rights\":[\"RIGHT_APPLICATION_ALL\"],\"expires_at\":\"$exp\"}" \
  "$TTN_API_URL/api/v3/applications/<app>/api-keys" | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
export OC_HUB_TTN_KEY
python3 -m tools.make_hub_file --url "$URL" --lora --ttn-cluster "${TTN_API_URL#https://}" | ssh wuerfel '…'
```

Check by key names only, never print the file: `python3 -c "import json,sys; print(sorted(json.load(open(sys.argv[1]))))" <file>`.

## Rules

- With `--ttn-cluster` it also holds a full-rights TTN key: it expires by itself, and the app deletes it
  after the setup. If the setup is not done, delete it in the TTN console (name `oc-setup-file`).
- The file holds the **van** password, which no phone keeps. Tell the owner to delete it once the
  form says *Saved* (iCloud Drive: also from "Recently Deleted").
- Never cat, echo or screenshot the file; never commit it; never write it into the repo.
- A broker password change (rekey, `howto-remote-hub` Recovery) makes an old file wrong: make a new one.
- The firmware refuses `*` in any value and a URL without `wss://`; the tool refuses them first.
