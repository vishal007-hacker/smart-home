# Smart Home 4-Channel AC Relay Controller

ESP32-based smart home controller for switching 4 independent 230V AC loads, with per-channel current and voltage sensing, manual override buttons, status LEDs, and an I²C LCD display.

The project is a complete KiCad 9 design — schematic, PCB layout, BOM, custom symbol/footprint for the HLK-PM01 AC-DC module, custom DRC rules for mains clearance, and Python generator scripts that build everything from a single `bom.csv`.

## Features

- **4 independent AC channels**, each with its own L/N/PE input terminal, fuse, relay, and L/N output terminal
- **Mains isolation** — HLK-PM01 isolated AC-DC module (230VAC → 5VDC, 600mA, 2.1 kV isolation)
- **Per-channel current sensing** — ACS712-05B Hall-effect IC (±5A, isolated)
- **Per-channel voltage sensing** — external ZMPT101B modules via I²C ADS1115 (16-bit, 4 channels)
- **Manual override** — 4 tactile buttons for local on/off control per channel
- **Status indication** — 4 green LEDs (one per channel) + 1 red power LED
- **I²C LCD support** — 4-pin header for an external 16×2 PCF8574 backpack LCD
- **UART programming header** — standard 6-pin pinout for any USB-to-serial adapter
- **Custom DRC rules** — 4mm HV-to-LV clearance, 3mm HV-to-HV, 2mm HV trace minimum, edge clearance
- **HV/LV isolation slot** — physical cut in the PCB between mains and logic sections

## Hardware overview

| Section | Parts |
|---|---|
| AC inputs | 4× Phoenix MKDS-1.5 3-pos terminals (L, N, PE per channel) |
| Protection | 4× T4A 5×20mm fuses (one per channel) + 1× S07K275 MOV across L1/N1 |
| Power supply | HLK-PM01 (230VAC → 5VDC) + AMS1117-3.3 (5V → 3.3V LDO) |
| Switching | 4× SANYOU SRD-05VDC SPDT relays (10A) driven by ULN2003A Darlington array |
| MCU | ESP32-WROOM-32E module (WiFi + Bluetooth) |
| AC outputs | 4× Phoenix MKDS-1.5 2-pos terminals (L_switched, N_passthrough per channel) |
| Current sense | 4× ACS712-05B (SOIC-8, ±5A range, internal isolation) |
| Voltage sense | 4× external ZMPT101B modules via 1× ADS1115 (I²C ADC at 0x48) |
| User interface | 4× 6mm tactile buttons + 4× green status LEDs + 1× red power LED + 4-pin I²C LCD header |
| Programming | 6-pin UART header (GND, +3V3, TXD0, RXD0, EN, IO0) |

Full bill of materials in [`bom.csv`](bom.csv) (~43 grouped rows, ~76 component instances).

## Board

| | |
|---|---|
| Size | 200 × 100 mm, 2-layer FR4, 1.6 mm |
| Net classes | Default (0.25mm), Power (0.5mm), AC_HV (2.5mm, 4mm clearance, red) |
| HV/LV separation | Physical slot at x=110..118 mm with 4 mm minimum clearance enforced via custom DRU |
| Mounting | 4× 3.2 mm holes at corners |

## ESP32 GPIO map

| GPIO | Function | Notes |
|---|---|---|
| GPIO16-19 | Relay control K1-K4 | Drives ULN2003A inputs 1-4 |
| GPIO21, 22, 23, 25 | Manual buttons SW1-SW4 | Active-low, 10k pull-up to 3V3 |
| GPIO26, 27, 32, 33 | Status LEDs D1-D4 | Active-high, 1k current limit |
| GPIO13 | I²C SDA | Shared bus: LCD (0x27) + ADS1115 (0x48) |
| GPIO14 | I²C SCL | Same shared bus |
| GPIO34, 35, 36, 39 | Current sense A0-A3 | ADC1 input from ACS712 voltage dividers |
| GPIO0 | BOOT button | SW5 + 10k pull-up |
| EN | RESET button | SW6 + 10k pull-up + 1uF RC delay |
| TXD0 (GPIO1) | UART TX | Programming header J3 pin 3 |
| RXD0 (GPIO3) | UART RX | Programming header J3 pin 4 |

ADS1115 reads voltage sensors V_SENSE_1..4 on its 4 single-ended inputs.

## Repository layout

```
.
├── README.md                       # This file
├── bom.csv                         # Bill of materials (source of truth)
├── smart_home.kicad_pro            # KiCad project file
├── smart_home.kicad_sch            # Schematic
├── smart_home.kicad_pcb            # PCB layout (routed)
├── smart_home.kicad_dru            # Custom DRC rules (HV clearance)
├── fp-lib-table / sym-lib-table    # Registers the local hlk_pm01 lib
├── lib/
│   └── hlk_pm01.kicad_sym          # Custom symbol for HLK-PM01
├── hlk_pm01.pretty/
│   └── HLK-PM01.kicad_mod          # Custom footprint for HLK-PM01
├── generate_sch.py                 # Builds smart_home.kicad_sch from bom.csv
├── generate_pcb.py                 # Builds initial smart_home.kicad_pcb
├── add_acs712.py                   # Adds current-sense ICs to PCB (preserves traces)
├── add_voltage_sense.py            # Adds ADS1115 + voltage-sense headers
└── fabrication/                    # Gerbers + drill + pick-and-place (regenerable)
```

## Building the design

### Regenerate schematic
```
"C:\Program Files\KiCad\9.0\bin\python.exe" generate_sch.py
```

### Regenerate PCB (initial placement only — overwrites routing)
```
"C:\Program Files\KiCad\9.0\bin\python.exe" generate_pcb.py
"C:\Program Files\KiCad\9.0\bin\python.exe" add_acs712.py
"C:\Program Files\KiCad\9.0\bin\python.exe" add_voltage_sense.py
```

### Run DRC / ERC
```
"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe" sch erc smart_home.kicad_sch --output erc.json
"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe" pcb drc smart_home.kicad_pcb --format json --output drc.json
```

### Export fabrication files
```
"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe" pcb export gerbers smart_home.kicad_pcb -o fabrication/ --no-protel-ext
"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe" pcb export drill smart_home.kicad_pcb -o fabrication/ --format excellon --excellon-units mm --generate-map --map-format gerberx2
"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe" pcb export pos smart_home.kicad_pcb -o fabrication/smart_home-pos.csv --format csv --units mm
```

Zip everything in `fabrication/` and upload to [JLCPCB](https://jlcpcb.com), [PCBWay](https://pcbway.com), or [OSHPark](https://oshpark.com).

## Flashing firmware

Wire a USB-to-serial adapter (CP2102, CH340, FT232) to **J3**:

| J3 pin | Adapter |
|---|---|
| 1 — GND | GND |
| 2 — +3V3 | leave disconnected (board self-powered from mains via PS1) |
| 3 — TXD0 | RX |
| 4 — RXD0 | TX |
| 5 — EN | DTR (optional, for auto-reset) |
| 6 — IO0 | RTS (optional, for auto-boot) |

**Safety:** Disconnect mains before plugging in the USB adapter. Then power the board from mains for upload — PS1 supplies 5V/3.3V and is galvanically isolated from mains.

**Manual reset sequence:** hold SW5 (BOOT) → press+release SW6 (RESET) → release SW5 → click Upload in your IDE.

Compatible with Arduino-ESP32, ESP-IDF, MicroPython, PlatformIO.

## External wiring per channel

Each ZMPT101B voltage sensor module is wired externally:

```
AC side (off-PCB):
    J1A pin 1 (L1) ──┐
                     ├── ZMPT101B "AC IN"
    J1A pin 2 (N1) ──┘

DC side (3-wire to PCB):
    ZMPT101B GND ── J5 pin 1
    ZMPT101B VCC ── J5 pin 2 (+3V3)
    ZMPT101B OUT ── J5 pin 3 (→ ADS1115 A0)
```

Repeat for channels 2, 3, 4 via J6, J7, J8 (→ ADS1115 A1, A2, A3).

Calibrate the trimmer pot on each ZMPT101B so its output stays in 0–3.3V range.

## Safety

- The HV side (input terminals, fuses, relays, MOV, PS1 AC input) carries lethal mains voltage. **Always disconnect mains before working on the board.**
- The HV/LV isolation slot in the PCB and the 4 mm HV-to-LV clearance rule provide creepage isolation per IEC 60664-1 for pollution degree 2, working voltage 250V.
- Each input has its own T4A slow-blow fuse (F1–F4). Replace only with the same rating.
- HLK-PM01 provides galvanic isolation between mains and the 5V rail (2.1 kV rated).
- ACS712-05B provides 2.1 kVrms isolation between the load current path and the MCU side.
- Use a properly rated enclosure with strain-relief for mains wiring. Do not power up outside an enclosure.

This is a hobby project. The author assumes no liability for damage, injury, or fire resulting from its use. Build and operate at your own risk and in compliance with local electrical codes.

## License

MIT — see commit history.
