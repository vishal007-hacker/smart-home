"""
Generate a complete smart_home.kicad_sch for the 4-channel ESP32 smart home
AC relay PCB project.

Reads bom.csv (27 components), extracts each referenced library symbol from
the standard KiCad 9 symbol libraries (plus the local hlk_pm01 lib and the
power lib for GND/+5V/+3V3 power flags), places component instances on an
A3 sheet in a sensible HV-left / LV-right grid layout, and adds wires plus
labels for the major nets described in the spec.

Re-run this script whenever bom.csv changes; it overwrites smart_home.kicad_sch.
"""

from __future__ import 


import csv
import os
import re
import uuid
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PROJECT_DIR   = Path(__file__).resolve().parent
BOM_PATH      = PROJECT_DIR / "bom.csv"
SCH_PATH      = PROJECT_DIR / "smart_home.kicad_sch"
HLK_LIB_PATH  = PROJECT_DIR / "lib" / "hlk_pm01.kicad_sym"
KICAD_SYMS    = Path(r"C:\Program Files\KiCad\9.0\share\kicad\symbols")


# --------------------------------------------------------------------------
# UUID helper — every primitive needs one
# --------------------------------------------------------------------------
def U() -> str:
    return str(uuid.uuid4())


ROOT_UUID = "a1b2c3d4-0000-0000-0000-000000000001"  # match existing file


# --------------------------------------------------------------------------
# BOM parsing — the CSV has *unquoted* commas in two TerminalBlock footprint
# names (J1, J2). The header is 8 columns; any row with >8 columns means a
# comma leaked into the Footprint or Manufacturer Part No field. Detect and
# rejoin so columns realign.
# --------------------------------------------------------------------------
def load_bom():
    rows = list(csv.reader(open(BOM_PATH, encoding="utf-8")))
    header = rows[0]
    out = []
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        if len(r) > len(header):
            # The footprint field (idx 5) contains a comma. Glue idx 5 + 6 back.
            excess = len(r) - len(header)
            fp = ",".join(r[5 : 5 + 1 + excess])
            r = r[:5] + [fp] + r[5 + 1 + excess :]
        out.append(dict(zip(header, r)))
    return out


# --------------------------------------------------------------------------
# Expand grouped references like "K1-K4" or "D1-D4" into individual rows.
# Returns a flat list of (ref, value, lib_id, footprint, description) tuples.
# --------------------------------------------------------------------------
def expand_components(bom):
    parts = []
    for row in bom:
        ref_raw = row["Reference"].strip()
        value   = row["Value"].strip()
        lib_id  = row["KiCad Symbol"].strip()
        fp      = row["KiCad Footprint"].strip()
        desc    = row["Description"].strip()

        m = re.match(r"^([A-Za-z]+)(\d+)-([A-Za-z]+)?(\d+)$", ref_raw)
        if m:
            prefix, start, _, end = m.groups()
            for n in range(int(start), int(end) + 1):
                parts.append((f"{prefix}{n}", value, lib_id, fp, desc))
        else:
            parts.append((ref_raw, value, lib_id, fp, desc))
    return parts


# --------------------------------------------------------------------------
# Extract a top-level (symbol "NAME" ...) block from a .kicad_sym library.
# Uses balanced-paren scanning (string-aware).
# --------------------------------------------------------------------------
def extract_symbol(lib_path: Path, sym_name: str) -> str:
    text = lib_path.read_text(encoding="utf-8")
    needle = f'(symbol "{sym_name}"'
    i = text.find(needle)
    if i < 0:
        raise RuntimeError(f"Symbol {sym_name!r} not found in {lib_path}")

    depth = 0
    in_str = False
    esc = False
    j = i
    while j < len(text):
        c = text[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return text[i : j + 1]
        j += 1
    raise RuntimeError(f"Unterminated symbol block for {sym_name}")


# Some lib_ids in the BOM don't resolve in the stock KiCad 9 libraries (e.g.
# ULN2003A actually lives in Transistor_Array, not Driver_Motor). Remap here
# without modifying bom.csv. The lib_id stored in the *placed component* is
# still the rewritten one, which keeps lib_symbols consistent.
LIB_ID_REMAP = {
    "Driver_Motor:ULN2003A": "Transistor_Array:ULN2003A",
}


# Map lib_id -> (library file path, symbol name)
def lib_id_to_path(lib_id: str) -> tuple[Path, str]:
    lib, sym = lib_id.split(":", 1)
    if lib == "hlk_pm01":
        return HLK_LIB_PATH, sym
    return KICAD_SYMS / f"{lib}.kicad_sym", sym


def resolve_lib_id(lib_id: str) -> str:
    return LIB_ID_REMAP.get(lib_id, lib_id)


# Rewrite an extracted symbol so its top-level name is prefixed with "lib:" —
# that's what KiCad puts in lib_symbols of a .kicad_sch (e.g. "Device:R", not
# just "R"). Sub-unit symbols inside keep their original names.
def rename_top_symbol(block: str, lib_id: str) -> str:
    # Only replace the very first '(symbol "NAME"' occurrence.
    return re.sub(
        r'^\(symbol\s+"[^"]+"',
        f'(symbol "{lib_id}"',
        block,
        count=1,
    )


# --------------------------------------------------------------------------
# Split a symbol block into its child fragments (top-level S-exps inside the
# outer (symbol ...) parens). Used to flatten `(extends ...)` symbols.
# --------------------------------------------------------------------------
def split_symbol_children(block: str) -> tuple[str, list[str]]:
    """Return (header_line, [child_sexpr_strings]).

    header_line is the '(symbol "NAME"' opener. children are the inner forms
    in order (each a complete balanced S-exp like '(extends "X")' or
    '(property "Reference" "U" (at ...))' or '(symbol "X_0_1" ...)').
    """
    # Find the opener
    m = re.match(r'^\(symbol\s+"[^"]+"\s*', block)
    assert m, f"bad symbol header: {block[:80]!r}"
    header = m.group(0).rstrip()
    body = block[m.end() : -1]  # strip trailing ')'

    children = []
    i = 0
    while i < len(body):
        # Skip whitespace
        while i < len(body) and body[i] in " \t\r\n":
            i += 1
        if i >= len(body):
            break
        if body[i] != "(":
            # Stray text — shouldn't happen
            i += 1
            continue
        # Walk balanced parens
        depth = 0
        in_str = False
        esc = False
        start = i
        while i < len(body):
            c = body[i]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        children.append(body[start:i])
                        break
            i += 1
    return header, children


def child_head(sexpr: str) -> str:
    """Return the first token of an S-exp, e.g. 'extends' or 'property'."""
    m = re.match(r'^\(\s*([A-Za-z_][A-Za-z0-9_]*)', sexpr)
    return m.group(1) if m else ""


def child_property_name(sexpr: str) -> str | None:
    m = re.match(r'^\(\s*property\s+"([^"]+)"', sexpr)
    return m.group(1) if m else None


def _child_symbol_name(sexpr: str) -> str | None:
    """If sexpr is (symbol "NAME" ...), return NAME, else None."""
    m = re.match(r'^\(\s*symbol\s+"([^"]+)"', sexpr)
    return m.group(1) if m else None


def flatten_extends(block: str, parent_block: str, child_name: str) -> str:
    """Replace (extends "P") in a child symbol with the parent's pins,
    graphics and sub-unit symbols. The child keeps its own properties
    (overriding any same-named parent property). Sub-symbol names inherited
    from the parent are rewritten to use the child's name (so they match
    the conventional `<name>_<unit>_<bodystyle>` form KiCad expects)."""
    child_hdr, child_children = split_symbol_children(block)
    _,         parent_children = split_symbol_children(parent_block)

    parent_name_match = re.match(r'^\(symbol\s+"([^"]+)"', parent_block)
    parent_name = parent_name_match.group(1) if parent_name_match else None

    # Drop the (extends ...) from the child.
    child_children = [c for c in child_children if child_head(c) != "extends"]

    # Collect property names defined by the child so we don't duplicate them
    # when pulling from the parent.
    child_prop_names = {
        child_property_name(c) for c in child_children
        if child_head(c) == "property"
    }

    # From the parent, take everything EXCEPT (extends), and skip any
    # property whose name is already overridden by the child. Keep the
    # parent's pin_numbers/pin_names settings and sub-symbols (which carry
    # the actual graphics + pins). Rename sub-symbols from
    # "<parent>_<u>_<b>" to "<child>_<u>_<b>".
    merged_parent = []
    for c in parent_children:
        h = child_head(c)
        if h == "extends":
            continue
        if h == "property":
            name = child_property_name(c)
            if name in child_prop_names:
                continue
        if h == "symbol" and parent_name:
            sub_name = _child_symbol_name(c)
            if sub_name and sub_name.startswith(parent_name + "_"):
                suffix = sub_name[len(parent_name):]
                new_sub = child_name + suffix
                c = re.sub(
                    r'^\(\s*symbol\s+"[^"]+"',
                    f'(symbol "{new_sub}"',
                    c,
                    count=1,
                )
        merged_parent.append(c)

    # Put child properties first (matches KiCad's output ordering), then
    # everything inherited from the parent.
    new_children = child_children + merged_parent

    body = "\n\t\t".join(new_children)
    return f"{child_hdr}\n\t\t{body}\n\t)"


# --------------------------------------------------------------------------
# Layout — A3 sheet (420 x 297 mm). HV components on the left half, LV on the
# right. Each entry: (ref, x_mm, y_mm, rotation_deg).
#
# NEW DESIGN: 4 fully-isolated AC inputs. Each input row has:
#   J1x (3-pos input terminal)  ->  Fx (fuse)  ->  Kx.COM (relay COM)
#                                                  Kx.NO -> J2x (2-pos load out)
# Input 1 (top row) also hosts RV1 (across L1/N1) and PS1 (HLK-PM01 PSU).
# --------------------------------------------------------------------------
LAYOUT = {
    # ---- HV side (left) — 4 input rows -----------------------------------
    # Row 1 (top) — input 1 with PSU + MOV
    "J1A": ( 30,  60, 0),    # AC input 1
    "F1":  ( 60,  60, 0),    # fuse 1
    "K1":  ( 95,  60, 0),    # relay 1
    "J2A": (135,  60, 0),    # load output 1

    "RV1": ( 60,  80, 0),    # varistor across L1-N1
    "PS1": (100,  80, 0),    # HLK-PM01 from L1/N1

    # Row 2 — input 2
    "J1B": ( 30, 115, 0),
    "F2":  ( 60, 115, 0),
    "K2":  ( 95, 115, 0),
    "J2B": (135, 115, 0),

    # Row 3 — input 3
    "J1C": ( 30, 160, 0),
    "F3":  ( 60, 160, 0),
    "K3":  ( 95, 160, 0),
    "J2C": (135, 160, 0),

    # Row 4 — input 4
    "J1D": ( 30, 205, 0),
    "F4":  ( 60, 205, 0),
    "K4":  ( 95, 205, 0),
    "J2D": (135, 205, 0),

    # ---- LV side (right) — shifted right to clear HV layout -------------
    "C1":  (175,  95, 0),    # HLK bulk cap

    # AMS1117 regulator
    "U3":  (195,  70, 0),
    "C2":  (180,  75, 0),
    "C3":  (210,  75, 0),

    # ESP32 module
    "U1":  (295, 120, 0),

    # ESP32 support
    "C4":  (265,  95, 0),
    "C5":  (280,  95, 0),
    "C6":  (265, 150, 0),
    "R11": (270, 135, 0),    # EN pullup
    "R12": (325, 135, 0),    # IO0 pullup

    # ULN2003 + decoupling
    "U2":  (210, 155, 0),
    "C7":  (190, 140, 0),

    # Manual override buttons + pullups
    "SW1": (345, 165, 0),
    "SW2": (345, 180, 0),
    "SW3": (345, 195, 0),
    "SW4": (345, 210, 0),
    "R5":  (360, 160, 0),
    "R6":  (360, 175, 0),
    "R7":  (360, 190, 0),
    "R8":  (360, 205, 0),

    # BOOT / RESET buttons
    "SW5": (330, 225, 0),    # BOOT
    "SW6": (315, 225, 0),    # RESET

    # Status LEDs + series resistors
    "D1":  (245, 225, 0),
    "D2":  (245, 240, 0),
    "D3":  (245, 255, 0),
    "D4":  (245, 270, 0),
    "R1":  (230, 225, 0),
    "R2":  (230, 240, 0),
    "R3":  (230, 255, 0),
    "R4":  (230, 270, 0),

    # Power indicator
    "D5":  (205, 100, 0),
    "R9":  (190, 100, 0),

    # Programming header
    "J3":  (395, 100, 0),

    # LCD I2C header + pull-ups (top-right area, near ESP32 module)
    # External 16x2 LCD with PCF8574 backpack: GND, +5V, SDA, SCL
    "J4":  (395,  60, 0),    # 4-pin header for external LCD
    "R13": (370,  50, 0),    # SDA pull-up to +3V3
    "R14": (370,  60, 0),    # SCL pull-up to +3V3

    # ---- Per-channel AC current sensing (ACS712-05B + decoupling + divider) ----
    # One ACS712 per channel between Kx.NO and J2x.1, with +5V decoupling
    # cap (C8-C11) and a 10k/18k divider (R15-R18 / R19-R22) on VIOUT for the
    # ESP32 ADC.
    # ACS712 placed to the right of each channel row's relay/J2 cluster; the
    # divider passives sit just below the IC. The +5V decoupling cap sits
    # immediately next to VCC (pin 8).
    "U4":  (160,  60, 0),    # ch1 ACS712
    "C8":  (155,  68, 0),    # ch1 decoupling 100nF
    "R15": (172,  64, 0),    # ch1 divider top 10k (Vout -> ADC)
    "R19": (172,  72, 0),    # ch1 divider bot 18k (ADC -> GND)

    "U5":  (160, 115, 0),    # ch2 ACS712
    "C9":  (155, 123, 0),
    "R16": (172, 119, 0),
    "R20": (172, 127, 0),

    "U6":  (160, 160, 0),    # ch3 ACS712
    "C10": (155, 168, 0),
    "R17": (172, 164, 0),
    "R21": (172, 172, 0),

    "U7":  (160, 205, 0),    # ch4 ACS712
    "C11": (155, 213, 0),
    "R18": (172, 209, 0),
    "R22": (172, 217, 0),

    # ---- Per-channel AC voltage sensing (ADS1115 + external ZMPT101B) -----
    # ADS1115 I2C 16-bit 4-channel ADC samples ZMPT101B AC voltage sensor
    # outputs (one per channel). The ZMPT101B modules are EXTERNAL; J5-J8
    # are 3-pin headers (GND, +3V3, VOUT) that connect to each module.
    # ADS1115 must run at 3.3V because the ESP32 GPIO is not 5V-tolerant and
    # the I2C bus is shared with the LCD on the 3V3 rail; running the ADC
    # at 5V would back-drive 5V on the I2C lines and damage the ESP32.
    # ZMPT101B output is centered at VCC/2 = 1.65V at 3.3V supply, which
    # keeps the AC signal within the 0-3.3V ADS1115 input range.
    # Placed in the LV upper-right area, near the LCD I2C connector (J4) and
    # well clear of the ESP32 module (U1 at 295,120).
    "U8":  (245,  60, 0),    # ADS1115 TSSOP-10
    "C16": (230,  60, 0),    # 100nF VDD decoupling, near U8
    "J5":  (395, 130, 0),    # ch1 external ZMPT101B header (GND/+3V3/VOUT)
    "J6":  (395, 150, 0),    # ch2
    "J7":  (395, 170, 0),    # ch3
    "J8":  (395, 190, 0),    # ch4
}


# --------------------------------------------------------------------------
# Component instance emitter
# --------------------------------------------------------------------------
def emit_symbol(ref, value, lib_id, footprint, x, y, rot, root_uuid):
    sym_uuid = U()
    return f'''\t(symbol
\t\t(lib_id "{lib_id}")
\t\t(at {x} {y} {rot})
\t\t(unit 1)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(uuid "{sym_uuid}")
\t\t(property "Reference" "{ref}"
\t\t\t(at {x + 2.54} {y - 5.08} 0)
\t\t\t(effects (font (size 1.27 1.27)) (justify left))
\t\t)
\t\t(property "Value" "{value}"
\t\t\t(at {x + 2.54} {y + 5.08} 0)
\t\t\t(effects (font (size 1.27 1.27)) (justify left))
\t\t)
\t\t(property "Footprint" "{footprint}"
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) hide)
\t\t)
\t\t(property "Datasheet" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) hide)
\t\t)
\t\t(property "Description" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) hide)
\t\t)
\t\t(instances
\t\t\t(project "smart_home"
\t\t\t\t(path "/{root_uuid}"
\t\t\t\t\t(reference "{ref}")
\t\t\t\t\t(unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
'''


def emit_wire(x1, y1, x2, y2):
    return (
        f'\t(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) '
        f'(stroke (width 0) (type default)) (uuid "{U()}"))\n'
    )


def emit_label(text, x, y, rot=0):
    return (
        f'\t(label "{text}" (at {x} {y} {rot}) '
        f'(effects (font (size 1.27 1.27)) (justify left bottom)) '
        f'(uuid "{U()}"))\n'
    )


def emit_global_label(text, x, y, rot=0, shape="bidirectional"):
    return (
        f'\t(global_label "{text}" (shape {shape}) (at {x} {y} {rot}) '
        f'(effects (font (size 1.27 1.27)) (justify left)) '
        f'(uuid "{U()}"))\n'
    )


# --------------------------------------------------------------------------
# Build everything
# --------------------------------------------------------------------------
def main():
    bom   = load_bom()
    parts = expand_components(bom)
    # BOM has 43 line items (4-input rework + LCD I2C: J4, R13, R14 + per-
    # channel current sensing: U4-U7, C8-C11, R15-R18, R19-R22 + per-channel
    # AC voltage sensing: U8 ADS1115, C16, J5-J8 external ZMPT101B headers).
    # Expanding grouped refs (K1-K4, D1-D4, R1-R4, R5-R8, SW1-SW4, F1-F4,
    # U4-U7, C8-C11, R15-R18, R19-R22, J5-J8) yields 76 individual component
    # instances.
    assert len(bom) == 43, f"expected 43 BOM rows, got {len(bom)}"
    assert len(parts) == 76, f"expected 76 expanded components, got {len(parts)}"

    # ---- Build lib_symbols (unique lib_ids + power symbols) ---------------
    lib_ids = sorted({resolve_lib_id(lib_id) for _, _, lib_id, _, _ in parts})
    # Power flags used in wiring:
    power_ids = ["power:GND", "power:+5V", "power:+3V3", "power:Earth_Protective"]
    lib_ids_all = lib_ids + power_ids

    # Some symbols use `(extends "OtherName")` — a reference to a sibling
    # symbol in the same library that holds the actual pins and graphics.
    # KiCad's *schematic* loader does not resolve extends inside lib_symbols
    # (verified with kicad-cli on KiCad 9.0), so we flatten the inheritance:
    # the child's overriding properties are kept, and the parent's graphics,
    # pins, and remaining properties are merged in.
    def fetch(lid: str) -> str:
        lib_path, sym_name = lib_id_to_path(lid)
        return extract_symbol(lib_path, sym_name)

    def resolve(lid: str) -> str:
        raw = fetch(lid)
        m = re.search(r'\(extends\s+"([^"]+)"\)', raw)
        if not m:
            return raw
        lib_prefix = lid.split(":", 1)[0]
        parent_lid = f"{lib_prefix}:{m.group(1)}"
        parent_resolved = resolve(parent_lid)
        # Child symbol name (without lib prefix) — used to rename inherited
        # sub-symbols so they match the conventional naming.
        child_name = lid.split(":", 1)[1]
        return flatten_extends(raw, parent_resolved, child_name)

    lib_symbols_blocks = []
    for lid in lib_ids_all:
        block = resolve(lid)
        block = rename_top_symbol(block, lid)
        lib_symbols_blocks.append(block)

    # ---- Build component instances ----------------------------------------
    inst_blocks = []
    missing = []
    for ref, value, lib_id, fp, _desc in parts:
        if ref not in LAYOUT:
            missing.append(ref)
            continue
        x, y, rot = LAYOUT[ref]
        inst_blocks.append(
            emit_symbol(
                ref, value, resolve_lib_id(lib_id), fp, x, y, rot, ROOT_UUID
            )
        )
    if missing:
        raise RuntimeError(f"No layout coords for: {missing}")

    # ---- Power flags (so KiCad knows +5V / +3V3 / GND nets exist) ----------
    # Place a single power symbol of each kind on the sheet so the global
    # nets aren't reported as "no driver" during ERC.
    power_instances = []
    def add_power(ref_prefix, lib_id, x, y):
        # Power symbols use lib_id like "power:GND"; their reference is "#PWR##".
        # We just emit them as ordinary symbol instances.
        u = U()
        power_instances.append(
            f'''\t(symbol
\t\t(lib_id "{lib_id}")
\t\t(at {x} {y} 0)
\t\t(unit 1)
\t\t(exclude_from_sim no)
\t\t(in_bom no)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(uuid "{u}")
\t\t(property "Reference" "#PWR{ref_prefix:02d}"
\t\t\t(at {x} {y - 2.54} 0)
\t\t\t(effects (font (size 1.27 1.27)) hide)
\t\t)
\t\t(property "Value" "{lib_id.split(":")[1]}"
\t\t\t(at {x} {y + 2.54} 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Footprint" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) hide)
\t\t)
\t\t(property "Datasheet" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) hide)
\t\t)
\t\t(property "Description" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) hide)
\t\t)
\t\t(instances
\t\t\t(project "smart_home"
\t\t\t\t(path "/{ROOT_UUID}"
\t\t\t\t\t(reference "#PWR{ref_prefix:02d}")
\t\t\t\t\t(unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
'''
        )

    add_power(1, "power:GND",  175, 240)
    add_power(2, "power:+5V",  175,  85)
    add_power(3, "power:+3V3", 265,  80)
    add_power(4, "power:Earth_Protective", 40, 67)

    # ---- Labels for major nets --------------------------------------------
    # These are placed as floating labels near the relevant component pins.
    # KiCad joins same-named labels into one net.
    labels = []

    # Per-channel AC nets:
    #   J1x.1 -> Lx_RAW -> Fx.1
    #   Fx.2  -> Lx     -> Kx.COM (pad 3)
    #   J1x.2 -> Nx
    #   J1x.3 -> PE (shared chassis bus)
    #   Kx.NO (pad 2) -> LOADx -> Ux.IP+ (pins 1+2 tied)
    #   Ux.IP- (pins 3+4 tied) -> LOADx_OUT -> J2x.1
    #   J2x.2 -> Nx (neutral passthrough)
    #   Ux.VIOUT (pin 7) -> R15(top 10k) -> I_SENSE_x -> R19(bot 18k) -> GND
    #   Ux.VCC (pin 8) -> +5V, decoupling cap Cx -> GND
    #   Ux.GND (pin 5) -> GND
    #   Ux.FILTER (pin 6) -> NC (left floating)
    for i, row_y in enumerate((60, 115, 160, 205), start=1):
        Lx = f"L{i}"
        Lx_raw = f"L{i}_RAW"
        Nx = f"N{i}"
        loadx = f"LOAD{i}"
        loadx_out = f"LOAD{i}_OUT"
        i_sense_x = f"I_SENSE_{i}"
        # Input terminal block J1x: pins 1=L_RAW, 2=N, 3=PE near (30, row_y..row_y+5)
        labels.append(emit_label(Lx_raw, 35, row_y - 3))     # J1x.1 -> Fx
        labels.append(emit_label(Nx,     35, row_y + 2))     # J1x.2 -> Nx
        labels.append(emit_label("PE",   35, row_y + 7))     # J1x.3 -> PE bus
        # Fuse output side -> Lx (post-fuse switched live)
        labels.append(emit_label(Lx_raw, 55, row_y - 3))     # Fx.1
        labels.append(emit_label(Lx,     68, row_y - 3))     # Fx.2
        # Relay Kx COM (pad 3) tied to Lx; NO (pad 2) -> LOADx (now routed to ACS712)
        labels.append(emit_label(Lx,     90, row_y))         # Kx.COM
        labels.append(emit_label(loadx, 115, row_y))         # Kx.NO -> LOADx
        # ACS712 HV current path: LOADx -> Ux.IP+ (pins 1,2);
        # Ux.IP- (pins 3,4) -> LOADx_OUT -> J2x.1
        u_x, u_y, _ = LAYOUT[f"U{3+i}"]
        # IP+ pins (pin 1,2) are on the LEFT of the symbol (at u_x-10.16, u_y-5.08)
        # IP- pins (pin 3,4) are on the LEFT of the symbol (at u_x-10.16, u_y+5.08)
        labels.append(emit_label(loadx,     u_x - 11, u_y - 5))    # Ux.IP+ side
        labels.append(emit_label(loadx_out, u_x - 11, u_y + 5))    # Ux.IP- side
        # ACS712 LV side: VIOUT (pin 7, right at y-2.54), FILTER (pin 6, right
        # at y+2.54, NC), VCC (pin 8, top), GND (pin 5, bottom).
        # Ux.VIOUT goes to R_top (R15+i-1); R_top other side is I_SENSE_x; R_bot
        # divides I_SENSE_x to GND. ESP32 ADC pin samples I_SENSE_x.
        # The VIOUT-to-R_top segment is left as an UNNAMED local net (KiCad
        # auto-generates a name); the post-divider tap is the named I_SENSE_x.
        labels.append(emit_label("+5V", u_x, u_y - 11))             # Ux.VCC
        labels.append(emit_label("GND", u_x, u_y + 11))             # Ux.GND
        # Decoupling cap Cx (one terminal +5V, other GND) — placed near VCC pin
        cx_ref = f"C{7+i}"
        cx_x, cx_y, _ = LAYOUT[cx_ref]
        labels.append(emit_label("+5V", cx_x, cx_y - 2))
        labels.append(emit_label("GND", cx_x, cx_y + 2))
        # Divider: Ux.VIOUT -> R_top.pin1 (same unnamed local net),
        #          R_top.pin2 -> R_bot.pin1 = I_SENSE_x (named, goes to ADC),
        #          R_bot.pin2 -> GND.
        # We connect VIOUT to R_top.pin1 via a same-named "VIOUT_x" local label;
        # we omit it (no label) so KiCad gives it an auto name. The R_top.pin2
        # and R_bot.pin1 share the I_SENSE_x label which routes to ESP32 ADC.
        r_top_ref = f"R{14+i}"
        r_bot_ref = f"R{18+i}"
        rt_x, rt_y, _ = LAYOUT[r_top_ref]
        rb_x, rb_y, _ = LAYOUT[r_bot_ref]
        # Label R_top.pin1 and Ux.VIOUT with the same local label so they merge.
        vx_raw = f"VOUT_{i}"
        labels.append(emit_label(vx_raw, u_x + 11, u_y))             # Ux.VIOUT
        labels.append(emit_label(vx_raw, rt_x - 2, rt_y))           # R_top.pin1
        # R_top.pin2 -> I_SENSE_x; R_bot.pin1 -> I_SENSE_x; R_bot.pin2 -> GND
        labels.append(emit_label(i_sense_x, rt_x + 2, rt_y))        # R_top.pin2
        labels.append(emit_label(i_sense_x, rb_x - 2, rb_y))        # R_bot.pin1
        labels.append(emit_label("GND",     rb_x + 2, rb_y))        # R_bot.pin2
        # Output terminal J2x: pin1=LOADx_OUT (now from ACS712 IP-), pin2=Nx
        labels.append(emit_label(loadx_out, 138, row_y - 3))     # J2x.1
        labels.append(emit_label(Nx,        138, row_y + 2))     # J2x.2

    # RV1 across L1-N1 (placed at (60, 80))
    labels.append(emit_label("L1", 60, 78))
    labels.append(emit_label("N1", 65, 83))

    # PS1 HLK-PM01 powered from L1, N1 (placed at (100, 80))
    labels.append(emit_label("L1", 100, 78))
    labels.append(emit_label("N1", 105, 83))

    # +5V rail
    for x, y in [(153, 77), (175, 90), (180, 70), (190, 140),
                 (85, 65), (85, 120), (85, 165), (85, 210),
                 (215, 150), (200, 100)]:
        labels.append(emit_label("+5V", x, y))

    # +3V3 rail
    for x, y in [(210, 70), (265, 90), (285, 105), (270, 130),
                 (325, 130), (360, 155), (360, 170), (360, 185), (360, 200),
                 (397, 102)]:
        labels.append(emit_label("+3V3", x, y))

    # GND
    for x, y in [(150, 82), (200, 75), (200, 105), (270, 105), (285, 100),
                 (270, 155), (325, 140), (270, 140), (195, 145),
                 (347, 168), (347, 183), (347, 198), (347, 213),
                 (317, 230), (332, 230),
                 (247, 230), (247, 245), (247, 260), (247, 275),
                 (210, 102), (205, 162), (393, 106)]:
        labels.append(emit_label("GND", x, y))

    # ESP32 control nets — symbolic only (no wires drawn, the labels join up)
    # GPIO -> ULN2003 inputs
    for net, x, y in [
        ("IO16", 285, 140), ("IO16", 205, 155),
        ("IO17", 287, 142), ("IO17", 205, 157),
        ("IO18", 289, 144), ("IO18", 205, 159),
        ("IO19", 291, 146), ("IO19", 205, 161),
    ]:
        labels.append(emit_label(net, x, y))

    # ULN2003 outputs -> relay coil A; coil B is +5V
    for net, xa, ya, xb, yb in [
        ("COIL1", 215, 152, 95,  62),
        ("COIL2", 215, 154, 95, 117),
        ("COIL3", 215, 156, 95, 162),
        ("COIL4", 215, 158, 95, 207),
    ]:
        labels.append(emit_label(net, xa, ya))
        labels.append(emit_label(net, xb, yb))

    # Manual buttons -> GPIO + pullup
    for net, x_btn, y_btn, x_gpio, y_gpio in [
        ("BTN1", 345, 163, 305, 163),
        ("BTN2", 345, 178, 305, 178),
        ("BTN3", 345, 193, 305, 193),
        ("BTN4", 345, 208, 305, 208),
    ]:
        labels.append(emit_label(net, x_btn, y_btn))
        labels.append(emit_label(net, x_gpio, y_gpio))

    # LED control GPIOs
    for net, x_led, y_led, x_gpio, y_gpio in [
        ("LED1", 232, 223, 305, 223),
        ("LED2", 232, 238, 305, 238),
        ("LED3", 232, 253, 305, 253),
        ("LED4", 232, 268, 305, 268),
    ]:
        labels.append(emit_label(net, x_led, y_led))
        labels.append(emit_label(net, x_gpio, y_gpio))

    # EN / BOOT / UART
    for net, x, y in [
        ("EN", 272, 132), ("EN", 315, 228), ("EN", 397, 110),
        ("IO0", 327, 132), ("IO0", 332, 228), ("IO0", 397, 112),
        ("TXD0", 397, 106), ("TXD0", 295, 90),
        ("RXD0", 397, 108), ("RXD0", 297, 90),
    ]:
        labels.append(emit_label(net, x, y))

    # ---- I_SENSE_x labels on ESP32 ADC pins -----------------------------
    # ESP32-WROOM-32E module pin map (verified):
    #   pin 4 = SVP / IO36 / ADC1_CH0
    #   pin 5 = SVN / IO39 / ADC1_CH3
    #   pin 6 = IO34       / ADC1_CH6
    #   pin 7 = IO35       / ADC1_CH7
    # Assignment per spec:
    #   I_SENSE_1 -> IO34 (pin 6), I_SENSE_2 -> IO35 (pin 7),
    #   I_SENSE_3 -> IO36 (pin 4), I_SENSE_4 -> IO39 (pin 5).
    # U1 is at (295, 120) on the schematic; module pin spacing is 1.27mm.
    # Pin 1 is top-left; pins 1..19 down the left side, then 20..38 up the
    # right side. In the .kicad_sym placement these come out at offsets
    # relative to U1 center; we approximate the label coords by relative
    # offsets that match the pin grid.
    # Since the actual symbol layout depends on the lib, we place the labels
    # near U1 (center 295,120) using the same rough scheme used for IO16-19.
    for net, x, y in [
        ("I_SENSE_1", 283, 130),   # IO34 (pin 6)
        ("I_SENSE_2", 283, 132),   # IO35 (pin 7)
        ("I_SENSE_3", 283, 134),   # IO36 (pin 4)
        ("I_SENSE_4", 283, 136),   # IO39 (pin 5)
    ]:
        labels.append(emit_label(net, x, y))

    # ---- ADS1115 voltage-sense ADC (U8) and external ZMPT101B headers ---
    # U8 (ADS1115IDGS) placed at (245, 60) on schematic.
    # IMPORTANT: KiCad's ADS1115IDGS symbol has pin numbers that do NOT
    # match the TI datasheet TSSOP-10 pin numbering directly. The symbol
    # assigns the following pin->function mapping (verified by inspecting
    # Analog_ADC.kicad_sym in KiCad 9):
    #   pin 1=ADDR, 2=ALERT/RDY, 3=GND, 4=AIN0, 5=AIN1, 6=AIN2, 7=AIN3,
    #   pin 8=VDD, 9=SDA, 10=SCL.
    # These bind via lib_id to the TSSOP-10_3x3mm_P0.5mm footprint pads
    # (which are simply numbered 1..10 in standard TSSOP order). The PCB
    # script (add_voltage_sense.py) assigns nets to the *footprint pad*
    # numbers using the symbol's mapping above, so symbol pin functions and
    # PCB pad nets stay consistent.
    # Pin positions (from the ADS1015IDGS_1_1 parent symbol, units mm,
    # relative to U8 center at (245, 60)):
    #   pin 4 AIN0  at (-10.16,  2.54) -> (234.84, 62.54)
    #   pin 5 AIN1  at (-10.16,  0)    -> (234.84, 60.00)
    #   pin 6 AIN2  at (-10.16, -2.54) -> (234.84, 57.46)
    #   pin 7 AIN3  at (-10.16, -5.08) -> (234.84, 54.92)
    #   pin 8 VDD   at (  0,    12.7)  -> (245.00, 72.70)
    #   pin 3 GND   at (  0,   -10.16) -> (245.00, 49.84)
    #   pin 2 ALERT at ( 10.16,  5.08) -> (255.16, 65.08)  # NC, no label
    #   pin 10 SCL  at ( 10.16,  0)    -> (255.16, 60.00)
    #   pin 9 SDA   at ( 10.16, -2.54) -> (255.16, 57.46)
    #   pin 1 ADDR  at ( 10.16, -5.08) -> (255.16, 54.92)
    for net, x, y in [
        ("V_SENSE_1", 233, 62),
        ("V_SENSE_2", 233, 60),
        ("V_SENSE_3", 233, 58),
        ("V_SENSE_4", 233, 55),
        ("+3V3",      245, 73),     # U8 pin 8 VDD
        ("GND",       245, 50),     # U8 pin 3 GND
        ("SCL_LCD",   256, 60),     # U8 pin 10 SCL
        ("SDA_LCD",   256, 58),     # U8 pin 9 SDA
        ("GND",       256, 55),     # U8 pin 1 ADDR -> GND (sets addr 0x48)
        # ALERT/RDY pin 2 intentionally left floating (no label)
        # C16 100nF decoupling near U8 VDD/GND
        ("+3V3",      230, 58),     # C16 pin 1
        ("GND",       230, 62),     # C16 pin 2
        # External ZMPT101B headers J5-J8 — each header: GND/+3V3/V_SENSE_x
        # Conn_01x03 pins are on the left side of the symbol; offset labels
        # slightly to the left of each header's pin positions.
        ("GND",       390, 130),    # J5 pin 1
        ("+3V3",      390, 133),    # J5 pin 2
        ("V_SENSE_1", 390, 135),    # J5 pin 3
        ("GND",       390, 150),    # J6 pin 1
        ("+3V3",      390, 153),    # J6 pin 2
        ("V_SENSE_2", 390, 155),    # J6 pin 3
        ("GND",       390, 170),    # J7 pin 1
        ("+3V3",      390, 173),    # J7 pin 2
        ("V_SENSE_3", 390, 175),    # J7 pin 3
        ("GND",       390, 190),    # J8 pin 1
        ("+3V3",      390, 193),    # J8 pin 2
        ("V_SENSE_4", 390, 195),    # J8 pin 3
    ]:
        labels.append(emit_label(net, x, y))

    # ---- LCD I2C nets (SDA_LCD on IO13, SCL_LCD on IO14) ----------------
    # External 16x2 LCD via PCF8574 backpack on connector J4. The two
    # pull-ups (R13 on SDA, R14 on SCL) tie each I2C line to +3V3.
    # Labels join ESP32 IO13/IO14 -> pull-up resistor -> J4 header pins.
    # J4 is at (395, 60); pin pitch 2.54 -> pin 1..4 stacked vertically.
    # R13 at (370, 50), R14 at (370, 60).
    for net, x, y in [
        # ESP32 side (near U1 module, IO13/IO14 nominal positions)
        ("SDA_LCD", 305, 105), ("SCL_LCD", 305, 107),
        # Pull-up resistor R13 (SDA <-> +3V3)
        ("SDA_LCD", 367, 50), ("+3V3", 373, 50),
        # Pull-up resistor R14 (SCL <-> +3V3)
        ("SCL_LCD", 367, 60), ("+3V3", 373, 60),
        # J4 header pin 3 = SDA_LCD, pin 4 = SCL_LCD
        # J4 placed at (395, 60); pin labels distributed vertically
        ("GND", 397, 58),       # J4 pin 1
        ("+5V", 397, 60),       # J4 pin 2
        ("SDA_LCD", 397, 62),   # J4 pin 3
        ("SCL_LCD", 397, 64),   # J4 pin 4
    ]:
        labels.append(emit_label(net, x, y))

    # ---- Assemble final file ----------------------------------------------
    parts_str = "".join(inst_blocks) + "".join(power_instances)
    lib_syms_str = "\n".join(lib_symbols_blocks)
    labels_str = "".join(labels)

    out = f'''(kicad_sch
\t(version 20231120)
\t(generator "eeschema")
\t(generator_version "8.0")
\t(uuid "{ROOT_UUID}")
\t(paper "A3")
\t(title_block
\t\t(title "Smart Home 4-Channel AC Relay Controller")
\t\t(date "2026-05-26")
\t\t(rev "0.1")
\t\t(company "WisRight Technologies Private Limited")
\t\t(comment 1 "ESP32-WROOM-32E + HLK-PM01 + 4x SRD-05VDC relays")
\t\t(comment 2 "230VAC input, 4 switched AC load outputs, UART programming header")
\t\t(comment 3 "Manual override buttons + status LEDs per channel")
\t\t(comment 4 "")
\t)
\t(lib_symbols
{lib_syms_str}
\t)
{parts_str}{labels_str}\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
)
'''

    SCH_PATH.write_text(out, encoding="utf-8")
    size = SCH_PATH.stat().st_size
    print(f"Wrote {SCH_PATH}  ({size} bytes, {len(inst_blocks)} components, "
          f"{len(lib_ids_all)} lib_symbols, {len(labels)} labels)")

    # Quick syntactic sanity: balanced parens (string-aware).
    text = out
    depth = 0
    in_str = False
    esc = False
    for c in text:
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
    print(f"Paren balance check: depth={depth} (must be 0)")
    if depth != 0:
        raise SystemExit("Unbalanced parens in generated file")


if __name__ == "__main__":
    main()
