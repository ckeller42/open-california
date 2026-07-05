# VW CaliforniaOnTour icon/illustration extraction

Vector graphics pulled from the VW CaliforniaOnTour Android app APK
(`de.volkswagen.CaliforniaOnTour.apk`, extracted on the `buspi` Raspberry Pi)
for use as reference/replica assets in a future own-vehicle interoperability
GUI. **Not official VW assets** — extracted from a shipped APK for local
reverse-engineering/interop purposes only. Not committed to git per task
instructions.

## Source

- APK path (on `buspi`): `~/apks/de.volkswagen.CaliforniaOnTour.apk`
- Drawables live under `assets/composeResources/<module>.generated.resources/drawable/*.xml`
  (Compose Multiplatform packages its vector drawables as raw XML assets rather
  than compiled `res/drawable*` — the app has no classic Android `res/drawable`
  vector resources, only the composeResources ones).
- **300** drawable XML files found across 13 feature/library modules.
- **257** distinct icon names after de-duplication (many icons are bundled
  identically into more than one Kotlin Multiplatform module — e.g. `alert`,
  `california_remote`, `close`, `manual`, `star_outline` each appear in 2-4
  modules with byte-identical or near-identical content; one copy was kept
  per name).
- 1 of the 257 is not a vector drawable at all: `transparent.xml` is a trivial
  `<shape><solid android:color="#00000000"/></shape>` (a fully transparent
  placeholder drawable, not an icon).
- **256** real vector drawables converted to SVG. **0 failed** conversion.

## Conversion

`vd2svg.py` (stdlib-only Python, no dependencies) converts Android
vector-drawable XML to SVG:

```
python3 vd2svg.py --batch <input_dir> <output_dir>
```

Mapping performed:
- `<vector android:viewportWidth/Height>` → `<svg viewBox="0 0 W H">`
- `<path android:pathData android:fillColor android:fillType>` → `<path d fill fill-rule>`
  (vector-drawable pathData is already SVG-path-compatible)
- `android:fillType="evenOdd"` → `fill-rule="evenodd"`
- `android:strokeColor/strokeWidth/strokeAlpha/strokeLineCap/strokeLineJoin` → matching SVG stroke attributes
- 8-digit `#AARRGGBB` colors → 6-digit `#RRGGBB` + `fill-opacity`/`stroke-opacity`
- `<group>` → `<g>` (rotate/translate/scale supported in the converter, though
  none of these 256 icons actually use group transforms)
- `<clip-path>` inside a group → `<clipPath>` in `<defs>`, referenced via `clip-path="url(#...)"`
  (43 files use this, always as a simple rectangular/shape clip, no transform)
- `<aapt:attr name="android:fillColor"><gradient .../></aapt:attr>` → `<linearGradient>`/`<radialGradient>`
  in `<defs>`, referenced via `fill="url(#...)"` (5 files: the `connection_*`
  illustration graphics use gradients — `connection_success`, `connection_bt_background`,
  `connection_ignition`, `connection_all_models`, `connection_searching`)

All 256 output SVGs were validated as well-formed XML
(`xml.dom.minidom.parse`, 0 failures).

## Failed / not converted

- `transparent.xml` — not a vector drawable (a `<shape>` resource), and not an
  icon (fully transparent 0-alpha fill). Left as-is, not converted. No other
  drawable failed; there were no "too complex to convert" cases requiring
  raw-XML-only fallback.

## Icon inventory by function

Grouping by the originating Kotlin Multiplatform module and by what the name
suggests. Names are exactly the SVG filenames in `ui/assets/svg/`.

### Vehicle remote control (camper-specific functions) — mostly `features.connectivity_ui`
Fridge/cooling, heating, lighting, roof, water, power/energy — the core
"California Multivan remote control" screen icons:
- **Cooling/fridge**: `coolingbox`
- **Heating**: `air_heater`, `air_heater02`, `park_heater_continuous`, `heating_cooling_timer`, `water_heater`, `temperature`, `snowflake`
- **Air distribution** (heater vent routing): `air_distribution_controller_front_on/off`, `air_distribution_controller_rear_on/off`, `air_distribution_controller_mixed_on/off`
- **Lighting**: `lights_on_filled`, `lights_off`, `lights_ambiente_on`, `lights_ambiente_off`, `camping_lights_on`
- **Roof**: `pop_up_roof`, `tiltroof`, `awning`
- **Power/energy**: `vehiclepower`, `vehicle_status_charging`, `vehicle_status_shore_power`, `vehicle_status_solar`, `electric_hookup` (in `libraries.places_core`), `wallbox` (places_core), `usb`, `usb_off`, `flash`, `level` (battery/tank level?)
- **Camping mode / general vehicle state**: `campingmode`, `california_remote` (the main remote-control glyph), `remote`, `cabinet`, `accessories`, `gear`, `dimensions`, `step`, `general_cabe`
- **Connectivity/pairing to the vehicle** (`features.connectivity_ui` + `libraries.connectionmanager.impl`): `bluetooth`, `bluetooth_reset`, `connection_all_models`, `connection_bt_background`, `connection_cabe`, `connection_connecting`, `connection_error`, `connection_ignition`, `connection_searching`, `connection_smartphone`, `connection_success`, `connection_wifi_bt_button`, `connectionerrorcot`, `internet`, `internet_12px`, `wifi`, `wifi_flow_connection_connecting`, `wifi_flow_connection_connecting_02/03`, `wlan_hotspot`, `mobile_reception`, `satellite_antenna`, `alert_red`, `alert_yellow`, `alert_small_red`, `alert_small_yellow`, `alert_vin_flow`
- **VIN / vehicle pairing flow**: `vin_flow_add_vehicle`, `vin_flow_checked`, `vin_flow_read_vin`, `vin_flow_warning`, `new_car_commercial`, `new_car_commercial_dimmed`, `new_car_commercial_filled_blue`, `add_car_commercial`

### Onboarding / auth (`features.onboarding`, `libraries.auth`)
`login`, `login_banner_high_five`, `login_card_high_five`, `couple_california`,
`couple_california_centered`, `account_filled`, `update`, `legal_terms_and_conditions`,
`no_internet`, `no_internet_80`, `map` / `map_filled`, `discovery_filled`,
`vehicle_filled`, `heart` / `heart_filled`, `topographical_lines`

### Account (`features.account.impl`)
`bluetooth_reset`, `california_remote`, `general_cabe`, `customers_center`,
`faq`, `technical_specification`, `checkmark`, `alert_blue`, `notification`,
`statistic`, `bin` (delete account/vehicle), `new_car_commercial`, `wlan_hotspot`

### Dashboard (`features.dashboard`)
`discover_dimmed`, `discover_filled_blue`, `map_dimmed`, `profile_dimmed`,
`profile_filled_blue`, `heart_dimmed`, `heart_filled_blue`,
`new_car_commercial_dimmed`, `manual`, `close` — bottom-nav / tab icons in
dimmed vs. filled/active states.

### AI assistant (`features.ai_assist`)
`cali_chat_icon`, `paper_airplane` (send), `waves` (voice/audio?), `manual`

### Discover / travel / places (`features.discover`, `features.travel`, `libraries.places_core`, `libraries.map`)
Points-of-interest and campsite amenity icons — this is the largest single
group (~90 icons), covering campsite/travel-guide amenity glyphs:
- **Site & stay**: `stellplatz`, `camping_or_caravan_site`, `roadsurfer`, `campinginfo`, `campsite_pin_active`
- **Utilities/hookups**: `freshwater`, `greywater_disposal`, `supply_and_disposal`, `electric_hookup`, `wallbox`, `shower`, `toilet`, `sanitary_facilities`, `laundry_facilities`, `washing_machine`, `tumble_dryer`
- **Food/leisure**: `restaurant`, `bread_service`, `food_and_drinks`, `cooking`, `cooking_and_kitchen`, `campfire_and_grilling`, `wellness`, `culture_and_nightlife`, `outdoor_activities`, `water_activities`, `winter_activities`, `family_and_trips`, `dog_friendly`, `pets`
- **Access/transport**: `highway_access`, `public_transport`, `transport_and_mobility`, `hinterland`, `mobile_reception`, `connectivity_and_media`, `shopping_options`, `vehicle_and_charging_options`
- **Rating/bookmarking**: `star_filled`, `star_outline`, `half_yellow_star`, `yellow_star`, `bookmark`, `bookmark_filled_12`, `bookmark_outlined_12px`, `bookmark_grey_22px_x_32px`, `heart_with_red_circle`, `circle_heart_48px`, `circle_bookmark_48px`, `circle_flag_48px`, `circle_pin_48px`, `flag_with_green_circle`, `visited`
- **Illustrations**: `cta_illustration`, `california_certified`, `bulb_info`, `thumbsup`, `section_header`, `topolines_padding_2`

### Search / filters (`features.search`)
`filters`, `home`, `layers`, `magnifier_12px`, `shape`, `number1`, `number2`, `number3`
(numbered map-pin badges, continued in `libraries.core_ui` as `number4`-`number9`)

### Generic UI chrome (`libraries.core_ui`) — largest module, ~70 icons
Navigation/back/close: `arrow_left`, `arrow_right_12`, `chevron`, `chevron_12px_icon`,
`chevron_right_12`, `chevron_up`, `close`, `close_12`, `close_left_aligned`,
`drag_indicator`
Actions: `add`, `add_12`, `edit`, `edit_12`, `bin`, `copy`, `reload`, `reload_12`,
`remove_minus`, `logout`, `checked`, `checked_filled`, `checked_filled_large`,
`checkmark_12px`, `checkmark_24px`, `circle_checked`, `crossed_filled`,
`green_checked_filled`, `sorting`, `more`, `three_dots`, `group`, `list`,
`stop`, `play`
Info/status: `info`, `info_filled`, `info_symbol`, `alert`, `help`, `bulb_info`,
`resource_false`, `empty_yellow_start`, `successuserhappy`
Contact/comms: `chat`, `mail_12px`, `mail_24px`, `phone_12px`, `phone_24px`,
`external_link_icon`, `external_link_icon_24`
Branding: `california_logo`, `cali_logo_without_box`, `volkswagen_logo_12px`
Misc: `bullet`, `calendar`, `calender_24px`, `clock_12`, `clock_24px`, `privacy`,
`route`, `route_arrow`, `route_with_blue_circle`, `locate`, `locate_12px`,
`navigate_12px`, `magnifier`, `drive_distance_icon`, `map_12px`,
`map_filled_blue`, `profile`, `videos`

## Files

- `ui/assets/vd2svg.py` — the converter (stdlib-only)
- `ui/assets/svg/*.svg` — 256 converted icons/illustrations
- `ui/assets/README.md` — this file

## Not converted / needs manual attention

None beyond `transparent.xml` (see above) — every real vector drawable
converted cleanly, including the 5 gradient-based illustrations and the 43
clip-path-using icons.
