![til](./banner.png)

# Via Stitching — KiCad Plugin - Carpet Bomber

A KiCad `ActionPlugin` that fills selected copper zones or footprint areas
with a grid of stitching vias on a chosen net — similar to Altium
Designer's **"Add Stitching to Net"** tool. Unlike a naive grid fill, it
actively avoids placing vias on top of pads, tracks, vias, or zones that
belong to a *different* net.

---

## What it does

1. You select one or more copper **zones** and/or **footprints** on the
   board.
2. You run the plugin and configure grid, via, and net settings in a
   dialog.
3. The plugin lays out a regular grid of candidate via positions across
   the selected area(s), tests each candidate against the surrounding
   copper, and places a via only where it's actually clear — skipping
   points that would short into a foreign net or land inside a keep-out
   region.

The result is a stitching-via pattern that respects your existing
routing, rather than a blind array of vias dropped over the whole
bounding box.

---

## Features

### Net-aware collision avoidance
- Every candidate via position is checked against nearby **pads**,
  **tracks/vias**, and **zones** using KiCad's own hit-testing
  (`HitTest`, `HitTestFilledArea`), expanded by the via's radius plus
  your configured clearance — a true geometric check, not just a
  net-code comparison.
- Objects on the **target net** are ignored (vias are allowed to sit on
  or near their own net), everything else is treated as an obstacle.
- **Keep-out zones** (rule areas with vias disallowed) are respected.
- For zone selections, placement is constrained to the zone's actual
  **polygon outline**, not just its rectangular bounding box, so vias
  won't spill outside an irregularly shaped pour.
- Obstacles are pre-filtered by bounding-box overlap before the grid
  scan, so the tool doesn't re-check the entire board for every
  candidate point.

### Configurable placement grid
- **Grid spacing** (mm)
- **X/Y offset** — shift the whole grid relative to the area's origin
- **Stagger alternate rows** — offsets every other row by half a pitch
  for a denser hex-like pattern
- **Clearance to other nets** (mm) — extra spacing kept from foreign
  copper, pre-filled from the board's default design-rule clearance
- **Boundary inset** (mm) — shrinks the usable area inward from the
  zone/footprint edge before the grid is generated
- **Remove existing same-net vias in area first** — clears previously
  placed stitching vias belonging to the target net before laying down
  a fresh grid, so you can re-run the tool after editing the board
  without leaving stale vias behind

### Via style
- **Diameter** and **drill** (mm)
- **Top/bottom copper layer** selection (defaults to the outer layers,
  but any enabled copper layer pair can be chosen)
- Vias are created as standard **through vias**

### Net & properties
- **Net** field with autocomplete against all nets already on the board;
  if the named net doesn't exist yet, it's created
- **Locked** — optionally lock the generated vias so they aren't moved
  by autorouters or accidental drags

---

## Usage

1. In KiCad's PCB editor, select the copper zone(s) and/or footprint(s)
   you want stitched.
2. Run **Carpet Bomber** from the Routing Tools plugin menu / toolbar
   button.
3. Fill in the dialog:
   - **Grid & Placement** — spacing, offset, stagger, clearance,
     boundary inset, remove-existing option
   - **Via Style** — diameter, drill, top/bottom layer
   - **Properties** — target net, locked
4. Click **OK**. A summary dialog reports how many vias were created and
   how many candidate grid points were skipped due to nearby foreign
   copper or keep-outs.

If nothing is selected, or the area is too small once the boundary
inset is applied, the plugin reports the problem and takes no action.

---

## How the collision check works

For each candidate grid point:

1. **Containment** — is the point inside the selected zone's polygon
   (or the footprint's bounding box, after the boundary inset)?
2. **Obstacle check** — expand the via by its radius plus the
   configured clearance and test that disc against:
   - Pads not on the target net
   - Tracks/vias not on the target net
   - Zones not on the target net, and any rule-area keep-outs that
     disallow vias
3. If the point passes both checks, a via is created there and added to
   the board on the target net, using the chosen diameter, drill, and
   layer pair.

Because obstacles are matched by **net code**, vias are allowed to touch
or overlap same-net copper (which is the point of stitching), while
anything belonging to a different net — or nothing at all in the case of
unconnected pads/tracks — is treated as something to avoid.

---

## Known limitations

- **Footprint areas** use the footprint's bounding box rather than a
  courtyard-aware or pad-array-aware shape, so a very irregular
  footprint outline may be under- or over-approximated.
- **Board edge / outline** is not currently checked — vias are only
  constrained by the selected zone/footprint area, not by the board's
  Edge.Cuts outline. If a selected area extends past the board edge,
  vias could be placed off-board.
- **Zone fill freshness** — `HitTestFilledArea` relies on the zone's
  filled polygons being up to date. If zones haven't been re-filled
  since the last edit, run a zone refill before stitching for accurate
  results.
- Rectangular grid only (no hex/triangular grid beyond the "stagger"
  option).

These are reasonable next steps if closer parity with Altium's tool is
needed.

---

## Compatibility

Written against the KiCad 7/8 `pcbnew` Python API. A few calls are
wrapped in `try/except` because some `SHAPE_POLY_SET` / `ZONE` helper
signatures changed between KiCad versions; where a helper isn't
available, the code falls back to a coarser check (e.g. bounding-box
containment instead of exact polygon containment) rather than failing
outright.

Requires `wxPython` (bundled with KiCad's scripting environment) for the
settings dialog; if `wx` isn't available, the plugin will still run from
the KiCad Python console but without the interactive dialog.

---

## Files

| File                    | Purpose                                   |
|-------------------------|--------------------------------------------|
| `via_carpet_bomber.py`  | Plugin implementation (`ViaStitcherPlugin`) |
| `README.md`             | This file                                  |

## Installation

Place the files in the fallowing directories:
### Windows Path:
`%APPDATA%\kicad\10.0\plugins\`
### Linux Path:
`~/.local/share/kicad/10.0/plugins/`
