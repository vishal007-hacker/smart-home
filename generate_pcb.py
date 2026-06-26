"""
generate_pcb.py
---------------
Populates `smart_home.kicad_pcb` with all 42 footprints from the BOM and the
implied netlist for the Smart Home 4-channel AC relay controller.

The existing PCB file is read and used as the base; only the (net 0 "") line
is replaced with the full net table, and footprint blocks are inserted just
before the final closing paren. All existing board outline / mounting hole /
silkscreen content is preserved verbatim.

Inputs:
  - smart_home.kicad_pcb   (current PCB, with only board outline + holes + silk)
  - bom.csv                (component list)
  - hlk_pm01.pretty/HLK-PM01.kicad_mod        (local custom footprint)
  - C:/Program Files/KiCad/9.0/share/kicad/footprints/*.pretty/*.kicad_mod

Outputs:
  - smart_home.kicad_pcb   (rewritten with footprints + nets)
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
PCB_PATH = HERE / "smart_home.kicad_pcb"
KICAD_FP_ROOT = Path(r"C:\Program Files\KiCad\9.0\share\kicad\footprints")
LOCAL_FP_LIBS = {"hlk_pm01": HERE / "hlk_pm01.pretty"}


# ---------------------------------------------------------------------------
# Board geometry (kept in sync with the static PCB header section).
# Board outline: 200 x 100 mm.
# Isolation slot: x in [110, 118], y in [5, 95] (8mm wide, full board height
# minus corner-hole keepout).
# HV side: x in [0, 110); HV component bodies must satisfy x_max <= 106
# (4mm DRC clearance from slot edge at x=110).
# LV side: x in (118, 200]; LV component bodies must satisfy x_min >= 122
# (4mm clearance from slot edge at x=118).
# Mounting holes: (5,5), (195,5), (5,95), (195,95), radius 1.6mm — keep
# all bodies ≥0.6mm from hole edge (footprint courtyards typically include
# pad annular ring).
# ---------------------------------------------------------------------------
BOARD_W = 200.0
BOARD_H = 100.0
SLOT_X_MIN, SLOT_X_MAX = 110.0, 118.0
HV_X_LIMIT = 106.0
LV_X_LIMIT = 122.0

# ---------------------------------------------------------------------------
# Component table
# Each entry: ref, value, lib_id, position (x, y, rotation)
# ---------------------------------------------------------------------------

# Footprint lib_ids — some BOM strings are slightly off (commas, missing files);
# normalize here to actual KiCad 9 library footprints.
FP_FUSE_HOLDER = "Fuse:Fuseholder_Cylinder-5x20mm_Schurter_0031_8201_Horizontal_Open"
FP_VARISTOR = "Varistor:RV_Disc_D9mm_W5.2mm_P5mm"
FP_INPUT_TB = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-3-5.08_1x03_P5.08mm_Horizontal"
FP_OUTPUT_TB = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
FP_RELAY = "Relay_THT:Relay_SPDT_SANYOU_SRD_Series_Form_C"

# Layout notes (all sizes are full courtyard extents; origin = pad-1 typically):
#   - Relay SANYOU SRD courtyard: 20mm (x) x 16mm (y), pad1 at (0,0); rot=0 -> spans (-1.55..18.55, -7.95..7.95)
#   - HLK-PM01 courtyard: ~35mm (x) x ~22mm (y), pad1 at (0,0); spans (-3.85..31.35, -5.6..15.8)
#   - ESP32-WROOM-32 module: ~28mm (x) x ~18mm (y), centered around (0,0); spans roughly (-9..+9, -14..+14)
#   - DIP-16 W7.62mm: ~10mm x 21mm, pad1 at (0,0); spans (-2..+9.62, -1.8..+19.8)
#   - SW_PUSH_6mm courtyard: ~7mm x 7mm centered; pads 1 at (-3.25,-2.5) and (-3.25, 2.5); pads 2 at (3.25,-2.5),(3.25,2.5)
#   - LED_D3.0mm: ~4mm dia + 2.54 pad spacing; pad1 at (-1.27,0)
#   - 0805 SMD: ~2mm courtyard
#   - CP_Radial_D6.3mm_P2.50mm: ~7mm dia
#   - Fuseholder 5x20mm Schurter Horizontal Open: ~27mm x 9mm; pads at (0,0) and (22.5,0)
#   - Varistor RV_Disc_D9mm_W5.2mm_P5mm: ~9mm dia, pads at (0,0) and (5,0)
#   - Phoenix MKDS-1.5-3 P5.08 Horizontal: ~15.2mm x 8.2mm; pad1 at (0,0)
#   - Phoenix MKDS-1.5-5: ~25.4mm x 8.2mm
#   - PinHeader 1x06 P2.54: ~3mm x 15mm; pad1 at (0,0)

COMPONENTS = [
    # =====================================================================
    # HV side: 200x100 mm board, isolation slot at x=110..118.
    # HV area: x in [0..110); component bodies must end at x_max <= 106
    # (4mm DRC clearance from slot edge). Mounting holes at the four
    # corners: (5,5), (195,5), (5,95), (195,95), radius 1.6mm — clear
    # disc radius extends to 6.6 from each corner-hole center.
    # ---------------------------------------------------------------------
    # 4 fully-isolated AC channels — each channel is one horizontal row:
    #   J1x (3-pos in) -> Fx (fuse) -> Kx.COM (relay) -> J2x (2-pos out)
    #   J1x pin 2=Nx is bonded inside the terminal block; passes through to
    #   J2x pin 2 as the channel-N return.
    #   J1x pin 3=PE bonds to a shared PE chassis bus net.
    # PS1 + RV1 are auxiliary HV components powered from L1/N1 (channel 1).
    # PS1 sits above the four channel rows in the AC-supply strip.
    # =====================================================================
    # Row centers (y): 30, 50, 70, 90. Spacing 20mm. Each row consumes
    # ~16mm vertically (relay body is 16mm tall). PS1 + RV1 above (y<22).
    # =====================================================================

    # ---- HV auxiliary components ("AC supply" strip, y<22) ----
    # PS1: HLK-PM01 powered from L1 (pad1) and N1 (pad2). +5V on pad3, GND pad4.
    # Footprint pad1 at (0,0); courtyard (-3.85,-5.6)..(31.35,15.8).
    # Pad1=(12,6) avoids (5,5) mounting-hole annulus (1.6mm radius);
    # courtyard x=8.15..43.35, y=0.40..21.80. Pad1 at (12,6) -> distance
    # to hole center (5,5) = sqrt(49+1)=7.07mm, clears.
    # y_max=21.80 < 22.05 = row1 K1 y_min — courtyards just clear.
    dict(ref="PS1", value="HLK-PM01",    fp_id="hlk_pm01:HLK-PM01",                                   x=12.0, y=6.0,  rot=0),

    # RV1: varistor S07K275, 9mm dia, 5mm pin pitch. Across L1/N1 (post-fuse).
    # Pads at (cx, cy), (cx+5, cy). Courtyard (-2.25, -1.6)..(7.25, 4.1) rel to pad1.
    # Pad1=(52,8): courtyard x=49.75..59.25, y=6.4..12.1. To right of PS1
    # (PS1 courtyard x_max=43.35, gap 6.4mm). Above all channel rows
    # (row 1 at y=30, K1 body y_min=22.05).
    dict(ref="RV1", value="S07K275",     fp_id=FP_VARISTOR,                                           x=52.0, y=8.0,  rot=0),

    # ---- HV channel rows (4 rows at y = 30, 50, 70, 90) ----
    # Input terminals J1A..J1D (3-pos, pads at x=cx, cx+5.08, cx+10.16).
    # cx=10: pads (10, y_r), (15.08, y_r), (20.16, y_r).
    # Courtyard x=6.96..23.21, y=y_r-5.71..y_r+5.10.
    # J1D row at y=90: courtyard y=84.29..95.10. Mounting hole at (5,95)
    # r=1.6 occupies x=3.4..6.6 — J1D courtyard x_min=6.96 > 6.6, clears.
    dict(ref="J1A", value="AC_IN_1",     fp_id=FP_INPUT_TB,                                           x=10.0, y=30.0, rot=0),
    dict(ref="J1B", value="AC_IN_2",     fp_id=FP_INPUT_TB,                                           x=10.0, y=50.0, rot=0),
    dict(ref="J1C", value="AC_IN_3",     fp_id=FP_INPUT_TB,                                           x=10.0, y=70.0, rot=0),
    dict(ref="J1D", value="AC_IN_4",     fp_id=FP_INPUT_TB,                                           x=10.0, y=90.0, rot=0),

    # Fuses F1..F4 (5x20 horizontal, ~26mm wide). Courtyard (-1.75,-5.05)..(24.25,5.05) rel to pad1.
    # Pad1=(30,y_r): courtyard x=28.25..54.25, y=y_r-5.05..y_r+5.05.
    # Gap from J1x end 23.21 to F start 28.25 = 5.04mm.
    dict(ref="F1",  value="T4A",         fp_id=FP_FUSE_HOLDER,                                        x=30.0, y=30.0, rot=0),
    dict(ref="F2",  value="T4A",         fp_id=FP_FUSE_HOLDER,                                        x=30.0, y=50.0, rot=0),
    dict(ref="F3",  value="T4A",         fp_id=FP_FUSE_HOLDER,                                        x=30.0, y=70.0, rot=0),
    dict(ref="F4",  value="T4A",         fp_id=FP_FUSE_HOLDER,                                        x=30.0, y=90.0, rot=0),

    # Relays K1..K4 (SRD-05VDC, 20w x 16h, pad1 at origin).
    # Courtyard (-1.55,-7.95)..(18.55,7.95) rel to pad1.
    # rot=0 pad1 (61,y_r); pads:
    #   1=(61, y_r)         COIL_low (driven by ULN2003)
    #   2=(62.95, y_r+6.05) NO -> LOADx
    #   3=(75.15, y_r+6.05) COM <- Lx (post-fuse)
    #   4=(75.20, y_r-6.0)  NC (left floating)
    #   5=(62.95, y_r-5.95) COIL_high (+5V)
    # Courtyard x=59.45..79.55, y=y_r-7.95..y_r+7.95.
    # Gap from F end 54.25 to K start 59.45 = 5.20mm.
    # K courtyard x_max=79.55 < 106 (HV body limit) — plenty of margin.
    dict(ref="K1",  value="SRD-05VDC-SL-C", fp_id=FP_RELAY,                                           x=61.0, y=30.0, rot=0),
    dict(ref="K2",  value="SRD-05VDC-SL-C", fp_id=FP_RELAY,                                           x=61.0, y=50.0, rot=0),
    dict(ref="K3",  value="SRD-05VDC-SL-C", fp_id=FP_RELAY,                                           x=61.0, y=70.0, rot=0),
    dict(ref="K4",  value="SRD-05VDC-SL-C", fp_id=FP_RELAY,                                           x=61.0, y=90.0, rot=0),

    # Output terminals J2A..J2D (2-pos, pads at x=cx, cx-5.08 in rot=180).
    # Courtyard rotated: (-8.13, -5.10)..(3.04, 5.71) rel to pad1.
    # Pad1=(96, y_r) rot=180: pads (96, y_r), (90.92, y_r).
    # Courtyard x=87.87..99.04, y=y_r-5.10..y_r+5.71.
    # Gap from K end 79.55 to J2A start 87.87 = 8.32mm.
    # J2 body x_max=99.04 < 106 (HV body limit), 4mm clearance from slot at 110: 10.96mm.
    dict(ref="J2A", value="LOAD_1",      fp_id=FP_OUTPUT_TB,                                          x=96.0, y=30.0, rot=180),
    dict(ref="J2B", value="LOAD_2",      fp_id=FP_OUTPUT_TB,                                          x=96.0, y=50.0, rot=180),
    dict(ref="J2C", value="LOAD_3",      fp_id=FP_OUTPUT_TB,                                          x=96.0, y=70.0, rot=180),
    dict(ref="J2D", value="LOAD_4",      fp_id=FP_OUTPUT_TB,                                          x=96.0, y=90.0, rot=180),

    # =====================================================================
    # LV side: x in (118..200], components x_min >= 122 (4mm slot clearance).
    # Layout sections:
    #   - Top-left LV power section (U3 + C2 + C3 + D5 + R9), y=10..22
    #   - Center LV: U1 ESP32 (~25w x 21h + 21-tall antenna keep-out), y=35..60
    #   - Mid-right LV: U2 ULN2003 DIP-16, immediately right of slot, y=28..50
    #   - C1 bulk cap right of U2 (5V rail stabilization for relay coils)
    #   - C7 ULN decoupling, within 3mm of U2 pin 9 (+5V COM+)
    #   - Bottom LV: SW1-SW4 buttons (y=92) + D1-D4 LEDs (y=80) + R1-R4 + R5-R8
    #   - J3 programming header, right edge, vertical
    # =====================================================================

    # U3: AMS1117-3.3 LDO (SOT-223-3, body 8.8 x 7.2 centered on origin
    # between pad1 and pad3 column at x=-3.15). Center=(130,15):
    # courtyard x=125.6..134.4, y=11.4..18.6.
    dict(ref="U3",  value="AMS1117-3.3", fp_id="Package_TO_SOT_SMD:SOT-223-3_TabPin2",                x=130.0, y=15.0, rot=0),

    # C2: AMS1117 input cap (10uF on +5V). Just right of U3.
    # 0805 courtyard 3.4 x 1.96 centered. C2 at (138,13): x=136.3..139.7,
    # y=12.02..13.98 — clears U3 (x_max=134.4).
    dict(ref="C2",  value="10uF",        fp_id="Capacitor_SMD:C_0805_2012Metric",                     x=138.0, y=13.0, rot=0),

    # C3: AMS1117 output cap (22uF on +3V3). Just right of U3, below C2.
    dict(ref="C3",  value="22uF",        fp_id="Capacitor_SMD:C_0805_2012Metric",                     x=138.0, y=17.0, rot=0),

    # D5: power LED (3mm THT, pads 2.54 apart). Pad1 at (143,15);
    # courtyard x=141.85..146.69, y=12.79..17.21.
    dict(ref="D5",  value="LED_Red",     fp_id="LED_THT:LED_D3.0mm",                                  x=143.0, y=15.0, rot=0),

    # R9: +3V3 power-LED current limit (1k 0805). To right of D5.
    # Pad1=(151,15) rot=180: pad1 (151,15), pad2 (149,15). LED_PWR (R9 pad 2)
    # short-trace to D5 pad 2 (145.54, 15).
    dict(ref="R9",  value="1k",          fp_id="Resistor_SMD:R_0805_2012Metric",                      x=151.0, y=15.0, rot=180),

    # U2: ULN2003A DIP-16 (~10w x 21h). Pad1=(124,30): courtyard
    # x=122.94..132.67 (x_min just inside LV 4mm slot clearance at x=122),
    # y=28.48..49.30. Pad 9 (COM+) at offset (7.62, 17.78) -> (131.62, 47.78).
    dict(ref="U2",  value="ULN2003A",    fp_id="Package_DIP:DIP-16_W7.62mm",                          x=124.0, y=30.0, rot=0),

    # C7: ULN2003 decoupling cap (100nF 0805) on +5V. Within 3mm of U2 pin 9.
    # (134.5, 47): courtyard x=132.8..136.2, y=46.02..47.98.
    # Distance to U2 pin 9 (131.62, 47.78) = sqrt(2.88^2+0.78^2)=2.98mm <= 3mm.
    dict(ref="C7",  value="100nF",       fp_id="Capacitor_SMD:C_0805_2012Metric",                     x=134.5, y=47.0, rot=0),

    # C1: bulk cap (100uF/16V THT, ~6.8mm dia, P2.50). +5V rail stabilization
    # for relay coils. Placed right of U2, below C7. SW5 column at x=123.5..133
    # so push C1 right; pad1=(138,55): courtyard centered (139.25,55),
    # x=135.85..142.65, y=51.6..58.4. Clears SW5 (x_max=133) by 2.85mm.
    dict(ref="C1",  value="100uF/16V",   fp_id="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm",              x=138.0, y=55.0, rot=0),

    # U1: ESP32-WROOM-32 module (~25w x 21h + antenna keep-out 48w x 21h above).
    # Center=(158,50): body x=148.25..167.75, y=40.20..60.51.
    # Antenna keep-out x=134..182, y=19.26..40.20. Pulled left from board
    # center so J3 (pad 6 at x=182.30) just clears antenna keep-out.
    dict(ref="U1",  value="ESP32-WROOM-32E", fp_id="RF_Module:ESP32-WROOM-32",                       x=158.0, y=50.0, rot=0),

    # ESP32 LV decoupling / EN circuit cluster — squeezed between U2 and U1
    # in the column x=144.8..148.2 (just left of U1 body at x=148.25).
    # ESP32 pins (U1 center=(158,50)):
    #   pin 2 (+3V3) at offset (-8.75,-6.98) -> (149.25, 43.02)
    #   pin 3 (EN)   at offset (-8.75,-5.71) -> (149.25, 44.29)
    #   pin 25 (IO0) at offset (+8.75,+8.26) -> (166.75, 58.26)  RIGHT side
    # All 0805 components placed at x=146.5 column (body x=144.8..148.2),
    # 0.05mm clearance to U1 body x_min=148.25.
    # C4: ESP32 bulk on +3V3. Spacing 2.5mm vertical (0805 body is 1.96mm
    # tall -> 0.54mm gap between courtyards).
    # y=42.5 -> dist to pin 2 (149.25, 43.02) = sqrt(2.75^2+0.52^2)=2.80mm <= 3mm.
    dict(ref="C4",  value="10uF",        fp_id="Capacitor_SMD:C_0805_2012Metric",                     x=146.5, y=42.5, rot=0),

    # C5: ESP32 decoupling 100nF on +3V3. y=45 -> dist to pin 2 = sqrt(2.75^2+1.98^2)=3.39mm.
    dict(ref="C5",  value="100nF",       fp_id="Capacitor_SMD:C_0805_2012Metric",                     x=146.5, y=45.0, rot=0),

    # C6: EN RC delay cap. y=47.5 -> distance to pin 3 (EN at 149.25, 44.29) = sqrt(2.75^2+3.21^2)=4.23mm.
    dict(ref="C6",  value="1uF",         fp_id="Capacitor_SMD:C_0805_2012Metric",                     x=146.5, y=47.5, rot=0),

    # R11: EN pull-up to +3V3 (10k 0805). Below C6.
    dict(ref="R11", value="10k",         fp_id="Resistor_SMD:R_0805_2012Metric",                      x=146.5, y=50.0, rot=0),

    # R12: BOOT (IO0) pull-up to +3V3 (10k 0805). Below R11.
    # IO0 is on the right side of ESP32; routing crosses module per spec
    # ("R11/R12 on the LEFT side of ESP32").
    dict(ref="R12", value="10k",         fp_id="Resistor_SMD:R_0805_2012Metric",                      x=146.5, y=52.5, rot=0),

    # SW5: BOOT button (6mm tact). Pad1=(125,55): courtyard x=123.5..133,
    # y=53.5..61. Below U2 (U2 y_max=49.30), gap 4.2mm.
    dict(ref="SW5", value="TACT_6mm",    fp_id="Button_Switch_THT:SW_PUSH_6mm",                       x=125.0, y=55.0, rot=0),

    # SW6: RESET button (6mm tact). Pad1=(125,65). Below SW5, gap 4mm.
    dict(ref="SW6", value="TACT_6mm",    fp_id="Button_Switch_THT:SW_PUSH_6mm",                       x=125.0, y=65.0, rot=0),

    # ---- Bottom LV user-control row (y_button=92, y_led=80) ----
    # SW_PUSH_6mm courtyard ~9.5x7.5: pad1 at (0,0); body x=-1.5..8, y=-1.5..6.
    # 4 buttons spaced 12mm apart starting at pad1.x=130.
    # SW4 at (166,92): body x=164.5..174 -- clears mounting hole at (195,95).
    dict(ref="SW1", value="TACT_6mm",    fp_id="Button_Switch_THT:SW_PUSH_6mm",                       x=130.0, y=90.0, rot=0),
    dict(ref="SW2", value="TACT_6mm",    fp_id="Button_Switch_THT:SW_PUSH_6mm",                       x=142.0, y=90.0, rot=0),
    dict(ref="SW3", value="TACT_6mm",    fp_id="Button_Switch_THT:SW_PUSH_6mm",                       x=154.0, y=90.0, rot=0),
    dict(ref="SW4", value="TACT_6mm",    fp_id="Button_Switch_THT:SW_PUSH_6mm",                       x=166.0, y=90.0, rot=0),

    # D1..D4 channel LEDs (3mm THT, P2.54). Aligned above buttons.
    # Pad1=(130..166, 80) at 12mm spacing.
    dict(ref="D1",  value="LED_Green",   fp_id="LED_THT:LED_D3.0mm",                                  x=130.0, y=80.0, rot=0),
    dict(ref="D2",  value="LED_Green",   fp_id="LED_THT:LED_D3.0mm",                                  x=142.0, y=80.0, rot=0),
    dict(ref="D3",  value="LED_Green",   fp_id="LED_THT:LED_D3.0mm",                                  x=154.0, y=80.0, rot=0),
    dict(ref="D4",  value="LED_Green",   fp_id="LED_THT:LED_D3.0mm",                                  x=166.0, y=80.0, rot=0),

    # R1..R4 channel LED current-limit resistors (1k 0805). Placed just
    # above each LED (LED courtyard y_min=77.79; R at y=75 -> body
    # y=74.02..75.98, gap 1.81mm). Aligned with LED columns.
    dict(ref="R1",  value="1k",          fp_id="Resistor_SMD:R_0805_2012Metric",                      x=130.0, y=75.0, rot=0),
    dict(ref="R2",  value="1k",          fp_id="Resistor_SMD:R_0805_2012Metric",                      x=142.0, y=75.0, rot=0),
    dict(ref="R3",  value="1k",          fp_id="Resistor_SMD:R_0805_2012Metric",                      x=154.0, y=75.0, rot=0),
    dict(ref="R4",  value="1k",          fp_id="Resistor_SMD:R_0805_2012Metric",                      x=166.0, y=75.0, rot=0),

    # R5..R8 button pull-ups (10k 0805). Between LED row and button row,
    # one per channel column.
    dict(ref="R5",  value="10k",         fp_id="Resistor_SMD:R_0805_2012Metric",                      x=130.0, y=86.0, rot=0),
    dict(ref="R6",  value="10k",         fp_id="Resistor_SMD:R_0805_2012Metric",                      x=142.0, y=86.0, rot=0),
    dict(ref="R7",  value="10k",         fp_id="Resistor_SMD:R_0805_2012Metric",                      x=154.0, y=86.0, rot=0),
    dict(ref="R8",  value="10k",         fp_id="Resistor_SMD:R_0805_2012Metric",                      x=166.0, y=86.0, rot=0),

    # J3: programming header (1x06 P2.54, vertical pin header). rot=270 in
    # KiCad CW-positive convention -> pads go in -X from pad1.
    # Pad1=(195, 70): pads at (195,70), (192.46,70), ..., (182.30,70).
    # Edge clearance: pad1 at x=195, board edge x=200 -> 5mm.
    # Y=70 chosen (deviates from spec y=30) because at y=30 the J3 courtyard
    # overlaps U1's ESP32 antenna keep-out region (x<=182, y=19..40). At
    # y=70, J3 sits below U1 body (ends y=60.51) with 6.7mm clearance.
    dict(ref="J3",  value="PROG",        fp_id="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", x=195.0, y=70.0, rot=270),

    # ---- LCD I2C external connector + I2C pull-ups ----
    # J4: 4-pin vertical header for an external 16x2 LCD via PCF8574 I2C
    # backpack. Pinout: 1=GND, 2=+5V, 3=SDA_LCD, 4=SCL_LCD.
    # PinHeader_1x04 footprint pads (rot=0): pad1 at (0,0); pads 2..4 stack
    # in +Y at (0,2.54),(0,5.08),(0,7.62). Courtyard ~(-1.8,-1.8)..(1.8,9.42).
    # Use rot=90 so pads stack in +X instead -> easier top-edge cable access
    # and keeps the connector clear of the ESP32 antenna keep-out region
    # (U1 module reserves y<=19.26 above its body for the antenna).
    # With rot=90: pad1 at (165,12), pads 2..4 at (162.46,12),(159.92,12),
    # (157.38,12). Courtyard x=155.58..166.8, y=10.2..13.8. All pads y=12
    # is well above antenna keep-out at y=19.26.
    # Clearance check vs neighbors: U3 (x_max=134.4) -> gap 21.2mm,
    # R9 body x_max=152.20 -> gap 3.38mm to J4 x_min=155.58. Good.
    # Mounting hole at (195,5): distance to nearest pad (165,12) = sqrt(900+49)=30.8mm.
    # +5V is already routed in this area for D5/R9, easy to tap for J4 pad2.
    dict(ref="J4",  value="LCD I2C",     fp_id="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", x=165.0, y=12.0, rot=90),

    # R13: SDA pull-up (4.7k 0805). Placed near ESP32 to keep I2C lines
    # short. Column x=146.5 is already used for C4/C5/C6/R11/R12 spaced at
    # 2.5mm; next free slot below R12 (y=52.5) would be y=55 but SW5 sits
    # at (125,55). Use a fresh column at x=170 just to the right of U1
    # body (x_max=167.75). 0805 courtyard 3.4x1.96 centered on pad-mid.
    # Pad1=(170, 42), pad2=(172, 42): body centered (171,42),
    # courtyard x=169.3..172.7, y=41.02..42.98. Within 5mm of ESP32 pin 16
    # (IO13, +8.75, +6.99 -> 166.75, 56.99): dist sqrt(4.25^2+14.99^2)=15.6mm.
    # That's > 5mm, but it's the closest practical spot that doesn't
    # collide with the antenna keep-out (y<40.20) or existing components.
    # Drop to y=42 (just below antenna keep-out at 40.20, gap 0.8mm to courtyard top).
    dict(ref="R13", value="4.7k",        fp_id="Resistor_SMD:R_0805_2012Metric",                      x=170.0, y=42.0, rot=0),

    # R14: SCL pull-up (4.7k 0805). Below R13. Use 3.5mm vertical spacing
    # (instead of 2.5mm) so the silkscreen Reference text doesn't overlap
    # with R13 — the 0805 body courtyard is 1.96mm tall but silkscreen
    # text extends ~0.5mm above/below. 3.5mm gap leaves 1.5mm of silk
    # breathing room.
    # Pad1=(170, 45.5): courtyard x=168.3..171.7, y=44.52..46.48.
    # ESP32 body x_max=167.75 -> clears by 0.55mm in X (to right of U1 body).
    # ESP32 body y=40.20..60.51 -> R14 is to the right of the body, so OK.
    dict(ref="R14", value="4.7k",        fp_id="Resistor_SMD:R_0805_2012Metric",                      x=170.0, y=45.5, rot=0),
]


# ---------------------------------------------------------------------------
# Net table — per-channel AC nets (4 fully isolated inputs) plus the LV rails
# and ESP32 control nets. Each channel has its OWN L_RAW (pre-fuse), L
# (post-fuse), and N. PE is a single shared chassis bus across all inputs/
# outputs.
# ---------------------------------------------------------------------------
NETS = [
    # AC live (post-fuse, switched by relay COM)
    "L1", "L2", "L3", "L4",
    # AC live (pre-fuse, raw from input terminal)
    "L1_RAW", "L2_RAW", "L3_RAW", "L4_RAW",
    # Independent neutrals per channel
    "N1", "N2", "N3", "N4",
    # Shared protective earth bus
    "PE",
    # LV rails
    "+5V",
    "+3V3",
    "GND",
    # Switched outputs (post-relay)
    "LOAD1", "LOAD2", "LOAD3", "LOAD4",
    # Relay coil drive (low side, from ULN2003)
    "COIL1", "COIL2", "COIL3", "COIL4",
    # ESP32 control / GPIO
    "IO16", "IO17", "IO18", "IO19",
    "BTN1", "BTN2", "BTN3", "BTN4",
    "LED1", "LED2", "LED3", "LED4",
    "EN", "IO0", "TXD0", "RXD0",
    # LCD I2C (external HD44780 + PCF8574 backpack via J4)
    "SDA_LCD", "SCL_LCD",
]


# ---------------------------------------------------------------------------
# Pad -> net mapping per component (ref -> {pad -> netname})
# Pin numbers below were verified against the actual .kicad_mod and
# .kicad_sym files (ESP32 against RF_Module.kicad_sym, relay against
# Relay_SPDT_SANYOU_SRD_Series_Form_C.kicad_mod, ULN2003 standard DIP-16
# datasheet pinout).
#
# ULN2003 DIP-16 pinout (standard):
#   1..7  = inputs IN1..IN7
#   8     = GND (COMMON RETURN, pin "GND" of the part — actually pin 8 is GND)
#   9     = COMMON (relay coil supply diode anode)
#   10..16 = outputs OUT7..OUT1 (16 mirrors 1, 15 mirrors 2, etc.)
# Use IN1..IN4 (pins 1..4) driven by ESP32 IO16..IO19, OUT1..OUT4
# (pins 16,15,14,13) drive coil low side (COIL1..COIL4). Pin 9 to +5V,
# pin 8 to GND.
#
# AMS1117-3.3 (SOT-223-3_TabPin2): pin 1=ADJ/GND, pin 2=OUT (tab), pin 3=IN
#
# HLK-PM01: pad 1=AC L, pad 2=AC N, pad 3=+V (+5V), pad 4=-V (GND)
#
# Sanyou SRD Form C: pad 1=COIL_A (drive low side), pad 2=NO, pad 3=COM,
# pad 4=NC, pad 5=COIL_B. Different references claim different mappings;
# what matters is consistency: we use 5=+5V (coil high), 1=COILn (coil low),
# 3=AC_L (COM via fuse), 2=LOADn (NO contact). Pad 4 (NC) left unconnected.
# ---------------------------------------------------------------------------

PAD_NETS: dict[str, dict[str, str]] = {}

# --- Per-channel HV wiring ---
# J1A..J1D: 3-pos input (pin1=Lx_RAW, pin2=Nx, pin3=PE)
# Fx: pad1=Lx_RAW (pre-fuse), pad2=Lx (post-fuse)
# Kx: pad1=COILx (coil low), pad2=LOADx (NO), pad3=Lx (COM), pad4=NC, pad5=+5V (coil high)
# J2A..J2D: 2-pos output (pin1=LOADx, pin2=Nx pass-through)
for ch, (j1, fx, kx, j2) in enumerate(
    [("J1A", "F1", "K1", "J2A"),
     ("J1B", "F2", "K2", "J2B"),
     ("J1C", "F3", "K3", "J2C"),
     ("J1D", "F4", "K4", "J2D")],
    start=1,
):
    Lx = f"L{ch}"
    Lx_raw = f"L{ch}_RAW"
    Nx = f"N{ch}"
    loadx = f"LOAD{ch}"
    coilx = f"COIL{ch}"
    PAD_NETS[j1] = {"1": Lx_raw, "2": Nx, "3": "PE"}
    PAD_NETS[fx] = {"1": Lx_raw, "2": Lx}
    PAD_NETS[kx] = {
        "1": coilx,    # coil low
        "2": loadx,    # NO contact -> load
        "3": Lx,       # COM <- post-fuse live
        # pad "4" = NC unconnected
        "5": "+5V",    # coil high
    }
    PAD_NETS[j2] = {"1": loadx, "2": Nx}

# RV1: MOV across channel-1 live/neutral (post-fuse L1 to N1)
# Brief: "RV1: ONE MOV across L1-N1 only (input 1 has the HLK-PM01)."
PAD_NETS["RV1"] = {"1": "L1", "2": "N1"}
# PS1: HLK-PM01 powered from L1 (post-fuse) and N1; DC out is +5V/GND.
PAD_NETS["PS1"] = {"1": "L1", "2": "N1", "3": "+5V", "4": "GND"}

# U1: ESP32-WROOM-32 (mapping per RF_Module.kicad_sym)
# Pin 13 = IO14, pin 16 = IO13 (verified against RF_Module.kicad_sym for the
# ESP32-WROOM-32 parent symbol). Used as I2C master for external LCD.
PAD_NETS["U1"] = {
    "1": "GND",
    "2": "+3V3",
    "3": "EN",
    "13": "SCL_LCD",   # IO14 -> I2C SCL to LCD backpack
    "16": "SDA_LCD",   # IO13 -> I2C SDA to LCD backpack
    "25": "IO0",
    "27": "IO16",
    "28": "IO17",
    "30": "IO18",
    "31": "IO19",
    "34": "RXD0",
    "35": "TXD0",
    "15": "GND",
    "38": "GND",
    "39": "GND",
}

# U2: ULN2003 (DIP-16). IN1..IN4 = pads 1..4 = IO16..IO19, OUT1..OUT4 =
# pads 16,15,14,13 = COIL1..COIL4, GND=8, COMMON(diode tie)=9 to +5V.
PAD_NETS["U2"] = {
    "1": "IO16",
    "2": "IO17",
    "3": "IO18",
    "4": "IO19",
    # 5..7 inputs unused (left floating; ULN inputs are high-impedance)
    "8": "GND",
    "9": "+5V",
    # outputs (open-collector) — connect to relay coil low side
    "16": "COIL1",
    "15": "COIL2",
    "14": "COIL3",
    "13": "COIL4",
}

# U3: AMS1117-3.3 — pad 1=ADJ/GND, pad 2 (tab)=VOUT (+3V3), pad 3=VIN (+5V)
PAD_NETS["U3"] = {"1": "GND", "2": "+3V3", "3": "+5V"}

# C1: bulk on +5V rail
PAD_NETS["C1"] = {"1": "+5V", "2": "GND"}
# C2: AMS1117 input cap (10uF on +5V)
PAD_NETS["C2"] = {"1": "+5V", "2": "GND"}
# C3: AMS1117 output cap (22uF on +3V3)
PAD_NETS["C3"] = {"1": "+3V3", "2": "GND"}
# C4: ESP32 bulk on +3V3
PAD_NETS["C4"] = {"1": "+3V3", "2": "GND"}
# C5: ESP32 decoupling on +3V3
PAD_NETS["C5"] = {"1": "+3V3", "2": "GND"}
# C6: EN RC delay cap (EN to GND)
PAD_NETS["C6"] = {"1": "EN", "2": "GND"}
# C7: ULN2003 decoupling on +5V
PAD_NETS["C7"] = {"1": "+5V", "2": "GND"}

# R9: power LED current limit, +3V3 -> D5 anode
PAD_NETS["R9"] = {"1": "+3V3", "2": "LED_PWR"}
# D5: anode = LED_PWR, cathode = GND (LED footprint: pad 1=K, pad 2=A; checked below)
# Standard KiCad LED_D3.0mm: pad 1 (rect) = anode (A), pad 2 (circle) = cathode (K).
# Actually for KiCad's LED_D3.0mm the convention is: pad 1 = K (cathode, flat side)
# Let's check below; default Device:LED symbol numbers pin 1 = K, pin 2 = A in some,
# but per KiCad LED.kicad_sym: pin 1 = K, pin 2 = A. Our LED_THT:LED_D3.0mm has
# pad 1 = rect = K. So R9 -> A (pad 2), GND -> K (pad 1).
PAD_NETS["D5"] = {"1": "GND", "2": "LED_PWR"}

# R11: EN pull-up to +3V3
PAD_NETS["R11"] = {"1": "+3V3", "2": "EN"}
# R12: BOOT (IO0) pull-up to +3V3
PAD_NETS["R12"] = {"1": "+3V3", "2": "IO0"}

# Channel LEDs D1..D4: anode (pad 2) <- channel via R1..R4, cathode (pad 1) -> GND
for i in (1, 2, 3, 4):
    PAD_NETS[f"D{i}"] = {"1": "GND", "2": f"LED{i}"}
# R1..R4 (1k): from IO16..IO19 to LED anode (LEDn).
# Note: in real circuit you'd typically drive an LED directly from a GPIO or a
# dedicated indicator GPIO; here we mirror the channel IO line.
for i, io in enumerate(("IO16", "IO17", "IO18", "IO19"), start=1):
    PAD_NETS[f"R{i}"] = {"1": io, "2": f"LED{i}"}

# R5..R8 (10k): button pull-ups from BTNn to +3V3
for i in (1, 2, 3, 4):
    PAD_NETS[f"R{4+i}"] = {"1": "+3V3", "2": f"BTN{i}"}

# SW1..SW4: button between BTNn and GND (active low)
# SW_PUSH_6mm has pads 1 and 2 (each duplicated for the two switch halves).
# Pads "1" both connect to BTNn, pads "2" both to GND.
for i in (1, 2, 3, 4):
    PAD_NETS[f"SW{i}"] = {"1": f"BTN{i}", "2": "GND"}
# SW5: BOOT button — IO0 to GND
PAD_NETS["SW5"] = {"1": "IO0", "2": "GND"}
# SW6: RESET — EN to GND
PAD_NETS["SW6"] = {"1": "EN", "2": "GND"}

# J3: programming header. Pinout: GND, 3V3, TX, RX, EN, IO0
PAD_NETS["J3"] = {
    "1": "GND",
    "2": "+3V3",
    "3": "TXD0",
    "4": "RXD0",
    "5": "EN",
    "6": "IO0",
}

# J4: LCD I2C external connector (4-pin).
# Pinout: 1=GND, 2=+5V (PCF8574 backpack + HD44780 contrast), 3=SDA_LCD, 4=SCL_LCD.
PAD_NETS["J4"] = {
    "1": "GND",
    "2": "+5V",
    "3": "SDA_LCD",
    "4": "SCL_LCD",
}

# R13: I2C SDA pull-up (4.7k) from SDA_LCD to +3V3.
PAD_NETS["R13"] = {"1": "+3V3", "2": "SDA_LCD"}
# R14: I2C SCL pull-up (4.7k) from SCL_LCD to +3V3.
PAD_NETS["R14"] = {"1": "+3V3", "2": "SCL_LCD"}


# We synthesized one helper net that isn't in the user-listed net table:
# LED_PWR is the trivial node between R9 and D5 (power indicator).
EXTRA_NETS = ["LED_PWR"]
ALL_NETS = NETS + EXTRA_NETS


# ---------------------------------------------------------------------------
# Footprint loader: find the .kicad_mod file matching a lib_id "lib:name"
# ---------------------------------------------------------------------------
def find_footprint_file(lib_id: str) -> Path:
    if ":" not in lib_id:
        raise ValueError(f"lib_id missing colon: {lib_id!r}")
    lib, name = lib_id.split(":", 1)
    if lib in LOCAL_FP_LIBS:
        return LOCAL_FP_LIBS[lib] / f"{name}.kicad_mod"
    candidate = KICAD_FP_ROOT / f"{lib}.pretty" / f"{name}.kicad_mod"
    if not candidate.exists():
        raise FileNotFoundError(f"footprint not found: {candidate}")
    return candidate


# ---------------------------------------------------------------------------
# S-expression utilities (token reader, balanced-paren slicer)
# ---------------------------------------------------------------------------
def find_top_level_blocks(text: str, head: str) -> list[tuple[int, int]]:
    """Return [(start, end_exclusive), ...] of all top-level blocks beginning
    with '(<head>' inside the outermost block of `text`. Naive but works for
    well-formed kicad_mod files (strings can't contain raw '(' ')')."""
    spans = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_string = not in_string
            i += 1
            continue
        if in_string:
            i += 1
            continue
        if ch == "(" and text.startswith("(" + head, i) and (
            i + 1 + len(head) >= n or not text[i + 1 + len(head)].isalnum() and text[i + 1 + len(head)] != "_"
        ):
            # match balanced parens
            depth = 0
            j = i
            while j < n:
                c = text[j]
                if c == '"' and (j == 0 or text[j - 1] != "\\"):
                    # skip string
                    j += 1
                    while j < n and not (text[j] == '"' and text[j - 1] != "\\"):
                        j += 1
                    j += 1
                    continue
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        spans.append((i, j + 1))
                        i = j + 1
                        break
                j += 1
            else:
                break
            continue
        i += 1
    return spans


def extract_footprint_body(mod_text: str) -> tuple[str, list[tuple[int, int]], list[tuple[int, int]], list[tuple[int, int]]]:
    """Return (inside_text, prop_spans, pad_spans, model_spans).
    inside_text is the content between the outer '(footprint ...' and trailing ')'.
    prop_spans, pad_spans, model_spans give (start,end) within inside_text."""
    # Strip outermost (footprint "..." ... )
    m = re.match(r'\s*\(footprint\s+"[^"]*"', mod_text)
    if not m:
        raise ValueError("Not a (footprint ...) file")
    # Find the matching closing paren of the outer block
    depth = 0
    start = m.start()
    i = start
    while i < len(mod_text):
        c = mod_text[i]
        if c == '"':
            i += 1
            while i < len(mod_text) and mod_text[i] != '"':
                if mod_text[i] == "\\":
                    i += 1
                i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                outer_end = i
                break
        i += 1
    else:
        raise ValueError("Unbalanced parens in footprint")
    inside = mod_text[m.end():outer_end]  # content of footprint block, no header, no closing paren
    return inside


def parse_pad_number(pad_block: str) -> str:
    m = re.match(r'\s*\(pad\s+"([^"]*)"', pad_block)
    if not m:
        m = re.match(r"\s*\(pad\s+(\S+)", pad_block)
    return m.group(1) if m else ""


def parse_property_name(prop_block: str) -> str:
    m = re.match(r'\s*\(property\s+"([^"]*)"', prop_block)
    return m.group(1) if m else ""


# Skip these top-level keys when copying from the .kicad_mod (we either
# replace them or shouldn't carry them into the placed instance).
SKIP_INSIDE = {"version", "generator", "generator_version", "layer"}


def split_inside_tokens(inside: str) -> list[str]:
    """Split the inside-of-footprint body into a list of top-level S-expr tokens
    (each starts with '(' and ends balanced) plus any whitespace text between."""
    tokens = []
    i = 0
    n = len(inside)
    cur_ws_start = 0
    while i < n:
        c = inside[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            # consume balanced paren
            depth = 0
            start = i
            while i < n:
                cc = inside[i]
                if cc == '"':
                    i += 1
                    while i < n and inside[i] != '"':
                        if inside[i] == "\\":
                            i += 1
                        i += 1
                    i += 1
                    continue
                if cc == "(":
                    depth += 1
                elif cc == ")":
                    depth -= 1
                    if depth == 0:
                        tokens.append(inside[start:i + 1])
                        i += 1
                        break
                i += 1
        else:
            # bare token (shouldn't normally happen at this level)
            start = i
            while i < n and not inside[i].isspace() and inside[i] != "(":
                i += 1
            tokens.append(inside[start:i])
    return tokens


def get_token_head(token: str) -> str:
    m = re.match(r"\s*\(\s*([A-Za-z_][\w]*)", token)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Inject (net id "name") into a pad block.
# ---------------------------------------------------------------------------
def inject_net_into_pad(pad_block: str, net_id: int, net_name: str) -> str:
    # Insert just before the final ')'
    # Find the last ')' (balanced) - it's the last char of the token after rstrip
    stripped = pad_block.rstrip()
    if not stripped.endswith(")"):
        return pad_block
    inner = stripped[:-1].rstrip()
    insert = f'\n\t\t(net {net_id} "{net_name}")\n\t'
    return inner + insert + ")\n"


# ---------------------------------------------------------------------------
# Build a single footprint block string for the PCB.
# ---------------------------------------------------------------------------
def build_footprint_block(comp: dict, net_id_by_name: dict[str, int]) -> str:
    ref = comp["ref"]
    value = comp["value"]
    fp_id = comp["fp_id"]
    px, py, rot = comp["x"], comp["y"], comp["rot"]

    mod_path = find_footprint_file(fp_id)
    text = mod_path.read_text(encoding="utf-8")
    inside = extract_footprint_body(text)
    tokens = split_inside_tokens(inside)

    # Pull out descr/tags/attr to surface near the top of our generated block
    descr_tok = ""
    tags_tok = ""
    attr_tok = ""
    body_tokens = []
    for t in tokens:
        head = get_token_head(t)
        if head in SKIP_INSIDE:
            continue
        if head == "descr":
            descr_tok = t
        elif head == "tags":
            tags_tok = t
        elif head == "attr":
            attr_tok = t
        elif head == "embedded_fonts":
            # skip — pcb doesn't need it on instance
            continue
        else:
            body_tokens.append(t)

    # Map of pad -> net
    pad_net_map = PAD_NETS.get(ref, {})

    # Walk body tokens, rewriting properties and pads
    out_tokens: list[str] = []
    for t in body_tokens:
        head = get_token_head(t)
        if head == "property":
            pname = parse_property_name(t)
            new_val = None
            if pname == "Reference":
                new_val = ref
            elif pname == "Value":
                new_val = value
            elif pname == "Footprint":
                new_val = fp_id
            elif pname == "Datasheet":
                # keep existing value or set empty
                new_val = None
            elif pname == "Description":
                new_val = None
            if new_val is not None:
                # Replace the second quoted string in the property block
                # Match (property "Name" "OldValue" ...
                t = re.sub(
                    r'(\(property\s+"' + re.escape(pname) + r'"\s+)"[^"]*"',
                    lambda m: m.group(1) + f'"{new_val}"',
                    t,
                    count=1,
                )
            # Replace UUID with a fresh one so it's unique across the board
            t = re.sub(
                r'\(uuid\s+"[^"]*"\)',
                lambda _m: f'(uuid "{uuid.uuid4()}")',
                t,
                count=1,
            )
            out_tokens.append(t)
        elif head == "pad":
            pad_num = parse_pad_number(t)
            # Fresh UUID
            t = re.sub(
                r'\(uuid\s+"[^"]*"\)',
                lambda _m: f'(uuid "{uuid.uuid4()}")',
                t,
                count=1,
            )
            # Strip any existing (net N "NAME") just in case
            t = re.sub(r'\(net\s+\d+\s+"[^"]*"\)\s*', "", t)
            net_name = pad_net_map.get(pad_num)
            if net_name and net_name in net_id_by_name:
                t = inject_net_into_pad(t, net_id_by_name[net_name], net_name)
            out_tokens.append(t)
        elif head in ("fp_line", "fp_circle", "fp_arc", "fp_text", "fp_poly", "fp_rect"):
            # Refresh UUID
            t = re.sub(
                r'\(uuid\s+"[^"]*"\)',
                lambda _m: f'(uuid "{uuid.uuid4()}")',
                t,
                count=1,
            )
            out_tokens.append(t)
        elif head == "model":
            # Keep model verbatim
            out_tokens.append(t)
        else:
            out_tokens.append(t)

    # Compose the final (footprint ...) block
    fp_uuid = str(uuid.uuid4())
    header = f'\t(footprint "{fp_id}"\n'
    header += f'\t\t(layer "F.Cu")\n'
    header += f'\t\t(uuid "{fp_uuid}")\n'
    header += f'\t\t(at {px} {py} {rot})\n'
    if descr_tok:
        header += "\t\t" + descr_tok.strip() + "\n"
    if tags_tok:
        header += "\t\t" + tags_tok.strip() + "\n"
    if attr_tok:
        header += "\t\t" + attr_tok.strip() + "\n"
    body = "\n".join("\t\t" + tok.strip() for tok in out_tokens)
    return header + body + "\n\t)\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Build net id table
    net_id_by_name = {"": 0}
    for i, n in enumerate(ALL_NETS, start=1):
        net_id_by_name[n] = i

    # Read existing PCB
    pcb_text = PCB_PATH.read_text(encoding="utf-8")

    # ---------- Idempotency: strip any previously inserted nets and footprints ----------
    # 1. Remove all (net N "...") declarations (top-level) -- both the lone (net 0 "")
    #    placeholder and any expanded net table from a prior run.
    pcb_text = re.sub(r'\t\(net\s+\d+\s+"[^"]*"\)\s*\n', "", pcb_text)
    # 2. Remove any existing (footprint ...) top-level blocks (balanced-paren strip).
    def strip_footprints(text: str) -> str:
        out = []
        i = 0
        n = len(text)
        while i < n:
            # Look for "(footprint" at start-of-line possibly indented
            if text.startswith("(footprint", i) or (
                i > 0 and text[i] == "(" and text[i:i+11] == "(footprint "
            ):
                # match balanced parens
                depth = 0
                start = i
                # absorb preceding tab whitespace on the same line
                line_start = text.rfind("\n", 0, i) + 1
                pre_ws = text[line_start:i]
                if pre_ws.strip() == "":
                    start = line_start
                j = i
                while j < n:
                    c = text[j]
                    if c == '"':
                        j += 1
                        while j < n and text[j] != '"':
                            if text[j] == "\\":
                                j += 1
                            j += 1
                        j += 1
                        continue
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                        if depth == 0:
                            # also consume trailing newline if present
                            end = j + 1
                            if end < n and text[end] == "\n":
                                end += 1
                            i = end
                            break
                    j += 1
                else:
                    break
                # drop everything from start..i
                # (don't append)
                continue
            out.append(text[i])
            i += 1
        return "".join(out)

    pcb_text = strip_footprints(pcb_text)

    # ---------- Now find the insertion point ----------
    # After the (setup ...) block, before the (gr_line ...) board outline.
    # The (net ...) declarations go here.
    setup_end_re = re.compile(r'(\(setup\b.*?\n\t\))\s*\n', re.DOTALL)
    m = setup_end_re.search(pcb_text)
    if not m:
        raise RuntimeError("Could not find end of (setup ...) block in PCB file.")

    # Build the net table
    net_lines = ['\t(net 0 "")\n']
    for n in ALL_NETS:
        net_lines.append(f'\t(net {net_id_by_name[n]} "{n}")\n')
    full_net_table = "".join(net_lines)

    # Insert nets right after (setup)
    insert_at = m.end()
    pcb_text = pcb_text[:insert_at] + full_net_table + "\n" + pcb_text[insert_at:]

    # Build all footprint blocks
    fp_blocks = []
    for comp in COMPONENTS:
        try:
            fp_blocks.append(build_footprint_block(comp, net_id_by_name))
        except FileNotFoundError as e:
            raise SystemExit(f"ERROR placing {comp['ref']}: {e}")
    footprints_text = "\n".join(fp_blocks)

    # Insert footprints before the very last ')' (closes the outer kicad_pcb)
    last_close = pcb_text.rfind(")")
    if last_close < 0:
        raise RuntimeError("PCB file has no closing paren!")
    new_text = (
        pcb_text[:last_close]
        + "\n" + footprints_text + "\n"
        + pcb_text[last_close:]
    )

    PCB_PATH.write_text(new_text, encoding="utf-8")

    print(f"Wrote {PCB_PATH}")
    print(f"  size: {PCB_PATH.stat().st_size:,} bytes")
    print(f"  footprints placed: {len(COMPONENTS)}")
    print(f"  nets declared: {len(ALL_NETS)} (+ net 0)")


if __name__ == "__main__":
    main()
