"""
add_voltage_sense.py
--------------------
Append per-channel AC voltage sensing components to an existing routed PCB:
  - 1x ADS1115IDGS TSSOP-10 4-channel 16-bit I2C ADC (U8)
  - 1x 100nF 0805 ADS1115 VDD decoupling cap (C16)
  - 4x 3-pin headers (J5..J8) — external ZMPT101B voltage sensor connectors

Adds 4 new nets:
  - V_SENSE_1..V_SENSE_4   (LV: ZMPT101B module OUT -> ADS1115 AIN0..3)

ADS1115 wiring (using KiCad's ADS1115IDGS symbol pin->function mapping,
which binds to the TSSOP-10 footprint pad numbers 1..10):
  pad 1  ADDR       -> GND       (sets I2C address to 0x48)
  pad 2  ALERT/RDY  -> (no net)  (left floating, no IRQ used)
  pad 3  GND        -> GND
  pad 4  AIN0       -> V_SENSE_1
  pad 5  AIN1       -> V_SENSE_2
  pad 6  AIN2       -> V_SENSE_3
  pad 7  AIN3       -> V_SENSE_4
  pad 8  VDD        -> +3V3
  pad 9  SDA        -> SDA_LCD   (existing I2C bus, shared with LCD)
  pad 10 SCL        -> SCL_LCD   (existing I2C bus, shared with LCD)

WHY 3.3V (not 5V)
-----------------
ADS1115 must run at 3.3V because the ESP32 GPIOs are NOT 5V tolerant — the
I2C bus is already on 3V3 (pulled up by R13/R14 to +3V3 for the LCD), and
running the ADC at 5V would back-drive 5V onto SDA/SCL and damage the
ESP32. ZMPT101B output is centered at VCC/2; at 3.3V supply that is 1.65V,
keeping the AC-sensed signal comfortably within the 0..3.3V ADS1115 input
range. (At 5V supply the centre would be 2.5V, above the 3V3 ADC max.)

Pinout of each header J5..J8 (3-pin, 2.54mm):
  pin 1  GND
  pin 2  +3V3     (powers the external ZMPT101B module from regulated 3V3)
  pin 3  V_SENSE_x  (analog signal from ZMPT101B OUT)

Pull-ups: the existing R13/R14 (4.7k SDA/SCL -> +3V3) are sufficient for
two devices on the bus (PCF8574 LCD backpack + ADS1115). Do NOT add new
pull-ups.

Address conflict: ADS1115 = 0x48 (ADDR=GND). PCF8574 LCD backpack = 0x27
or 0x3F. No conflict.

DOES NOT touch existing tracks, vias, zones, or footprint positions.
The user will hand-route the new connections later.

Run with KiCad 9's bundled Python:
  "/c/Program Files/KiCad/9.0/bin/python.exe" add_voltage_sense.py
"""

from __future__ import annotations

from pathlib import Path
import pcbnew

PROJECT = Path(__file__).resolve().parent
PCB_PATH = PROJECT / "smart_home.kicad_pcb"

KICAD_FP_ROOT = Path(r"C:\Program Files\KiCad\9.0\share\kicad\footprints")
LIB_PACKAGE_SO = str(KICAD_FP_ROOT / "Package_SO.pretty")
LIB_CAP_SMD = str(KICAD_FP_ROOT / "Capacitor_SMD.pretty")
LIB_PIN_HEADER = str(KICAD_FP_ROOT / "Connector_PinHeader_2.54mm.pretty")

MM = 1_000_000  # 1 mm in pcbnew internal units (nm)


def vec(x_mm: float, y_mm: float) -> "pcbnew.VECTOR2I":
    return pcbnew.VECTOR2I(int(round(x_mm * MM)), int(round(y_mm * MM)))


def deg(d: float) -> "pcbnew.EDA_ANGLE":
    return pcbnew.EDA_ANGLE(d, pcbnew.DEGREES_T)


def load_footprint(libpath: str, name: str) -> "pcbnew.FOOTPRINT":
    fp = pcbnew.FootprintLoad(libpath, name)
    if fp is None:
        raise RuntimeError(f"Could not load footprint {name!r} from {libpath!r}")
    return fp


def add_net(board: "pcbnew.BOARD", name: str) -> int:
    nets = board.GetNetsByName()
    if name in nets:
        return nets[name].GetNetCode()
    net = pcbnew.NETINFO_ITEM(board, name)
    board.Add(net)
    return net.GetNetCode()


def set_pad_net(fp: "pcbnew.FOOTPRINT", pad_number: str, netcode: int) -> None:
    for pad in fp.Pads():
        if pad.GetNumber() == pad_number:
            pad.SetNetCode(netcode)
            return
    raise RuntimeError(
        f"pad {pad_number!r} not found on footprint {fp.GetReference()!r}"
    )


def place_footprint(
    board: "pcbnew.BOARD",
    src: "pcbnew.FOOTPRINT",
    reference: str,
    value: str,
    fp_id: str,
    x_mm: float,
    y_mm: float,
    rot_deg: float = 0.0,
) -> "pcbnew.FOOTPRINT":
    """Duplicate `src`, configure it, and add it to `board`."""
    fp = src.Duplicate().Cast()
    board.Add(fp)
    fp.SetReference(reference)
    fp.SetValue(value)
    fp.SetFPID(pcbnew.LIB_ID(fp_id.split(":")[0], fp_id.split(":")[1]))
    fp.SetPosition(vec(x_mm, y_mm))
    if rot_deg != 0:
        fp.SetOrientation(deg(rot_deg))
    return fp


# --------------------------------------------------------------------------
# Placement constants
# --------------------------------------------------------------------------
# Placement strategy: the brief suggested putting U8 at (180,60) and J5..J8
# at (193, 76..91), but a survey of existing routing showed those positions
# collide with several existing F.Cu/B.Cu tracks:
#   - EN track on F.Cu at y=65 from x=150..180 (lands on C16 +3V3 pad)
#   - IO0 track on F.Cu (175.68,58.26)->(180.68,63.26) (crosses U8 area)
#   - +3V3 diagonal track on F.Cu (178.40,84.06)->(192.46,70.00)
#     (crosses J5..J7 area, shorts +3V3 to V_SENSE_1)
#   - GND diagonal track on B.Cu (195,72)->(172.5,94.5) (crosses J5..J7)
# Per brief: "DO NOT delete or modify existing copper traces" — instead,
# the components were relocated to a completely track-free zone in the
# upper-right corner (x=185..200, y=15..45 confirmed empty of segments
# AND vias). TXD0/RXD0 routing starts at y>=47.
#
# U8 ADS1115 TSSOP-10: body 3x3mm + ~0.5mm pad extension on each long side,
# so pads span ~4.3mm wide x ~3mm tall. Centered at (185, 22) — clear of
# R13/R14 (x_max=171.72), J4 (y_max=13.79), and all routed tracks.
U8_X, U8_Y = 185.0, 22.0

# C16 100nF 0805 decoupling — just below U8 (pad-to-pad distance ~3mm).
# Pads at (184, 27) and (186, 27); within 5mm of U8 VDD pin (pad 8 at
# 187.15, 23) and GND pin (pad 3 at 182.85, 22). Body 2mm long.
C16_X, C16_Y = 185.0, 27.0

# J5..J8 3-pin headers in a vertical column at the right edge. Each header
# is placed with rot=270 (CCW 90 deg): pin 1 at (x,y), pins 2 and 3 extend
# in -x direction (pads at x, x-2.54, x-5.08, all at the same y).
# Pin 1 (GND) on the rightmost position gives a consistent connector
# orientation (GND outermost) for external ZMPT101B wiring.
# Vertical column at x=193 (7mm from board edge x=200, well clear of the
# corner mounting hole at (195,5), and the right-most pad (pad 1) is at
# x=193 / leftmost (pad 3) is at x=187.92). y values chosen to fit between
# C16 (y=27, body bottom at ~y=28) and the start of TXD0/RXD0 routing
# (y=47.70), with 5mm pitch. Pad 3 of J8 (187.92, 43) is 4.7mm clear of
# the TXD0 via at (187.49, 47.70).
HEADER_X = 193.0
HEADER_ROT = 270.0
J5_Y, J6_Y, J7_Y, J8_Y = 30.0, 35.0, 40.0, 45.0


def main():
    print(f"Loading {PCB_PATH}")
    board = pcbnew.LoadBoard(str(PCB_PATH))

    # ---------- Pre-flight ---------------------------------------------------
    tracks_before = list(board.Tracks())
    initial_segments = sum(1 for t in tracks_before if t.GetClass() == "PCB_TRACK")
    initial_vias = sum(1 for t in tracks_before if t.GetClass() == "PCB_VIA")
    initial_footprints = len(board.Footprints())
    initial_nets = len(board.GetNetsByName())
    print(
        f"  before: footprints={initial_footprints}, "
        f"segments={initial_segments}, vias={initial_vias}, nets={initial_nets}"
    )

    # ---------- Add 4 new V_SENSE_x nets -------------------------------------
    NEW_NETS = [f"V_SENSE_{ch}" for ch in (1, 2, 3, 4)]
    new_net_code = {n: add_net(board, n) for n in NEW_NETS}
    print(f"  added {len(NEW_NETS)} nets: {NEW_NETS}")

    # ---------- Look up existing net codes we'll need ------------------------
    existing = board.GetNetsByName()
    gnd_net = existing["GND"].GetNetCode()
    p3v3_net = existing["+3V3"].GetNetCode()
    sda_net = existing["SDA_LCD"].GetNetCode()
    scl_net = existing["SCL_LCD"].GetNetCode()

    # ---------- Load donor footprints ----------------------------------------
    src_ads = load_footprint(LIB_PACKAGE_SO, "TSSOP-10_3x3mm_P0.5mm")
    src_c0805 = load_footprint(LIB_CAP_SMD, "C_0805_2012Metric")
    src_hdr = load_footprint(LIB_PIN_HEADER, "PinHeader_1x03_P2.54mm_Vertical")

    # ---------- U8 ADS1115 TSSOP-10 -----------------------------------------
    # KiCad ADS1115IDGS symbol pin->function mapping (verified against
    # Analog_ADC.kicad_sym in KiCad 9.0). Footprint pad numbers 1..10
    # bind directly to symbol pin numbers via lib_id.
    u8 = place_footprint(
        board, src_ads, "U8", "ADS1115",
        "Package_SO:TSSOP-10_3x3mm_P0.5mm",
        U8_X, U8_Y, 0,
    )
    set_pad_net(u8, "1", gnd_net)        # ADDR -> GND (I2C addr 0x48)
    # pad 2 ALERT/RDY intentionally left on net 0 (no connection)
    set_pad_net(u8, "3", gnd_net)        # GND
    set_pad_net(u8, "4", new_net_code["V_SENSE_1"])   # AIN0
    set_pad_net(u8, "5", new_net_code["V_SENSE_2"])   # AIN1
    set_pad_net(u8, "6", new_net_code["V_SENSE_3"])   # AIN2
    set_pad_net(u8, "7", new_net_code["V_SENSE_4"])   # AIN3
    set_pad_net(u8, "8", p3v3_net)       # VDD -> +3V3
    set_pad_net(u8, "9", sda_net)        # SDA -> SDA_LCD
    set_pad_net(u8, "10", scl_net)       # SCL -> SCL_LCD
    print(f"  placed U8 ADS1115 at ({U8_X}, {U8_Y})")

    # ---------- C16 100nF decoupling ----------------------------------------
    c16 = place_footprint(
        board, src_c0805, "C16", "100nF",
        "Capacitor_SMD:C_0805_2012Metric",
        C16_X, C16_Y, 0,
    )
    set_pad_net(c16, "1", p3v3_net)
    set_pad_net(c16, "2", gnd_net)
    print(f"  placed C16 at ({C16_X}, {C16_Y})")

    # ---------- J5..J8 3-pin headers (external ZMPT101B connection) ---------
    # Per header: pin 1 = GND, pin 2 = +3V3, pin 3 = V_SENSE_x
    HEADERS = [
        ("J5", J5_Y, "V_SENSE_1"),
        ("J6", J6_Y, "V_SENSE_2"),
        ("J7", J7_Y, "V_SENSE_3"),
        ("J8", J8_Y, "V_SENSE_4"),
    ]
    for ref, y, sense_net in HEADERS:
        j = place_footprint(
            board, src_hdr, ref, sense_net,
            "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
            HEADER_X, y, HEADER_ROT,
        )
        set_pad_net(j, "1", gnd_net)
        set_pad_net(j, "2", p3v3_net)
        set_pad_net(j, "3", new_net_code[sense_net])
        print(f"  placed {ref} at ({HEADER_X}, {y}) rot={HEADER_ROT} -> {sense_net}")

    # ---------- Post-flight: confirm no tracks/vias destroyed ----------------
    tracks_after = list(board.Tracks())
    final_segments = sum(1 for t in tracks_after if t.GetClass() == "PCB_TRACK")
    final_vias = sum(1 for t in tracks_after if t.GetClass() == "PCB_VIA")
    final_footprints = len(board.Footprints())
    final_nets = len(board.GetNetsByName())

    assert final_segments == initial_segments, (
        f"segment count changed: {initial_segments} -> {final_segments}"
    )
    assert final_vias == initial_vias, (
        f"via count changed: {initial_vias} -> {final_vias}"
    )
    assert final_footprints == initial_footprints + 6, (
        f"footprint count: expected {initial_footprints + 6}, got {final_footprints}"
    )
    assert final_nets == initial_nets + 4, (
        f"net count: expected {initial_nets + 4}, got {final_nets}"
    )
    print(
        f"  after:  footprints={final_footprints}, "
        f"segments={final_segments}, vias={final_vias}, nets={final_nets}"
    )

    pcbnew.SaveBoard(str(PCB_PATH), board)
    print(f"Saved {PCB_PATH}")


if __name__ == "__main__":
    main()
