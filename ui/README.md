# VW CaliforniaOnTour GUI spec (`ui/screens/*.yaml`)

Machine-usable reconstruction of the CaliforniaOnTour Android app's screens,
for building a replica GUI. Extracted from the decompiled APK (JADX output),
cross-referenced against `protocol/dictionary.yaml` (BLE field dictionary)
and `docs/business-logic/*.md` (reverse-engineered per-feature business
logic: exact enums, setter methods, full-packet-resend rules, and — new in
this pass — the per-feature `Installed`-bit visibility mechanism in
`feature-availability.md`).

Decompiled source root used throughout:
`.../scratchpad/decompile/src/sources` (see individual YAML file headers for
exact file:line citations).

## How this maps to the rest of the repo

- **`protocol/dictionary.yaml`** — the BLE wire-protocol dictionary (services,
  control/state fields, bit offsets/widths). Every `function:` value in a
  screen YAML (e.g. `roofaircondition`, `satelliteantenna`, `stairs`,
  `campingmode`) is a key in that file's `functions:` map; every
  `controls: {field: X, action: Y}` names a field from that function's
  `control_fields`/`state_fields` list.
- **`docs/business-logic/*.md`** — deep reverse-engineering notes (exact
  enum values, setter method names/line numbers, full-packet-resend
  semantics, the `Installed`-bit-gates-visibility mechanism). Screen YAMLs
  cite these docs by section/line rather than repeating their content —
  treat the docs as authoritative for *protocol behavior*, the YAMLs as
  authoritative for *GUI shape/layout*.
- **`ui/assets/svg/*.svg`** — every icon referenced by `icons:` in a screen
  YAML has (in almost all cases) a matching converted SVG here; see
  `ui/assets/README.md` for the extraction/conversion notes and a
  by-category icon index.

## Screen inventory

Every `NavigationXxx$YyyScreen` nav-route class found under `com/californiaontour/**/graphs/`
(the authoritative screen-enumeration signal — far more reliable than the
`*Page`-suffixed local composable/string-key names, which turned out to be
internal per-file naming, not one-per-screen). Each nav-route class itself is
an empty `@Serializable` marker object (no composable body — the actual UI
lives in R8-obfuscated lambda classes not resolvable by name); all content
below was reconstructed from composeResources string-key harvests plus the
business-logic docs.

### This agent's scope: roof / roof-air-condition / satellite / stairs / camping-mode + app shell

| Nav-route class | Graph | Covered by |
|---|---|---|
| `RoofScreen` | NavigationConnectivity | `roof.yaml` |
| `RoofAirConditionScreen` | NavigationConnectivity | `roof-air-condition.yaml` |
| `SatelliteAntennaScreen` | NavigationConnectivity | `satellite-antenna.yaml` |
| `StairsScreen` | NavigationConnectivity | `stairs.yaml` |
| `CampingModeScreen` | NavigationConnectivity | `camping-mode.yaml` |
| `RoofConsoleLightSwitchScreen` | NavigationConnectivity | **lighting.yaml** (roof-console light settings, not this agent's — `lighting_roofConsole_*` keys) |
| `Vehicle`, `VehicleInfo` (graph roots) | NavigationConnectivity | `home.yaml` |
| `VehicleDataScreen`, `VehicleDataMenuScreen` | NavigationConnectivity | `vehicle-info.yaml` |
| `HowToAndInfoScreen` | NavigationConnectivity | `vehicle-info.yaml` |
| `LevelIndicatorScreen` | NavigationConnectivity | `vehicle-info.yaml` (confirmed NOT its own BLE char — derived from the always-on General/0x1004 handshake, see `docs/business-logic/feature-availability.md` §2d) |
| `HeatingAndCoolingTimerScreen` | NavigationConnectivity | `vehicle-info.yaml` (shared entry point; owned jointly with airheater/cooler/roofaircondition) |
| `NotificationCenterScreen` | NavigationConnectivity | `notifications.yaml` |
| `SelfCheckListScreen`, `SelfCheckScreen`, `SelfCheckExitScreen` | NavigationSelfCheck | `self-check.yaml` |
| `AccountScreen`, `MyProfileScreen`, `MyCaliforniaScreen`, `FaqScreen`, `ContactScreen`, `HelpContactScreen` | NavigationAccount | `account.yaml` |
| `UnitOfDistanceScreen`, `DataPrivacyScreen`, `DataPrivacyAnalyticsScreen`, `DataPrivacyGoogleMapsScreen`, `NotificationSettingsScreen`, `ConnectivitySettingsScreen` | NavigationAccount | `settings.yaml` |
| (pairing wizard — no dedicated nav-route class found; reconstructed from `vinFlow_*`/`bluetoothReset_*`/`wifiReset_*`/`setUpRemoteControl_*` string keys) | — | `connectivity.yaml` |
| `WelcomeScreen`, `LocalizationSelection`, `LegalDocument`, `TermsAndConditions`, `AnalyticsScreen`, `MyLocation`, `MyVehicle`, `AppTourScreen`, `Onboarding` | NavigationOnboarding | `onboarding.yaml` (also covers migration/no-internet/force-update interstitials — no dedicated nav-route class found for those three either) |

**Known gap**: `LivingRoomHeaterScreen` (NavigationConnectivity) has **no
YAML file** at the time of writing. It's the combined air+water heater
feature (dictionary function `livingroomheater`, service `0x2100`, shares
its control-model *class* `gg/a.java` with SatelliteAntenna via a different
constructor — see `satellite-antenna.yaml` header note). Per the task split
this belongs to the "airHeater/heater" agent (who produced `air-heater.yaml`
but explicitly excluded `livingRoomHeaterPage_*`/`HeaterPage_*` keys as
out-of-scope for that file) — nobody has claimed it. Business logic is
fully documented in `docs/business-logic/climate-stairs.md` ("LivingRoomHeater"
section) if/when someone writes `living-room-heater.yaml`.

### Other agents' scope (for cross-reference; not re-authored here)

| Nav-route class | Graph | Covered by |
|---|---|---|
| `CoolerScreen` | NavigationConnectivity | `coolbox.yaml` |
| `AirHeaterScreen` | NavigationConnectivity | `air-heater.yaml` |
| `LightingScreen`, `LightingDoorSettingsScreen`, `LightingDoubleTapScreen`, `LightingWakeUpLightScreen`, `ResetLightFactorySettingsScreen`, `RoofConsoleLightSwitchScreen` | NavigationConnectivity | `lighting.yaml` |
| — (Energy has no dedicated nav-route class; surfaced as a dashboard tile + `infoPage_*` detail screen) | NavigationConnectivity | `energy.yaml` |
| — (Water likewise has no dedicated nav-route class; dashboard tiles only) | NavigationConnectivity | `water.yaml` |

### Out of scope for this vehicle-control replica (other app tabs, not part of any agent's task)

These exist in the same APK but belong to campsite-discovery/trip-planning/
account-adjacent features unrelated to vehicle BLE control — listed here
only so the inventory is complete, not modeled as YAML:

| Nav-route class | Graph |
|---|---|
| `DiscoverScreen`, `ThemesOverviewScreen`, `ThemeDetailScreen`, `MarketplaceScreen`, `ProductDetailScreen`, `MagazineScreen` | NavigationDiscover |
| `AllTripsScreen`, `TripDetailsScreen`, `TravelScreen`, `CollectionDetailsScreen` | NavigationTravel |
| `SearchScreen` | NavigationSearch |
| `AiAssistScreen` | NavigationAiAssist |
| `CameraLaunchScreen` | NavigationCamera |
| `AccountModule`, `AiAssistanceModule`, `CameraModule`, `DashboardModule`, `DeveloperModule`, `DiscoverModule`, `OnboardingModule`, `SearchModule`, `SelfCheckModule`, `TravelModule`, `VehicleModule` | NavigationModule (top-level graph-root markers, one per module; `DeveloperModule` in particular is an internal debug menu) |

## Navigation structure

```
App root
├─ Onboarding flow (first run only)     — onboarding.yaml
│   Welcome → Region(Localization) → Legal/Terms → Analytics opt-in
│   → [Migration / No-Internet / Force-Update interstitials, as needed]
│   → Final "welcome to California" → AppTour (5-slide carousel, one per
│     bottom-tab-bar destination below) → MyLocation → MyVehicle
│     (→ hands off into connectivity.yaml's VIN add-vehicle flow)
│
└─ Bottom tab bar (5 tabs — common_tabBar_{discover,plan,travel,vehicle,account}_button,
  │  confirmed at ga/c.java:123 string-registry accessor; the same 5 names
  │  recur as this task's app-tour slides: appTour_{discover,plan,travel,vehicle,account}Tab_*)
  │
  ├─ Discover tab       — out of scope (campsite/POI discovery, travel-guide content)
  ├─ Plan tab           — out of scope (not resolved further; distinct from Travel per
  │                        separate common_tabBar_plan_button / appTour_planTab_* keys)
  ├─ Travel tab         — out of scope (AllTrips / TripDetails / Collections)
  │
  ├─ Vehicle tab  →  home.yaml  (dashboard)
  │   ├─ connectHeader / firstTimeDrawer / vehicleNotEnrolledTile → connectivity.yaml
  │   │     (BLE pairing, VIN add-vehicle, bluetooth/wifi reset)
  │   ├─ overviewWidget (battery/water summary tiles) → energy.yaml / water.yaml detail screens
  │   ├─ remoteControlWidget (quick-access grid, 8 tiles + section header;
  │   │     vehiclePage_remoteControlWidget_* keys, x9/q.java) →
  │   │       air-heater.yaml, coolbox.yaml, roof-air-condition.yaml ("heatingAndCooling"),
  │   │       water.yaml ("hotWater"), lighting.yaml, camping-mode.yaml, stairs.yaml ("step")
  │   │     NOTE: roof.yaml and satellite-antenna.yaml have NO tile in this specific
  │   │     grid (confirmed by exhaustive string-key search) — reachability path from
  │   │     the dashboard is UNCONFIRMED, most likely vehicle-info.yaml's fuller
  │   │     function/spec list. Per-tile *visibility* (independent of that open
  │   │     question) is data-driven per docs/business-logic/feature-availability.md:
  │   │     each feature has its own 1-bit "Installed" flag gating whether its
  │   │     screen/tile renders — NOT vehicle model/trim. Camping Mode is the one
  │   │     exception: its Installed bit is decoded but never wired up, so it always
  │   │     shows (see camping-mode.yaml notes).
  │   ├─ selfCheckWidget → self-check.yaml
  │   └─ helpWidget → vehicle-info.yaml (how-to videos, vehicle spec list, level
  │         indicator, firmware/VIN "General" handshake data)
  │
  └─ Account tab  →  account.yaml  (main menu: login, profile, myCalifornia,
      add-vehicle, bluetooth activity, notification settings, data privacy,
      unit of distance, help & contact)
      ├─ settings.yaml (Unit of Distance / Data Privacy(+Analytics/+GoogleMaps) /
      │     Notification Settings / Connectivity Settings — all live under
      │     NavigationAccount despite feeling like a separate "Settings" app;
      │     there is no separate top-level Settings module in this codebase)
      ├─ account-my-california (vehicle management: VIN, equipment, delete
      │     vehicle, bluetooth/wifi reset, setup remote control) → connectivity.yaml
      └─ FAQ / Contact / Help&Contact / feedback rating drawer
```

Notification bell / in-app alerts feed (`notifications.yaml`,
`NotificationCenterScreen`) is reachable from somewhere in the Vehicle tab
shell (toolbar bell icon, most likely) but no `vehiclePage_*` key directly
references it, so its exact entry point widget is unconfirmed.

## Representation notes

- Schema is the one shared across all agents' files (see any YAML for the
  exact shape): `screen`, `title_key`, `function`, `widgets[]` (each with
  `id`/`type`/`label_key`/`range`/`controls`/`elements`/`constraints`),
  `icons[]`, `notes`. A demo/fixture exercising every widget `type` lives at
  `ui/screens/_demo.yaml` (used by `ui/build_prototype.py` if present) —
  safe to ignore for content purposes.
- `label_key: null` means no dedicated composeResources string key could be
  found for that specific widget (either it reuses a sibling key at
  runtime, or the label is resolved dynamically — several enum-paired
  widgets in `roof-air-condition.yaml` work this way, see that file's notes).
- `function: null` means the screen doesn't correspond 1:1 to a
  `protocol/dictionary.yaml` function (app-shell/navigation screens, or a
  screen whose control model exists but isn't yet a named dictionary entry
  — e.g. `roof.yaml`, which maps to `dictionary.yaml`'s
  `control_models_unresolved` list instead of a named function).
- Every `constraints:` field that states a concrete fact (enum values,
  setter method names, bit indices, gating behavior) is now cross-checked
  against the relevant `docs/business-logic/*.md` file and cites it by
  section/line; anything still genuinely unknown is marked `UNVERIFIED`
  inline, matching that doc set's own convention.
