# How to set up the remote hub (broker, bridge, app)

The **remote hub** lets the Open California remote app show your van and switch a few things from
anywhere. With WiFi at the van, the satellite talks to an MQTT broker, and the app talks to the same
broker. Without WiFi, the satellite's LoRa uplinks go through The Things Network (TTN) and a small
Cloudflare Worker (the **bridge**) into the broker, and the app sends a few commands over LoRa
directly to TTN. This page builds all of it from zero, with hosted services only. It is also the
operations page: inventory, secrets, checks and recovery.

Every setup step has one action and a **Check** that proves it. Do not go on when a check fails.
Anything that this page could not confirm from the vendor's documentation is marked **check in the
console**: the console or the documentation may name or place it differently today.

Read first: [How to read your van from anywhere with LoRa](howto-lora-remote-setup.md) (TTN
account, the app on the home screen) and [How to put the ESP32 satellite on your
WiFi](howto-esp-wifi-setup.md).

## How it fits together

![System context: the phone app talks to the EMQX broker over sealed MQTT; the satellite reaches the broker over the van's WiFi, or sends LoRa uplinks through TTN and the Cloudflare Worker, which seals them for the broker; LoRa commands go from the app through TTN to the satellite; the data key moves once through the hub QR](assets/remote-hub-context.svg)

Blue is the internet path: the satellite and the app exchange sealed frames through the broker, end to end. Amber is the LoRa path: uplinks go through TTN to the Worker, which seals them for the broker; the four LoRa commands go from the app to TTN and down to the satellite. Green is the key exchange: the data key moves once, through the hub QR on the satellite's screen. The same links as a flowchart:

```mermaid
flowchart LR
    ESP["Satellite (ESP)"] ---|"WiFi: sealed MQTT frames (wss), both ways"| BROKER[("EMQX broker")]
    APP["Remote app (phone)"] ---|"sealed MQTT frames (wss), both ways"| BROKER
    ESP -->|"LoRa uplink"| TTN["The Things Network"]
    TTN -->|"webhook, X-OC-Secret"| BRIDGE["Cloudflare Worker (bridge)"]
    BRIDGE -->|"uplink sealed with the data key"| BROKER
    APP -->|"LoRa command, FPort 20 (command key)"| TTN
    TTN -->|"downlink"| ESP
```

- **Topics** (`<van>` is the van name): `oc/<van>/online` (presence, last will), `oc/<van>/state`
  (retained), `oc/<van>/req`, `oc/<van>/res`, `oc/<van>/lora/<fport>` (the bridge's sealed uplinks).
- **Sealing:** every WiFi frame is AES-128-GCM with the van's **data key**: `ver | dir | nonce(12) |
  ciphertext | tag(16)`, nonce = `dir | random(7) | counter(4)`. The data key is made on the
  satellite and travels only in the hub QR. The broker carries ciphertext.
- **Over the hub** only `GET /api/*` and `POST /api/command` work. Every write to the settings
  answers `403 local_only`: **settings change only in the van's WiFi.**
- **LoRa commands** (heater on or off, camping mode on or off) are 15 bytes on FPort 20, sealed by
  the app, sent directly to TTN with the command key of the LoRa setup. The app allows at most 8 per
  rolling 24 hours. It waits 30 seconds for a WiFi answer before it uses LoRa.

**Who can read what.** LoRa uplinks are **not** sealed on the air: LoRaWAN encryption only, so TTN
can read the values. The bridge seals them with the data key before the broker, and the data key is a
Cloudflare secret. So **TTN and Cloudflare can read the LoRa uplink values; the broker cannot.** The
WiFi path is end-to-end between the satellite and your phones: the broker and Cloudflare see only
ciphertext. Not protected: when and how much the van sends, and a compromised phone.

**Why EMQX.** HiveMQ Cloud Serverless reaches end of life on 2026-12-31 and accepts no new clusters
(docs.hivemq.com, "Serverless end of life"). EMQX Cloud Serverless is the hosted broker that this
page uses.

## What you need

- An EMQX Cloud account (`https://cloud-intl.emqx.com`), a Cloudflare account, a TTN or TTS account
  with the application of your van, and a GitHub account that can create repositories in the
  `open-california` organization.
- A computer with `git`, `node` and `npm`, the GitHub CLI `gh` (logged in) and `ssh-keygen`.
- The satellite with the firmware that has the hub (a new board: [first
  flash](howto-esp-first-flash.md)), a phone, and the van's WiFi.
- Choose a **van name** now: letters, digits, `_` and `-` only. The examples use `van1`. It is one
  topic level, `oc/van1/...`.
- **No `*` in any value that goes into the hub QR:** the van name, the broker URL, the app user and the
  app password. The QR uses `*` as its separator and the satellite refuses such a value. The van
  user and password are not in the QR, but keep `*` out of them as well. `openssl rand -hex 16`
  makes safe passwords.
- A place for secrets that you control: your password manager or a file only you can read. The
  steps call it **your secret store**. (The owner's own is `~/.env`, managed with `chezmoi edit ~/.env`.)

Do not paste a secret into a chat, a file in the repository or a shell command line. The steps below
read secrets from a prompt or a pipe.

## Where each key goes

Every key of the hub, the service where it is made, every place it must be put into, and where
you keep a copy. "Your secret store" is the encrypted place for your private values (the owner
uses `~/.env`, edited with `chezmoi edit ~/.env`). Never type a value into a command line, a chat,
a file in this repository or a log.

| Key | Made in | Put into | Copy kept in |
| --- | --- | --- | --- |
| EMQX login `van` (user, password) | EMQX console, **Access Control > Authentication** (step 1.6) | the satellite's **Remote hub** card, fields *van user* and *van password* (step 5.2) | your secret store |
| EMQX login `app` (user, password) | EMQX console, step 1.6 | the satellite's **Remote hub** card, fields *app user* and *app password* (step 5.2); from there the hub QR carries it to every phone | your secret store |
| EMQX login `bridge` (user, password) | EMQX console, step 1.6 | Cloudflare Worker secrets `BROKER_USER` and `BROKER_PASS` (`npx wrangler secret put`, steps 2.9 and 2.10) | your secret store |
| Topic permissions (ACLs) | EMQX console, **Access Control > Client Authorization** (step 1.7) | nothing else: they stay in EMQX | not secret |
| `WEBHOOK_SECRET` | your computer: `openssl rand -hex 24` (step 2.7) | Cloudflare Worker secret `WEBHOOK_SECRET` (step 2.8) **and** the TTN webhook header `X-OC-Secret` (step 3.3) | your secret store |
| `HUB_KEY` (the data key) | the satellite, at the first save of the Hub card | the hub QR, scanned by every phone (step 5.4); Cloudflare Worker secret `HUB_KEY`, read from the QR with `zbarcam` (step 5.7) | no copy: the satellite holds it and the hub QR shows it again; **New data key** in the Hub card makes a new one |
| Bridge settings `VAN`, `BROKER_WSS`, `DEV_ID` | your choice; `DEV_ID` is the van's TTN device ID | `bridge/wrangler.toml` under `[vars]` (step 2.4) | the repository: they are not secret |
| `REMOTE_APP_DEPLOY_KEY` (SSH key pair) | your computer, `ssh-keygen` in a temporary folder (step 4.3) | private half: GitHub secret of `ckeller42/open-california` (`gh secret set`); public half: deploy key with write access on the Pages repository | no copy: the temporary folder is deleted |
| `OC_HUB_ORIGIN` | the broker URL of step 1.5 without the path | GitHub variable of `ckeller42/open-california` (`gh variable set`, step 4.4) | not secret |
| Cloudflare access for `wrangler` | `npx wrangler login` in the browser (step 2.3), or an API token from the Cloudflare dashboard, **My Profile > API Tokens**, template *Edit Cloudflare Workers* | your computer only: `wrangler` keeps the login, or reads the token from `CLOUDFLARE_API_TOKEN` | your secret store (the token) |
| EMQX platform API key (optional) | EMQX console, **Settings > Platform API Key > New API Key** | your computer only, as `EMQX_API_KEY` and `EMQX_API_SECRET`, for automation; the setup does not need it | your secret store |
| EMQX deployment API key (optional) | EMQX console, the deployment's **API Key** page (**check in the console**; the Platform API cannot make one for Serverless) | your computer only, as `EMQX_DEPLOY_APP_ID` and `EMQX_DEPLOY_APP_SECRET`, to create logins and permissions through `https://<host>:8443/api/v5` | your secret store |
| TTN full-rights API key | TTN console, the application's **API keys**, *Grant all current and future rights* | the remote app, **Set up LoRa**, once; the app deletes it after the setup | none |
| TTN read key and command key | the remote app, during the LoRa setup | the phone; the app sends them to the satellite over LoRa (FPort 12, sealed with the transfer key) | the phone and the satellite only |
| LoRaWAN AppKey and transfer key | the satellite, when its LoRa screen opens | the setup QR; from there the app registers the AppKey at TTN | the satellite and TTN |

### The infrastructure setup in one picture

Sections 1 to 4 below, with the keys that move between the services. Steps 5.2 to 5.7 then bring
the keys into the satellite, the phones and the Worker (see [The van setup in one picture](#the-van-setup-in-one-picture)).

```mermaid
sequenceDiagram
actor O as Owner
participant S as Your secret store
participant E as EMQX console
participant C as Cloudflare (wrangler)
participant T as TTN console
participant G as GitHub
O->>E: Serverless deployment, spend limit 0
E-->>O: host, wss URL :8084/mqtt
O->>E: logins van, app, bridge
O->>S: keep the three passwords
O->>E: topic permissions on oc/‹van›/
O->>C: wrangler login
O->>C: wrangler.toml: VAN, BROKER_WSS, DEV_ID
O->>S: WEBHOOK_SECRET (openssl rand)
S->>C: secret put WEBHOOK_SECRET
S->>C: secret put BROKER_USER, BROKER_PASS (bridge login)
O->>C: wrangler deploy
C-->>O: Worker URL /ttn
O->>T: webhook: Worker URL, header X-OC-Secret = WEBHOOK_SECRET
O->>G: Pages repo, deploy key (public half)
O->>G: secret REMOTE_APP_DEPLOY_KEY (private half)
O->>G: variable OC_HUB_ORIGIN = wss origin
O->>G: tag remote-v*
G-->>O: app on GitHub Pages
Note over O,C: HUB_KEY comes later, from the satellite's hub QR (step 5.7)
```

## Setup from zero

### 1. The broker: EMQX Cloud Serverless

1. Open the EMQX Cloud console (`https://cloud-intl.emqx.com`) and log in. Create a project if there is
   none.
2. Click **New Deployment** and choose the **Serverless** plan.
   **Check:** the form asks for a monthly spend limit.
3. Set the **spend limit to 0** and give the deployment a name. With 0, only the free quota
   applies and the deployment stops when it is used up (see [Inventory](#inventory)). Accept the
   terms and deploy.
   **Check:** the status becomes **Running** (a few minutes).
4. Open the deployment's **Overview**. Write down the **address** (the host name). Ports 8883
   (MQTT over TLS) and 8084 (WebSocket over TLS) are on by default.
   **Check:** the Overview shows the address and both ports.
5. Build the WebSocket URL: `wss://<address>:8084/mqtt`. The path `/mqtt` is the usual EMQX
   default: **check in the console** (the Overview or the connection guide). You need this URL
   three times: in the satellite's Hub card, in the bridge's `BROKER_WSS` and (without the path) as
   the GitHub variable `OC_HUB_ORIGIN`.
   **Check:** you have the URL written down, with the address, port 8084 and the path.
6. Create **three credentials** under **Access Control > Authentication** (**check in the
   console**). Make long random passwords (`openssl rand -hex 16`, copy them from your own
   terminal into the console; keep them in your secret store, see [Secrets](#secrets)).

   | Credential | Used by | Needs |
   | --- | --- | --- |
   | `van` | the satellite | publish `oc/<van>/state`, `oc/<van>/res`, `oc/<van>/online`; subscribe `oc/<van>/req` |
   | `app` | every phone (it is in the hub QR) | subscribe `oc/<van>/online`, `oc/<van>/state`, `oc/<van>/res`, `oc/<van>/lora/+`; publish `oc/<van>/req` |
   | `bridge` | the Cloudflare Worker | publish `oc/<van>/lora/#` (retained) |

   **Check:** the console lists three users.
7. Add the **topic permissions** (ACLs) under **Access Control > Client Authorization** (**check in
   the console**; the page offers **+ Add** with a user name, a topic, an action
   Publish/Subscribe/Both and Allow/Deny). One rule per row of the table above, for the matching
   user name, with the van name in the topic. Then add a deny-all rule for **All Users** with topic
   `#`, action Publish and Subscribe, permission Deny, so that a user has only what is allowed.
   The documentation does not say whether user-name rules work on Serverless: **check in the
   console.** If they do not, the three credentials share all topics. Then the broker's ACL is not
   a second line of defence: the data stays sealed, but a leaked app password could publish where
   the van or the bridge publishes (availability, not secrecy). Write that in your notes.
   **Check** (after step 9, which introduces the test client): with the test client, logged in as `app`, publish to `oc/<van>/req` works and
   publish to `oc/<van>/state` is refused (ACL working), or note that it is not refused.
8. Retained messages: the satellite publishes `oc/<van>/state` and `oc/<van>/online` retained,
   and the bridge publishes `oc/<van>/lora/*` retained. **Check in the console** that the deployment
   accepts retained messages: publish a retained test message as `van`, subscribe with a new client
   and see it arrive at once.
9. Test the credentials with a WebSocket test client (the console's online test client, **check in the
   console**, or any MQTT client): connect to the URL of step 5 as `app`.
   **Check:** the connection is accepted. With a wrong password it is refused. The broker's certificate
   chains to DigiCert Global Root G2, which the satellite's CA bundle trusts (checked with
   `openssl s_client`).

Optional alternative to the click path of steps 1 to 3: create the deployment with the EMQX Platform
API (`POST {platform}/deployments`, with the platform's base URL from the EMQX API documentation and
the platform API key as credential, read from your secret store, never typed into the command line). The body
holds `platform`, `region`, `connections` 1000, `tps` 1000, `deploymentType` `"serverless"`,
`deploymentVersion` `"v5"`, `projectID` and `freeTrial` `false`. The `201` answer shows
`spec.spendLimit` 0 and `spendLimitAction` `"stop"`: check both before you go on. Serverless exposes
only port 8084 (wss) and 8883 (mqtts), and the host looks like `<id>.ala.<region>.emqxsl.com` (an
example format: take the real host from the answer or the Overview). The deployment API
(`https://<host>:8443/api/v5`) needs a deployment API key, and the Platform API cannot make one for
Serverless: make it in the console. The path `/mqtt` stays **check in the console**.

The EMQX platform API key can automate the console steps. It belongs in your secret store as
`EMQX_API_KEY` and `EMQX_API_SECRET` (names only). This page does not depend on it.

### 2. Cloudflare: the bridge Worker

1. Create a free Cloudflare account (`https://dash.cloudflare.com`).
   **Check:** the dashboard opens.
2. On the computer run `git clone https://github.com/ckeller42/open-california.git`, then
   `cd open-california/bridge` and `npm install`.
   **Check:** `npx wrangler --version` prints a version.
3. Run `npx wrangler login`. A browser window opens: allow it.
   **Check:** `npx wrangler whoami` shows your account.
4. Open `bridge/wrangler.toml` and set `VAN` to your van name, `BROKER_WSS` to the URL of step 1.5
   (with `:8084/mqtt`) and replace the placeholder `SET-ME` of `DEV_ID` with the van's TTN device ID
   (the **End device ID** of the van's device in the TTN console). The bridge forwards only the uplinks
   of this device and ignores every other device of the TTN application (answer `204`). With the
   placeholder left in, the bridge answers `500` to every webhook. These are not secrets.
   **Check:** `git diff` shows only these three values.
5. Run the bridge's tests from the repository root: `tools/ci.sh bridge`.
   **Check:** it ends with all tests passed.
6. Commit the change and push `main` (`git commit -am "bridge: van and broker"` and `git push origin
   main`). If `main` is protected, open a pull request and merge it. The app's publish job
   (step 4.5) accepts only a tag on a commit that is on `origin/main`.
   **Check:** `git fetch && git status` says `main` is up to date with `origin/main`.
7. Make the webhook secret (at least 16 characters): `openssl rand -hex 24`. Save it in your secret
   store, for example as `OC_WEBHOOK_SECRET`. Never type it into a command line.
   **Check:** the secret store holds a value of 48 characters.
8. Store it in the Worker. The command reads the value from the pipe, so it is in no file and no
   shell history (load the value into `$OC_WEBHOOK_SECRET` from your secret store first, in `bridge/`):

   ```sh
   printf %s "$OC_WEBHOOK_SECRET" | npx wrangler secret put WEBHOOK_SECRET
   ```

   **Check:** `npx wrangler secret list` names `WEBHOOK_SECRET`. The first `secret put` may ask to
   create the Worker: **check in the console**.
9. Store the bridge user: `npx wrangler secret put BROKER_USER` and type the `bridge` user of step
   1.6 at the prompt.
   **Check:** `npx wrangler secret list` names `BROKER_USER`.
10. Store the bridge password: `npx wrangler secret put BROKER_PASS` and type the `bridge` password at
    the prompt.
    **Check:** `npx wrangler secret list` names `BROKER_PASS`. The fourth secret, `HUB_KEY`, comes in
    step 5.7: the data key does not exist yet.
11. Run `npx wrangler deploy`.
    **Check:** it prints the Worker URL, `https://oc-bridge.<your-subdomain>.workers.dev`. Then
    `curl -i https://oc-bridge.<your-subdomain>.workers.dev/ttn` answers `405` (the route exists, GET
    is refused).

### 3. TTN: the webhook

1. In the TTN console open your application and choose **Integrations > Webhooks > Add webhook >
   Custom webhook** (**check in the console**).
2. Give it an ID, for example `oc-bridge`. Format: **JSON**. Base URL: the Worker URL from step 2.11.
3. Under **Additional headers** add the header `X-OC-Secret` with the value of your webhook secret.
   Copy it from your secret store with any clipboard tool.
4. Under **Enabled event types** turn on **Uplink message** with the path `/ttn`, and turn **everything
   else off**. The bridge reads uplink messages only. No TTN API key goes to the bridge: LoRa
   commands use the app's own command key.
5. Save.
   **Check:** the webhook shows as healthy in the console (**check in the console**). A first real
   check is the [smoke test](#smoke-test-per-link).

### 4. GitHub: the app on Pages

1. Create the repository `open-california/open-california.github.io` (public) with a first commit on
   `main`, for example a README. The publish job clones branch `main`, so the branch must exist.
   **Check:** `gh repo view open-california/open-california.github.io` works.
2. In that repository open **Settings > Pages** and serve **branch `main`, folder `/`** (**check in
   the GitHub settings**).
3. Make a deploy key that can write to that repository only, and store its private half as the
   secret `REMOTE_APP_DEPLOY_KEY` of `ckeller42/open-california`, without a copy on disk:

   ```sh
   d=$(mktemp -d)
   ssh-keygen -t ed25519 -N '' -C oc-remote-app -f "$d/k"
   gh repo deploy-key add "$d/k.pub" -R open-california/open-california.github.io --allow-write --title remote-app
   gh secret set REMOTE_APP_DEPLOY_KEY -R ckeller42/open-california < "$d/k"
   shred -u "$d/k"; rm -rf "$d"
   ```

   **Check:** `gh secret list -R ckeller42/open-california` names `REMOTE_APP_DEPLOY_KEY`, and
   `gh repo deploy-key list -R open-california/open-california.github.io` shows `remote-app` with
   write access.
4. Set the repository variable with the broker origin (the URL of step 1.5 without the path). The
   build puts it into the page's `connect-src`; without it the page cannot reach the broker:

   ```sh
   gh variable set OC_HUB_ORIGIN -R ckeller42/open-california --body 'wss://<address>:8084'
   ```

   **Check:** `gh variable list -R ckeller42/open-california` shows `OC_HUB_ORIGIN`.
5. Push `main` first if it has commits that `origin/main` lacks (step 2.6 did this for the bridge
   change): the workflow stops with "tag is not on main" for a tag on a commit that is not on
   `origin/main`. Then tag that commit and push the tag (workflow `.github/workflows/remote-app.yml`):

   ```sh
   git tag remote-v1 && git push origin remote-v1
   ```

   **Check:** the run `remote-app` of that tag is green (`gh run list -R ckeller42/open-california`),
   and `https://open-california.github.io/` opens the app. A later release uses `remote-v2` and so on.

### 5. The first van setup on the phone

The first steps cannot use the hub: a fresh satellite is reachable only on its own hotspot. Steps 2
to 5 happen in the setup wizard's optional **Remote access** step (after *Sensors*). Its two parts
are independent: **Over the internet** (the Hub form, then the remote app) and **Over LoRa** (the LoRa
switch). If you skipped the step, the same form is the **Remote hub** card in *Satellite settings*.

On the satellite's screen, each **3-second hold** shows the next screen, always in this order: the
**app QR** (the app's address) → **the van's code** (the hub QR) → the **LoRa code** (with LoRa on:
the setup code before the TTN keys, the repair code after). The app QR and the van's code show only
with a hub config. A tap closes the screen, and so do 120 seconds. Light a dark screen with a tap
first: the hold that wakes the screen does not count.

```mermaid
sequenceDiagram
actor O as Owner
participant P as Phone
participant E as Satellite
participant C as Computer
Note over O,C: Before: broker, Worker, TTN webhook, Pages (sections 1-4), and for LoRa a TTN account
rect rgb(245, 245, 245)
Note over C,E: First flash (howto-esp-first-flash)
C->>E: flash the firmware over USB
end
rect rgb(235, 242, 250)
Note over O,E: Satellite wizard (howto-esp-wifi-setup)
O->>P: scan the hotspot QR, join calictl-esp-XXXX
P->>E: WiFi step, then the second QR
O->>P: Pair the camper unit, Victron, sensors
O->>P: Remote access, over the internet - the Hub form
P->>E: POST /api/hub
E-->>P: Connected - the app link and the install steps
O->>P: Remote access, over LoRa - the LoRa switch on
end
rect rgb(240, 248, 235)
Note over O,E: The remote app (still in Remote access)
O->>E: hold 3 s - the app QR
O->>P: camera, install, icon OC Remote
O->>E: hold 3 s again - the van's code
O->>P: OC Remote, Scan the van's code
O->>E: hold 3 s again - the LoRa code
O->>P: OC Remote, Set up LoRa (TTN key)
end
rect rgb(235, 242, 250)
Note over O,E: Satellite wizard, back in Safari
O->>P: Next - Home - add this page, icon OC Van
O->>P: Done
end
rect rgb(250, 243, 232)
Note over O,C: The bridge
O->>E: show the van's code again
C->>C: zbarcam, wrangler secret put HUB_KEY
end
```

1. **Hotspot, WiFi and pairing.** Follow [How to put the ESP32 satellite on your
   WiFi](howto-esp-wifi-setup.md): the wizard joins the van's WiFi, pairs the camper unit and asks
   for Victron devices and sensors.
   **Check:** the satellite's page shows its status over the WiFi, and the camper unit as paired.
2. **Remote access, over the internet.** In the wizard's **Remote access** step (or later in
   *Satellite settings*, card **Remote hub**) fill in: broker address (the `wss://<address>:8084/mqtt`
   URL), van name, van user, van password, app user and app password (the `van` and `app` credentials
   of step 1.6). Save. The form works only in the van's WiFi, not over the setup hotspot.
   **Check:** the status says **Connected** (also `GET /api/state`: `device.hub` has
   `"configured": true` and `"online": true`). If not, the status says why (address not found,
   secure connection failed, login refused, broker does not answer) and what to check.
3. **Install the app.** When the hub is connected, the step shows the link to the app
   (`device.hub.app_url`, `https://open-california.github.io/`) and how to install it. Or hold the
   satellite's screen for 3 seconds: the **app QR** shows (the plain app address, no secret, with
   three numbered steps beside it). Scan it with the phone's camera, open the page and add it to the
   home screen (iPhone: Safari → *Share* → *Add to Home Screen*; Android: Chrome → ⋮ → *Install app*
   or *Add to Home screen*). Its icon is called **OC Remote**. Do all later scans inside the installed
   app: on iPhone a home-screen app has its own storage, so a code read by Safari never reaches it.
   **Check:** OC Remote opens from its icon on the home screen.
4. **The van's code.** In OC Remote open ⋮ and choose **Scan the van's code**. On the satellite, hold
   the screen for 3 seconds again (from the app QR) until **The van's code** shows, and scan it.
   **Check:** the app shows the van with **WLAN** and a short age.
5. **Remote access, over LoRa.** With a LoRa module, switch on **This satellite has the LoRa module**
   in the same step. Then do the LoRa setup of the [LoRa how-to](howto-lora-remote-setup.md)
   (section 3, **Set up LoRa** in OC Remote, with the full-rights key of your TTN application). With
   the hub set up, the LoRa code is the third screen. Do not hold the satellite's screen between the
   steps that the how-to names.
   **Check:** the app says **The van has the keys.**
6. **Finish the wizard.** Go back to the wizard in Safari and tap **Next**. The **Home** step adds
   the satellite's own page to the home screen as **OC Van** (settings, in the van's WiFi); **Done**
   shows the checklist.
7. **The data key into the bridge.** The bridge needs the data key to seal uplinks. The key is the last
   field of the hub QR text (`OCH1*<van>*<url>*<app user>*<app pass>*<key>`). Read it **without a
   shell history and without a file**: show the van's code again (step 4) and scan it with the
   computer's camera, so that only the key goes into the pipe:

   ```sh
   zbarcam -1 --raw --nodisplay | cut -d'*' -f6 | tr -d '\r\n' | npx wrangler secret put HUB_KEY
   ```

   `zbarcam` is in the package `zbar-tools`; `-1` makes it exit after the first code.
   The value must be exactly 32 hex characters; the bridge answers 500 for anything
   else, a trailing newline included, which is why `tr` strips it. Do not read the QR text in a
   normal phone scanner app: it shows the app password and the key on the screen and in its history.
   **Check:** `npx wrangler secret list` names `HUB_KEY`, and the LoRa smoke test below passes.

## The van setup in one picture

Steps 2 to 4 of section 5, and the first command over the hub.

```mermaid
sequenceDiagram
actor O as Owner
participant P as Phone (remote app)
participant E as Satellite
participant B as EMQX broker
O->>E: wizard Remote access in the van's WiFi: wss URL, van name, van + app login
E->>E: make the data key once (KV hub_key)
E->>B: connect wss :8084 as van
E->>B: publish oc/‹van›/online, sealed oc/‹van›/state (retained)
E-->>O: step says Connected, shows the app link
O->>E: hold the screen 3 s
E-->>O: app QR: https://open-california.github.io/
O->>P: scan it with the camera, install the app, icon OC Remote
O->>E: hold 3 s again
E-->>O: hub QR: OCH1*van*url*app user*app pass*key
O->>P: Scan the van's code
P->>B: connect wss as app, subscribe oc/‹van›/#
B-->>P: retained sealed state
P->>P: open with the data key
P-->>O: van shows WLAN, short age
O->>P: switch heater or camping mode
P->>B: sealed req
B->>E: req
E->>B: sealed res
B-->>P: res, app shows the answer

```

## Smoke test per link

1. **Satellite → broker.** The Hub card says **Connected**. In the EMQX console (or the test client
   logged in as `app`) subscribe to `oc/<van>/#`: `oc/<van>/online` shows `1` and `oc/<van>/state`
   arrives about every 30 s as binary (sealed). Fail: wrong address, wrong `van` credentials (the card
   says **Not connected**) or the broker is stopped (see [Recovery](#recovery)).
2. **TTN → bridge → broker.**
   1. Open `npx wrangler tail` in a terminal (see [Bridge logs](#bridge-logs)).
   2. In the TTN console simulate an uplink on FPort 2 for the van's device (**Messaging >
      Simulate uplink**, **check in the console**), or wait for a real uplink.
   3. Expect a `POST /ttn` with status `204`, and a new retained message on `oc/<van>/lora/2`.
   `401`: the header value differs from `WEBHOOK_SECRET`. `500`: the secret is missing or shorter than
   16 characters, `HUB_KEY` is not 32 hex, or `DEV_ID` is empty or still `SET-ME`. `502`: the broker refused the
   bridge's connection or did not answer in 5 seconds (`bridge` credential, `BROKER_WSS`). `400`: the
   body is not a TTN uplink. `204` but no new message on `oc/<van>/lora/2`: the bridge publishes with
   QoS 0, so the broker does not report a publish that its ACL refuses; check the topic permission of
   the `bridge` credential (step 1.7). The same answer comes for an uplink of a device other than
   `DEV_ID`.
   A check without TTN: `curl -i -X POST <Worker URL>/ttn -H "X-OC-Secret: $OC_WEBHOOK_SECRET" -d '{}'`
   (with the secret in `$OC_WEBHOOK_SECRET`, loaded from your secret store) answers `400`: the secret is right and the body is empty.
3. **App → broker → satellite.** In the app, switch the **air heater** or **camping mode** while the
   phone is on mobile data (not the van's WiFi). The app shows the answer within a few seconds.
   Fail: the app shows "Key changed: scan the hub QR" (rekeyed, scan again) or "No connection to the
   hub" (broker or `app` credential).
4. **LoRa command.** Turn off the satellite's WiFi (or block the broker), switch camping mode in the
   app. After 30 seconds the app sends it over LoRa and says **Sent over LoRa.** The satellite acts
   after its next downlink window (class C, usually seconds). If its camper link rests, the satellite
   wakes it and holds the command for up to 120 seconds after its arrival; the console log then says
   `lora: command ... waits for the camper link`, and at the end `-> 5` (accepted) or `dropped`. Remember the budget of 8 per 24 hours:
   do not repeat this test needlessly.

## Bridge logs

`cd bridge && npx wrangler tail` streams the Worker's requests live: one line per webhook call with
its status. The bridge **logs nothing else** by design: no payload, no key, no error detail. Filter
with `npx wrangler tail --status error` (failed calls), `--format pretty` or `--search 401`
(**check `npx wrangler tail --help`**). `tail` shows only live traffic; there is no history on the free
plan unless you turn on Workers Logs in the dashboard (**check in the console**).

## Inventory

| Service | Where | What it does | Free-tier limit and what happens at the limit |
| --- | --- | --- | --- |
| EMQX Cloud Serverless | `https://cloud-intl.emqx.com`, deployment `<address>`, port 8084 (wss) | the broker | 1 M session minutes and 1 GB traffic a month (EMQX pricing page). With spend limit 0 the deployment stops when the quota is used (**check in the console**); the app then shows no connection and LoRa through the bridge also stops. |
| Cloudflare Worker `oc-bridge` | `https://oc-bridge.<your-subdomain>.workers.dev/ttn` | TTN webhook → sealed MQTT | Workers free plan: a daily request limit (**check in the Cloudflare docs**). Over the limit the Worker answers an error and TTN retries or drops; the van's LoRa values stop reaching the app, WiFi is not affected. |
| TTN / TTS application | TTN console, cluster `eu1` | LoRaWAN network, webhook, command downlinks | Fair use: about 30 s of uplink airtime and 10 downlinks a day per device. The app counts 8 commands per 24 h. |
| GitHub Pages | `https://open-california.github.io/` (repo `open-california/open-california.github.io`) | the app | Soft limits of GitHub Pages. The app opens offline from its cache; no new version until it is back. |
| GitHub Actions | repo `ckeller42/open-california`, workflow `remote-app` | builds and pushes the app on a `remote-v*` tag | Free minutes; a failed run changes nothing on the site. |
| HiveMQ | not used | | Serverless reaches end of life on 2026-12-31. |

There is no self-hosted fallback. If the hosted hub is down, the app says so.

## Secrets

Never write a value into this repository, a chat or a log. Names and places only. Where each key is made and put in: [Where each key goes](#where-each-key-goes).

| Name | Place | What it can do | How to rotate |
| --- | --- | --- | --- |
| `van` user and password | EMQX console; the satellite's flash (Hub card) | publish the van's topics, read `req` | New password in the console, then the Hub card. |
| `app` user and password | EMQX console; the hub QR; every phone | read the van's topics, publish `req` (frames still need the data key) | New password in the console, new password in the Hub card, then every phone scans the hub QR again. |
| `bridge` user and password = `BROKER_USER`, `BROKER_PASS` | EMQX console; Cloudflare secrets | publish `oc/<van>/lora/#` | New password in the console, then `npx wrangler secret put BROKER_PASS`. |
| `WEBHOOK_SECRET` | Cloudflare secret; the TTN webhook header `X-OC-Secret`; your secret store | make the bridge accept an uplink (it can feed the app fake LoRa values; the bridge still seals them) | New value in your secret store, `wrangler secret put WEBHOOK_SECRET`, new header value in the TTN webhook. |
| `HUB_KEY` (the data key) | the satellite's flash; the hub QR; every phone; Cloudflare secret | read the van's WiFi data, send valid commands | [Rekey](#lost-phone-or-leaked-key-rekey). |
| `REMOTE_APP_DEPLOY_KEY` | GitHub secret of `ckeller42/open-california`; the public half is the deploy key of the Pages repo | write to the Pages repository only | Step 4.3 again with a new key, then delete the old deploy key (`gh repo deploy-key delete`). |
| `OC_HUB_ORIGIN` | GitHub variable (not a secret) | the broker origin in the page's `connect-src` | `gh variable set`, then a new tag. |
| `EMQX_API_KEY`, `EMQX_API_SECRET` | your secret store | optional: the EMQX platform API for automation; this page does not need it | New key in the EMQX console. |
| TTN full-rights API key | the phone, once | the app deletes it after the LoRa setup | n/a |
| `EMQX_DEPLOY_APP_ID`, `EMQX_DEPLOY_APP_SECRET` | your secret store | optional: the EMQX deployment API (logins, permissions, clients) | Delete the key in the console and make a new one. |
| `CLOUDFLARE_API_TOKEN` | your secret store | optional instead of `wrangler login`: deploy the Worker and set its secrets | Roll the token in the Cloudflare dashboard (**My Profile > API Tokens**). |
| TTN read key and command key | the phone; the satellite | read the van's uplinks; send the four LoRa commands | [You lost a phone](howto-lora-remote-setup.md#you-lost-a-phone). |
| LoRaWAN AppKey | the satellite; TTN | join the van to TTN | Forget LoRa on the satellite (`lora forget`) and do the LoRa setup again. |

## Deploy from a clean machine

```sh
git clone https://github.com/ckeller42/open-california.git && cd open-california
# bridge (needs node and npm)
tools/ci.sh bridge                       # the bridge's tests, before a deploy
cd bridge && npm install && npx wrangler login
npx wrangler secret list                 # the four secrets must be named
npx wrangler deploy
cd ..
# app (Pages): a tag on a commit that is on main
git tag remote-v2 && git push origin remote-v2
```

A clean machine also needs the secrets in Cloudflare and GitHub, which stay there; a new machine does
not need them again. To build the app by hand: `python3 -m tools.build_remote --out _build/remote
--hub-origin wss://<address>:8084`.

## Recovery

### Lost phone or leaked key (rekey)

A phone with the hub QR can read the van's WiFi data and send commands until the data key changes.

1. Change the `app` password in the EMQX console and in the satellite's Hub card (the old password is
   in the lost phone).
2. In the satellite's **Satellite settings**, card **Remote hub**, tap **New data key** and confirm
   (only in the van's WiFi). The satellite makes a **new data key**; the van's code changes. Old
   phones stop working: the app says "Key changed: scan the hub QR".
3. On the satellite, hold the screen for 3 seconds, and again, until **The van's code** shows.
4. Scan it on every phone you keep (**Scan the van's code**).
5. Put the new key into the bridge (step 5.7 above). **If you skip this, LoRa values stop reaching
   the app**, because the bridge still seals with the old key.
6. For LoRa, follow also [You lost a phone](howto-lora-remote-setup.md#you-lost-a-phone) (new TTN keys).

The LoRa repair QR does **not** carry the hub data: every phone scans the hub QR.

### Rekey in one picture

The steps above, in order.

```mermaid
sequenceDiagram
actor O as Owner
participant B as EMQX console
participant E as Satellite
participant P as Kept phones
participant W as Cloudflare Worker
O->>B: change the app password
O->>E: Hub card: new app password
O->>E: Hub card: New data key, confirm
E->>E: new data key, lora_cmd_ctr = 0
O->>E: hold 3 s, again until the van's code shows
E-->>O: new hub QR
O->>P: Scan the van's code on each kept phone
O->>W: wrangler secret put HUB_KEY (zbarcam from the QR)
Note over P,E: the lost phone now has neither the key nor the login

```

### The free tier is used up

- **Broker (EMQX):** with spend limit 0 the deployment stops. Wait for the next month, or raise the
  limit in the console (this can cost money: **check the prices in the console** first). The
  satellite and the app reconnect when it runs again. Watch the usage in the console.
- **Worker:** wait for the next day, or move to a paid Workers plan.
- **TTN:** the daily airtime is not extendable; commands wait for the next day.

### Lost account or deleted deployment

- **EMQX deployment or account gone:** make a new deployment (step 1). The address changes, so
  change it in the Hub card, in `BROKER_WSS` (`wrangler.toml`, then `wrangler deploy`) and in
  `OC_HUB_ORIGIN` (then a new `remote-v*` tag). New credentials mean a new app password: every phone
  scans the hub QR again after the Hub card is saved. The data key can stay.
- **Cloudflare account gone:** steps 2 and 3 again (the Worker URL changes: change the TTN
  webhook).
- **GitHub repository gone:** step 4 again.
- **TTN account gone:** the LoRa how-to again from section 1.

## Known limits

- LoRa frames carry no frame age (issue #508): values are marked by their uplink time at TTN, not by
  when the van measured them.
- Replay protection after a satellite reboot rests on the 120-second time check, so the satellite
  runs commands over the hub only once it has valid SNTP time. Before that it answers them with
  "The van has no network time yet"; reads still work. It ignores a retained request.
- A LoRa command that the satellite cannot run within 120 seconds of its arrival (its camper link
  does not come up) is dropped. It is also dropped when LoRa is switched off or forgotten, when the
  data key changes, or when a newer command from WiFi runs first. The app has already said
  **Sent over LoRa.**; only the console log shows it.
- The LoRa repair QR (`OCR1`) carries no hub data: every phone scans the hub QR (`OCH1`).
- TTN and Cloudflare can read LoRa uplink values (see above); the broker cannot.
- Settings (WiFi, hub, pairing, firmware update) work only in the van's WiFi.
- Over LoRa only heater and camping mode, on or off, at most 8 commands per rolling 24 hours.
- The free tiers have no service level agreement. EMQX Serverless can stop at its quota.
