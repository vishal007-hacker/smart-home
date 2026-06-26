"""
add_acs712.py
-------------
Append per-channel AC current sensing components to an existing routed PCB:
  - 4x ACS712-05B SOIC-8 Hall-effect current sensors (U4..U7)
  - 4x 100nF 0805 decoupling caps (C8..C11)
  - 4x 10k 0805 divider-top resistors (R15..R18)
  - 4x 18k 0805 divider-bottom resistors (R19..R22)

Adds 12 new nets:
  - LOAD1_OUT..LOAD4_OUT       (HV: ACS712 IP- -> J2x.1)
  - I_SENSE_1..I_SENSE_4       (LV: divider midpoint -> ESP32 ADC)
  - VOUT_1..VOUT_4             (LV: ACS712 VIOUT -> divider top resistor)

Rewires the existing J2x pad 1 net from LOADx to LOADx_OUT, so the load
current path is broken between the relay and the load terminal — the user
will hand-route LOADx Kx.NO -> Ux.IP+ and LOADx_OUT Ux.IP- -> J2x.1.

DOES NOT touch existing tracks, vias, zones, or footprint positions.

Run with KiCad 9's bundled Python:
  "/c/Program Files/KiCad/9.0/bin/python.exe" add_acs712.py
"""

from __future__ import annotations

from pathlib import Path
import pcbnew

PROJECT = Path(__file__).resolve().parent
PCB_PATH = PROJECT / "smart_home.kicad_pcb"

KICAD_FP_ROOT = Path(r"C:\Program Files\KiCad\9.0\share\kicad\footprints")
LIB_PACKAGE_SO = str(KICAD_FP_ROOT / "Package_SO.pretty")
LIB_RES_SMD = str(KICAD_FP_ROOT / "Resistor_SMD.pretty")
LIB_CAP_SMD = str(KICAD_FP_ROOT / "Capacitor_SMD.pretty")

MM = 1_000_000  # 1 mm in pcbnew internal units (nm)


def vec(x_mm: float, y_mm: float) -> "pcbnew.VECTOR2I":
    return pcbnew.VECTOR2I(int(round(x_mm * MM)), int(round(y_mm * MM)))


def deg(d: float) -> "pcbnew.EDA_ANGLE":
    return pcbnew.EDA_ANGLE(d, pcbnew.DEGREES_T)


# Channel rows match the existing K1-K4 / J2A-J2D rows.
ROWS_Y = {1: 30.0, 2: 50.0, 3: 70.0, 4: 90.0}


# Per-channel component positions (x, y, rotation_deg).
# ACS712 placed in the K-to-J2 horizontal gap on the channel row.
#   K body x=59.45..79.55, J2 body x=87.87..99.04 -> 8.32 mm gap.
#   ACS712 SOIC-8 body ~5.4 mm wide; center at x=83.7 fits cleanly.
# Decoupling cap and divider resistors placed to the right of the ACS712
# (overlapping the J2 footprint slightly per brief — user will hand-fix).
# Place ACS712 in the K-to-J2 horizontal gap (x=79.55..87.87). Center at
# x=83.7 fits the 5.4mm-wide SOIC-8 body with ~1.5mm clearance to each side.
# This avoids hard pad-on-pad shorts with the J2x output terminals.
ACS712_X = 83.7
# Decoupling cap and divider passives go in the inter-row vertical gaps
# (K body y_max=y_row+7.95, next K y_min=y_row+12.05 -> 4mm gap centered at
# y_row+10). 0805 body is 1.96mm tall, fits with ~1mm to spare on each side.
# For channel 4 (y=90) the inter-row "gap below" is outside the board, so
# we place its passives in the same column but above K4 (y_row-10) by using
# a per-channel offset list.
DECOUP_X = 81.0     # cap toward left edge of K-J2 gap
R_TOP_X  = 85.0     # divider top, 4mm right of cap (clear courtyard overlap)
R_BOT_X  = 89.0     # divider bottom, 4mm right of R_top


def load_footprint(libpath: str, name: str) -> "pcbnew.FOOTPRINT":
    fp = pcbnew.FootprintLoad(libpath, name)
    if fp is None:
        raise RuntimeError(f"Could not load footprint {name!r} from {libpath!r}")
    return fp


def add_net(board: "pcbnew.BOARD", name: str) -> int:
    """Create the named net on the board (if missing) and return its netcode."""
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
    # Duplicate() returns a BOARD_ITEM; cast to FOOTPRINT.
    fp = src.Duplicate().Cast()
    board.Add(fp)
    fp.SetReference(reference)
    fp.SetValue(value)
    fp.SetFPID(pcbnew.LIB_ID(fp_id.split(":")[0], fp_id.split(":")[1]))
    fp.SetPosition(vec(x_mm, y_mm))
    if rot_deg != 0:
        fp.SetOrientation(deg(rot_deg))
    return fp


def main():
    print(f"Loading {PCB_PATH}")
    board = pcbnew.LoadBoard(str(PCB_PATH))

    # ---------- Pre-flight: confirm we're acting on the routed board -------
    initial_tracks = len(board.Tracks())
    initial_footprints = len(board.Footprints())
    initial_nets = len(board.GetNetsByName())
    print(
        f"  before: footprints={initial_footprints}, tracks={initial_tracks}, "
        f"nets={initial_nets}"
    )

    # ---------- Add new nets ------------------------------------------------
    NEW_NETS = []
    for ch in (1, 2, 3, 4):
        NEW_NETS.append(f"LOAD{ch}_OUT")
        NEW_NETS.append(f"I_SENSE_{ch}")
        NEW_NETS.append(f"VOUT_{ch}")
    net_code = {n: add_net(board, n) for n in NEW_NETS}
    print(f"  added {len(NEW_NETS)} nets")

    # ---------- Load donor footprints ---------------------------------------
    src_acs = load_footprint(LIB_PACKAGE_SO, "SOIC-8_3.9x4.9mm_P1.27mm")
    src_r0805 = load_footprint(LIB_RES_SMD, "R_0805_2012Metric")
    src_c0805 = load_footprint(LIB_CAP_SMD, "C_0805_2012Metric")

    # ---------- Look up existing net codes we'll need -----------------------
    existing = board.GetNetsByName()
    gnd_net = existing["GND"].GetNetCode()
    p5v_net = existing["+5V"].GetNetCode()

    # Inter-row vertical gap positions for passives.
    # Channels 1-3: place in the inter-row gap below the ACS712.
    # Channel 4 has no inter-row gap below (board edge at y=100), so place
    # at y=98 (1mm clearance to the 200x100 board edge); the mounting holes
    # at (5,95) and (195,95) are far from x=83..90.
    PASS_Y = {1: 40.0, 2: 60.0, 3: 80.0, 4: 98.0}

    # ---------- Place per-channel components --------------------------------
    for ch in (1, 2, 3, 4):
        y = ROWS_Y[ch]
        py = PASS_Y[ch]

        u_ref = f"U{3 + ch}"
        c_ref = f"C{7 + ch}"
        rt_ref = f"R{14 + ch}"
        rb_ref = f"R{18 + ch}"

        loadx = existing[f"LOAD{ch}"].GetNetCode()
        loadx_out = net_code[f"LOAD{ch}_OUT"]
        vout_x = net_code[f"VOUT_{ch}"]
        i_sense_x = net_code[f"I_SENSE_{ch}"]

        # ACS712 SOIC-8, rotation 0:
        #   pad 1,2 = IP+ (left, top)     -> LOADx (from Kx.NO)
        #   pad 3,4 = IP- (left, bottom)  -> LOADx_OUT (to J2x.1)
        #   pad 5   = GND
        #   pad 6   = FILTER (NC)
        #   pad 7   = VIOUT               -> VOUT_x
        #   pad 8   = VCC                 -> +5V
        u = place_footprint(
            board, src_acs, u_ref, "ACS712-05B",
            "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
            ACS712_X, y, 0,
        )
        set_pad_net(u, "1", loadx)
        set_pad_net(u, "2", loadx)
        set_pad_net(u, "3", loadx_out)
        set_pad_net(u, "4", loadx_out)
        set_pad_net(u, "5", gnd_net)
        # pad 6 (FILTER) intentionally left on net 0 (no connection)
        set_pad_net(u, "7", vout_x)
        set_pad_net(u, "8", p5v_net)

        # 100nF decoupling cap in inter-row gap.
        c = place_footprint(
            board, src_c0805, c_ref, "100nF",
            "Capacitor_SMD:C_0805_2012Metric",
            DECOUP_X, py, 0,
        )
        set_pad_net(c, "1", p5v_net)
        set_pad_net(c, "2", gnd_net)

        # 10k divider-top resistor (VOUT_x -> I_SENSE_x).
        rt = place_footprint(
            board, src_r0805, rt_ref, "10k",
            "Resistor_SMD:R_0805_2012Metric",
            R_TOP_X, py, 0,
        )
        set_pad_net(rt, "1", vout_x)
        set_pad_net(rt, "2", i_sense_x)

        # 18k divider-bottom resistor (I_SENSE_x -> GND).
        rb = place_footprint(
            board, src_r0805, rb_ref, "18k",
            "Resistor_SMD:R_0805_2012Metric",
            R_BOT_X, py, 0,
        )
        set_pad_net(rb, "1", i_sense_x)
        set_pad_net(rb, "2", gnd_net)

        # ---- Rewire existing J2x pad 1: LOADx -> LOADx_OUT ----
        # The previous route went Kx.NO -> LOADx -> J2x.1 (one net). With the
        # ACS712 inserted, LOADx now stops at Ux.IP+ and a new net LOADx_OUT
        # carries Ux.IP- -> J2x.1.
        j2_ref = {1: "J2A", 2: "J2B", 3: "J2C", 4: "J2D"}[ch]
        for fp in board.Footprints():
            if fp.GetReference() == j2_ref:
                for pad in fp.Pads():
                    if pad.GetNumber() == "1":
                        old_code = pad.GetNetCode()
                        pad.SetNetCode(loadx_out)
                        print(
                            f"  {j2_ref}.1: net {old_code} ({existing[f'LOAD{ch}'].GetNetname()}) "
                            f"-> {loadx_out} (LOAD{ch}_OUT)"
                        )
                break

    # ---------- Save --------------------------------------------------------
    final_tracks = len(board.Tracks())
    final_footprints = len(board.Footprints())
    final_nets = len(board.GetNetsByName())
    assert final_tracks == initial_tracks, (
        f"track count changed: {initial_tracks} -> {final_tracks}"
    )
    print(
        f"  after:  footprints={final_footprints}, tracks={final_tracks}, "
        f"nets={final_nets}"
    )

    pcbnew.SaveBoard(str(PCB_PATH), board)
    print(f"Saved {PCB_PATH}")


if __name__ == "__main__":
    main()
