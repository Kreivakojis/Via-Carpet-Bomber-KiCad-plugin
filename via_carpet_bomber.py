"""
Via Carpet Bomber - KiCad ActionPlugin
---------------------------------
Fills selected copper zones / footprint areas with a grid of stitching vias
on a chosen net, while avoiding pads, tracks, vias and zones that belong to
a *different* net (similar in spirit to Altium Designer's "Add Stitching to
Net" tool).

Key behaviour:
  * Vias are placed on a regular grid (spacing + X/Y offset, optional
    staggered rows) instead of an arbitrary fill.
  * Before a via is placed, its footprint (diameter + clearance) is tested
    against every pad / track / via / zone on the board. If it would land
    on, or too close to, an object belonging to a different net (or a
    keep-out area), that grid point is skipped.
  * Footprint solder pads get dedicated treatment: pads are collected by
    walking every footprint's own pad list (rather than relying on a single
    board-wide pad accessor), so pad avoidance is robust across pcbnew API
    versions and always covers every component on the board, including the
    one(s) selected for stitching. Pads on foreign nets and pads on the
    via's own net are avoided independently (both default on) -- a via
    should not normally land directly on top of any existing pad, even one
    that shares its net, since that drives the via drill straight through
    the pad's copper. An additional, separate "extra clearance from pads"
    setting lets pads be kept further away than other copper if desired.
  * If the selected area is a zone, placement is constrained to the zone's
    actual **filled copper shape** (post-fill, accounting for pad
    clearances, thermal reliefs, and clearance to other copper/board edge)
    rather than just the raw outline the zone was drawn with, so vias can't
    land in a spot that's inside the drawn boundary but isn't actually
    copper once filled. Falls back to the drawn outline if the zone hasn't
    been filled yet.
  * A larger set of parameters is exposed in the dialog: grid spacing,
    offset, stagger, clearance, pad-specific clearance, boundary inset, via
    diameter/drill, copper layer pair, target net, "lock via" and "remove
    existing same-net vias in area first".

Tested against the KiCad 7/8 pcbnew API. A few calls are wrapped in
try/except because some SHAPE_POLY_SET / ZONE helper methods changed
signature between KiCad releases; where a helper isn't available the code
degrades gracefully (e.g. falls back to bounding-box containment instead of
exact polygon containment).
"""

import sys
import os
import traceback

if 'pcbnew' in sys.modules:
    pcbnew = sys.modules['pcbnew']
else:
    import pcbnew

try:
    import wx
except ImportError:
    wx = None


MM_TO_NM = 1_000_000


def mm2nm(value_mm):
    return int(round(value_mm * MM_TO_NM))


def nm2mm(value_nm):
    return value_nm / MM_TO_NM


class ViaStitcherPlugin(pcbnew.ActionPlugin):

    def defaults(self):
        self.name = "Via Carpet Bomber"
        self.category = "Routing Tools"
        self.description = "Auto-fill copper areas with stitching vias, avoiding foreign nets"
        self.show_toolbar_button = True
        try:
            self.icon_file_name = os.path.join(os.path.dirname(__file__), 'icon.png')
        except NameError:
            self.icon_file_name = ""

    # ------------------------------------------------------------------ #
    # UI helpers
    # ------------------------------------------------------------------ #

    def show_msg(self, message, is_error=False):
        if not wx:
            print(message)
            return
        try:
            parent = wx.GetActiveWindow()
        except Exception:
            parent = None
        title = "Carpet Bomb - Error" if is_error else "Carpet Bomber"
        style = wx.OK | (wx.ICON_ERROR if is_error else wx.ICON_INFORMATION)
        try:
            wx.MessageBox(message, title, style, parent)
        except Exception:
            print(f"[{title}] {message}")

    def _get_copper_layers(self, board):
        """Return list of (layer_id, layer_name) for enabled copper layers,
        ordered top -> bottom."""
        layers = []
        try:
            stack = board.GetEnabledLayers().CuStack()
        except Exception:
            stack = [pcbnew.F_Cu, pcbnew.B_Cu]
        for lid in stack:
            try:
                name = board.GetLayerName(lid)
            except Exception:
                name = str(lid)
            layers.append((lid, name))
        if not layers:
            layers = [(pcbnew.F_Cu, "F.Cu"), (pcbnew.B_Cu, "B.Cu")]
        return layers

    def _default_clearance_mm(self, board):
        try:
            return nm2mm(board.GetDesignSettings().GetDefaultClearance())
        except Exception:
            return 0.2

    def get_params(self, board):
        if not wx:
            return None

        parent = wx.GetActiveWindow()
        dlg = wx.Dialog(parent, title="Via Carpet Bomber", style=wx.DEFAULT_DIALOG_STYLE)
        outer = wx.BoxSizer(wx.VERTICAL)
        columns = wx.BoxSizer(wx.HORIZONTAL)

        def labeled_grid():
            g = wx.FlexGridSizer(0, 2, 8, 10)
            g.AddGrowableCol(1, 1)
            return g

        # --- Grid & placement -------------------------------------------------
        grid_box = wx.StaticBoxSizer(wx.VERTICAL, dlg, "Grid && Placement")
        g1 = labeled_grid()

        g1.Add(wx.StaticText(dlg, label="Grid spacing (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        spacing_ctrl = wx.TextCtrl(dlg, value="2.54")
        g1.Add(spacing_ctrl, 0, wx.EXPAND)

        g1.Add(wx.StaticText(dlg, label="Offset X (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        offx_ctrl = wx.TextCtrl(dlg, value="0")
        g1.Add(offx_ctrl, 0, wx.EXPAND)

        g1.Add(wx.StaticText(dlg, label="Offset Y (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        offy_ctrl = wx.TextCtrl(dlg, value="0")
        g1.Add(offy_ctrl, 0, wx.EXPAND)

        grid_box.Add(g1, 0, wx.ALL | wx.EXPAND, 8)

        stagger_ctrl = wx.CheckBox(dlg, label="Stagger alternate rows")
        stagger_ctrl.SetValue(True)
        grid_box.Add(stagger_ctrl, 0, wx.LEFT | wx.BOTTOM, 8)

        g1b = labeled_grid()
        g1b.Add(wx.StaticText(dlg, label="Clearance to other nets (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        clearance_ctrl = wx.TextCtrl(dlg, value=f"{self._default_clearance_mm(board):.3f}")
        g1b.Add(clearance_ctrl, 0, wx.EXPAND)

        g1b.Add(wx.StaticText(dlg, label="Boundary inset (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        boundary_ctrl = wx.TextCtrl(dlg, value="0")
        g1b.Add(boundary_ctrl, 0, wx.EXPAND)

        g1b.Add(wx.StaticText(dlg, label="Extra clearance from pads (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        pad_clearance_ctrl = wx.TextCtrl(dlg, value="0")
        g1b.Add(pad_clearance_ctrl, 0, wx.EXPAND)
        grid_box.Add(g1b, 0, wx.ALL | wx.EXPAND, 8)

        avoid_pads_ctrl = wx.CheckBox(dlg, label="Avoid footprint pads of other nets")
        avoid_pads_ctrl.SetValue(True)
        grid_box.Add(avoid_pads_ctrl, 0, wx.LEFT | wx.BOTTOM, 8)

        avoid_same_net_pads_ctrl = wx.CheckBox(dlg, label="Also avoid pads on the via's own net")
        avoid_same_net_pads_ctrl.SetValue(True)
        grid_box.Add(avoid_same_net_pads_ctrl, 0, wx.LEFT | wx.BOTTOM, 8)

        remove_existing_ctrl = wx.CheckBox(dlg, label="Remove existing same-net vias in area first")
        grid_box.Add(remove_existing_ctrl, 0, wx.LEFT | wx.BOTTOM, 8)

        # --- Via style ----------------------------------------------------
        style_box = wx.StaticBoxSizer(wx.VERTICAL, dlg, "Via Style")
        g2 = labeled_grid()

        g2.Add(wx.StaticText(dlg, label="Diameter (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        dia_ctrl = wx.TextCtrl(dlg, value="0.6")
        g2.Add(dia_ctrl, 0, wx.EXPAND)

        g2.Add(wx.StaticText(dlg, label="Drill (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        drill_ctrl = wx.TextCtrl(dlg, value="0.3")
        g2.Add(drill_ctrl, 0, wx.EXPAND)

        layers = self._get_copper_layers(board)
        layer_names = [name for _, name in layers]

        g2.Add(wx.StaticText(dlg, label="Top layer:"), 0, wx.ALIGN_CENTER_VERTICAL)
        top_layer_ctrl = wx.Choice(dlg, choices=layer_names)
        top_layer_ctrl.SetSelection(0)
        g2.Add(top_layer_ctrl, 0, wx.EXPAND)

        g2.Add(wx.StaticText(dlg, label="Bottom layer:"), 0, wx.ALIGN_CENTER_VERTICAL)
        bottom_layer_ctrl = wx.Choice(dlg, choices=layer_names)
        bottom_layer_ctrl.SetSelection(len(layer_names) - 1)
        g2.Add(bottom_layer_ctrl, 0, wx.EXPAND)

        style_box.Add(g2, 0, wx.ALL | wx.EXPAND, 8)

        # --- Properties -----------------------------------------------------
        props_box = wx.StaticBoxSizer(wx.VERTICAL, dlg, "Properties")
        g3 = labeled_grid()

        net_names = sorted(
            n.GetNetname() for n in board.GetNetsByName().values() if n.GetNetname()
        )
        g3.Add(wx.StaticText(dlg, label="Net:"), 0, wx.ALIGN_CENTER_VERTICAL)
        net_ctrl = wx.ComboBox(dlg, value="GND", choices=net_names)
        g3.Add(net_ctrl, 0, wx.EXPAND)

        # Type-ahead filtering: as the user types, narrow the dropdown to
        # nets whose name contains what's been typed so far, so finding a
        # net doesn't require scrolling a long list. The text field itself
        # still accepts anything typed (including a brand-new net name),
        # this only affects which items are offered in the dropdown.
        net_filter_state = {"updating": False}

        def _on_net_text(event):
            if net_filter_state["updating"]:
                event.Skip()
                return
            typed = net_ctrl.GetValue()
            insertion_point = net_ctrl.GetInsertionPoint()
            matches = (
                [n for n in net_names if typed.lower() in n.lower()] if typed else net_names
            )
            net_filter_state["updating"] = True
            try:
                net_ctrl.Set(matches)
                net_ctrl.SetValue(typed)
                net_ctrl.SetInsertionPoint(insertion_point)
            finally:
                net_filter_state["updating"] = False
            try:
                if typed and matches:
                    net_ctrl.Popup()
            except Exception:
                pass  # Popup() isn't available on every platform/wx build.
            event.Skip()

        net_ctrl.Bind(wx.EVT_TEXT, _on_net_text)

        props_box.Add(g3, 0, wx.ALL | wx.EXPAND, 8)

        locked_ctrl = wx.CheckBox(dlg, label="Locked")
        props_box.Add(locked_ctrl, 0, wx.LEFT | wx.BOTTOM, 8)

        left_col = wx.BoxSizer(wx.VERTICAL)
        left_col.Add(grid_box, 0, wx.EXPAND | wx.ALL, 5)

        right_col = wx.BoxSizer(wx.VERTICAL)
        right_col.Add(style_box, 0, wx.EXPAND | wx.ALL, 5)
        right_col.Add(props_box, 0, wx.EXPAND | wx.ALL, 5)

        columns.Add(left_col, 0, wx.EXPAND)
        columns.Add(right_col, 0, wx.EXPAND)

        outer.Add(columns, 1, wx.EXPAND | wx.ALL, 10)
        btn_sizer = dlg.CreateButtonSizer(wx.OK | wx.CANCEL)
        outer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        dlg.SetSizerAndFit(outer)
        dlg.Center()

        if dlg.ShowModal() == wx.ID_OK:
            try:
                spacing = float(spacing_ctrl.GetValue().strip())
                offset_x = float(offx_ctrl.GetValue().strip() or 0)
                offset_y = float(offy_ctrl.GetValue().strip() or 0)
                clearance = float(clearance_ctrl.GetValue().strip() or 0)
                boundary = float(boundary_ctrl.GetValue().strip() or 0)
                pad_clearance = float(pad_clearance_ctrl.GetValue().strip() or 0)
                dia = float(dia_ctrl.GetValue().strip())
                drill = float(drill_ctrl.GetValue().strip())
                net_name = net_ctrl.GetValue().strip()

                if spacing <= 0 or dia <= 0 or drill <= 0:
                    raise ValueError("Spacing / diameter / drill must be positive")
                if drill >= dia:
                    raise ValueError("Drill must be smaller than diameter")
                if clearance < 0 or boundary < 0 or pad_clearance < 0:
                    raise ValueError("Clearance / boundary inset can't be negative")
                if not net_name:
                    raise ValueError("Net name can't be empty")

                top_layer_id = layers[top_layer_ctrl.GetSelection()][0]
                bottom_layer_id = layers[bottom_layer_ctrl.GetSelection()][0]

                return dict(
                    spacing_mm=spacing,
                    offset_x_mm=offset_x,
                    offset_y_mm=offset_y,
                    stagger=stagger_ctrl.GetValue(),
                    clearance_mm=clearance,
                    boundary_mm=boundary,
                    pad_clearance_mm=pad_clearance,
                    avoid_foreign_pads=avoid_pads_ctrl.GetValue(),
                    avoid_same_net_pads=avoid_same_net_pads_ctrl.GetValue(),
                    remove_existing=remove_existing_ctrl.GetValue(),
                    dia_mm=dia,
                    drill_mm=drill,
                    top_layer=top_layer_id,
                    bottom_layer=bottom_layer_id,
                    net_name=net_name,
                    locked=locked_ctrl.GetValue(),
                )
            except ValueError as e:
                self.show_msg(f"Invalid input: {e}", is_error=True)
        return None

    # ------------------------------------------------------------------ #
    # Geometry / collision helpers
    # ------------------------------------------------------------------ #

    def _inset_bbox(self, bbox, inset_nm):
        """Shrink (positive inset_nm) or grow (negative inset_nm) a BOX2I
        by the given amount on all four sides.

        Built via the BOX2I(pos, size) constructor rather than
        SetOrigin()/SetSize() separately: on some KiCad/SWIG versions
        SetSize() rejects a VECTOR2I argument (expects two plain ints),
        while the two-argument constructor is accepted consistently.
        """
        x0 = bbox.GetX() + inset_nm
        y0 = bbox.GetY() + inset_nm
        x1 = bbox.GetX() + bbox.GetWidth() - inset_nm
        y1 = bbox.GetY() + bbox.GetHeight() - inset_nm
        if x1 <= x0 or y1 <= y0:
            return None
        return pcbnew.BOX2I(
            pcbnew.VECTOR2I(int(x0), int(y0)),
            pcbnew.VECTOR2I(int(x1 - x0), int(y1 - y0)),
        )

    def _shrink_zone_outline(self, outline, inset_nm):
        if inset_nm <= 0:
            return outline
        try:
            shrunk = pcbnew.SHAPE_POLY_SET(outline)
            try:
                shrunk.Deflate(inset_nm, 16)
            except TypeError:
                shrunk.Inflate(-inset_nm, 16)
            return shrunk
        except Exception:
            # Fall back to the un-inset outline; boundary inset is best-effort.
            return outline

    def _zone_fill_polygon(self, zone):
        """Return the polygon to test via placement against for this zone.

        `zone.Outline()` is the shape as originally *drawn* -- it knows
        nothing about pad clearances, thermal-relief cutouts, or clearance
        to the board edge / other zones. Testing against it can let a via
        land at a spot that is inside the drawn outline but has no actual
        copper there once the zone is filled (e.g. right next to a
        different-net pad's clearance halo), which looks like the via
        "spilled outside the zone".

        `zone.GetFilledPolysList(layer)` is the real, post-fill copper
        shape and is what we want to test against. We union it across
        every copper layer the zone lives on. If the zone hasn't been
        filled yet (or the filled-polygon API isn't available on this
        KiCad version), we fall back to the raw outline.
        """
        try:
            combined = pcbnew.SHAPE_POLY_SET()
            found_fill = False
            for layer in zone.GetLayerSet().Seq():
                try:
                    polys = zone.GetFilledPolysList(layer)
                except TypeError:
                    polys = zone.GetFilledPolysList()
                if polys is None:
                    continue
                try:
                    has_outlines = polys.OutlineCount() > 0
                except Exception:
                    has_outlines = not polys.IsEmpty()
                if has_outlines:
                    combined.Append(polys)
                    found_fill = True
            if found_fill:
                try:
                    combined.Simplify()
                except Exception:
                    pass
                return combined
        except Exception:
            pass
        # Zone not filled (or API unavailable) -- fall back to the drawn
        # outline; make sure zones are re-filled before stitching for the
        # most accurate results.
        return zone.Outline()

    def _collect_pads_to_avoid(
        self, board, bbox_expanded, target_net_code, avoid_foreign, avoid_same_net
    ):
        """Gather solder pads (of any footprint) that should be treated as
        obstacles, whose bounding box is near the working area.

        `avoid_foreign` controls whether pads on nets other than the target
        net are avoided; `avoid_same_net` controls whether pads that ARE on
        the target net are also avoided. The latter matters because placing
        a via's drill straight through an existing component pad is a
        manufacturability problem even when they share a net.

        Pads are walked via each footprint's own `Pads()` list rather than
        `board.GetPads()`: this is the more universally-supported path
        across pcbnew API versions and guarantees every footprint on the
        board is covered -- including the one(s) the user selected, so a
        component's own pins are correctly avoided when stitching thermal
        vias under/around it.
        """
        if not avoid_foreign and not avoid_same_net:
            return []

        def wanted(pad):
            same_net = pad.GetNetCode() == target_net_code
            return avoid_same_net if same_net else avoid_foreign

        pads = []
        try:
            footprints = board.GetFootprints()
        except Exception:
            footprints = []

        if footprints:
            for fp in footprints:
                try:
                    fp_pads = fp.Pads()
                except Exception:
                    continue
                for pad in fp_pads:
                    if not wanted(pad):
                        continue
                    try:
                        if bbox_expanded.Intersects(pad.GetBoundingBox()):
                            pads.append(pad)
                    except Exception:
                        pads.append(pad)
        else:
            # Fallback for API variants without GetFootprints()/Pads().
            try:
                board_pads = board.GetPads()
            except Exception:
                board_pads = []
            for pad in board_pads:
                if not wanted(pad):
                    continue
                try:
                    if bbox_expanded.Intersects(pad.GetBoundingBox()):
                        pads.append(pad)
                except Exception:
                    pads.append(pad)

        return pads

    def _collect_obstacles(
        self, board, bbox_expanded, target_net_code, avoid_foreign_pads=True, avoid_same_net_pads=True
    ):
        """Pre-filter board objects that are near the working area, so the
        per-point checks below only test a small, relevant subset instead
        of the whole board."""
        pads = self._collect_pads_to_avoid(
            board, bbox_expanded, target_net_code, avoid_foreign_pads, avoid_same_net_pads
        )
        tracks, zones = [], []

        for trk in board.GetTracks():
            if trk.GetNetCode() == target_net_code:
                continue
            try:
                if bbox_expanded.Intersects(trk.GetBoundingBox()):
                    tracks.append(trk)
            except Exception:
                tracks.append(trk)

        for zone in board.Zones():
            if zone.GetNetCode() == target_net_code and not zone.GetIsRuleArea():
                continue
            try:
                if bbox_expanded.Intersects(zone.GetBoundingBox()):
                    zones.append(zone)
            except Exception:
                zones.append(zone)

        return pads, tracks, zones

    def _point_clear(self, pt, clear_nm, pad_clear_nm, pads, tracks, zones):
        x, y = int(pt[0]), int(pt[1])
        point = pcbnew.VECTOR2I(x, y)

        for pad in pads:
            try:
                if pad.HitTest(point, pad_clear_nm):
                    return False
            except TypeError:
                if pad.HitTest(point):
                    return False

        for trk in tracks:
            try:
                if trk.HitTest(point, clear_nm):
                    return False
            except TypeError:
                if trk.HitTest(point):
                    return False

        for zone in zones:
            try:
                is_keepout = zone.GetIsRuleArea() and (
                    not hasattr(zone, "GetDoNotAllowVias") or zone.GetDoNotAllowVias()
                )
            except Exception:
                is_keepout = False
            try:
                hit = False
                for layer in zone.GetLayerSet().Seq():
                    if zone.HitTestFilledArea(layer, point, clear_nm):
                        hit = True
                        break
                if hit and (is_keepout or zone.GetNetCode() != 0):
                    return False
            except Exception:
                # Older API without accuracy param / filled polys not built.
                try:
                    if zone.GetBoundingBox().Contains(point):
                        return False
                except Exception:
                    pass

        return True

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def Run(self):
        try:
            board = pcbnew.GetBoard()
            if not board:
                self.show_msg("No open PCB board.", is_error=True)
                return

            zones_sel = [z for z in board.Zones() if z.IsSelected()]
            footprints_sel = [f for f in board.GetFootprints() if f.IsSelected()]

            if not zones_sel and not footprints_sel:
                self.show_msg("Select a copper zone or footprint first.", is_error=True)
                return

            params = self.get_params(board)
            if not params:
                return

            spacing_nm = mm2nm(params["spacing_mm"])
            offset_x_nm = mm2nm(params["offset_x_mm"])
            offset_y_nm = mm2nm(params["offset_y_mm"])
            clearance_nm = mm2nm(params["clearance_mm"])
            boundary_nm = mm2nm(params["boundary_mm"])
            pad_clearance_nm = mm2nm(params["pad_clearance_mm"])
            dia_nm = mm2nm(params["dia_mm"])
            drill_nm = mm2nm(params["drill_mm"])
            via_radius_nm = dia_nm // 2
            required_clear_nm = via_radius_nm + clearance_nm
            required_pad_clear_nm = via_radius_nm + clearance_nm + pad_clearance_nm

            # Resolve / create the target net.
            target_net = None
            for n in board.GetNetsByName().values():
                if n.GetNetname().upper() == params["net_name"].upper():
                    target_net = n
                    break
            if target_net is None:
                target_net = pcbnew.NETINFO_ITEM(board, params["net_name"])
                board.Add(target_net)
            via_net_code = target_net.GetNetCode()

            # Build the list of working areas: exact filled-copper polygon
            # for zones, bounding box for footprints. Each area also keeps
            # its raw (pre-boundary-inset) bounding box, expanded by a
            # generous margin, purely for the "remove existing vias" sweep
            # below -- that way vias left over from a previous run (e.g.
            # with a different boundary inset, or ones that spilled out due
            # to a since-fixed bug) still get cleaned up even if they sit
            # slightly outside this run's placement area.
            removal_margin_nm = max(spacing_nm, required_pad_clear_nm)
            areas = []
            for z in zones_sel:
                fill_poly = self._zone_fill_polygon(z)
                outline = self._shrink_zone_outline(fill_poly, boundary_nm)
                bbox = self._inset_bbox(z.GetBoundingBox(), boundary_nm)
                if bbox is None:
                    continue
                raw_bbox = self._inset_bbox(z.GetBoundingBox(), -removal_margin_nm) or z.GetBoundingBox()
                areas.append({"type": "zone", "outline": outline, "bbox": bbox, "raw_bbox": raw_bbox})
            for fp in footprints_sel:
                bbox = self._inset_bbox(fp.GetBoundingBox(), boundary_nm)
                if bbox is None:
                    continue
                raw_bbox = self._inset_bbox(fp.GetBoundingBox(), -removal_margin_nm) or fp.GetBoundingBox()
                areas.append({"type": "footprint", "bbox": bbox, "raw_bbox": raw_bbox})

            if not areas:
                self.show_msg(
                    "Selected area is too small once the boundary inset is applied.",
                    is_error=True,
                )
                return

            # Optionally clear out previously-placed same-net vias in these
            # areas so re-running the tool regenerates a clean grid. Uses
            # each area's raw (expanded) bbox rather than the current run's
            # placement bbox -- see comment above.
            if params["remove_existing"]:
                to_remove = []
                for trk in board.GetTracks():
                    if not isinstance(trk, pcbnew.PCB_VIA):
                        continue
                    if trk.GetNetCode() != via_net_code:
                        continue
                    pos = trk.GetPosition()
                    for area in areas:
                        if area["raw_bbox"].Contains(pos):
                            to_remove.append(trk)
                            break
                for trk in to_remove:
                    board.Remove(trk)

            created = 0
            skipped = 0

            for area in areas:
                bbox = area["bbox"]
                search_margin_nm = max(required_clear_nm, required_pad_clear_nm)
                expanded_bbox = self._inset_bbox(bbox, -search_margin_nm)
                if expanded_bbox is None:
                    expanded_bbox = bbox
                pads, tracks, zones_nearby = self._collect_obstacles(
                    board, expanded_bbox, via_net_code,
                    avoid_foreign_pads=params["avoid_foreign_pads"],
                    avoid_same_net_pads=params["avoid_same_net_pads"],
                )

                x0, y0 = bbox.GetX(), bbox.GetY()
                x1, y1 = x0 + bbox.GetWidth(), y0 + bbox.GetHeight()

                row = 0
                y = y0 + spacing_nm // 2 + offset_y_nm
                while y < y1:
                    row_offset = (spacing_nm // 2) if (params["stagger"] and row % 2 == 1) else 0
                    x = x0 + spacing_nm // 2 + offset_x_nm + row_offset
                    while x < x1:
                        if area["type"] == "zone":
                            inside = area["outline"].Contains(pcbnew.VECTOR2I(int(x), int(y)))
                        else:
                            inside = bbox.Contains(pcbnew.VECTOR2I(int(x), int(y)))

                        if inside and self._point_clear(
                            (x, y), required_clear_nm, required_pad_clear_nm, pads, tracks, zones_nearby
                        ):
                            via = pcbnew.PCB_VIA(board)
                            via.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
                            via.SetWidth(dia_nm)
                            via.SetDrill(drill_nm)
                            via.SetNetCode(via_net_code)
                            try:
                                via.SetViaType(pcbnew.VIATYPE_THROUGH)
                            except Exception:
                                pass
                            try:
                                via.SetLayerPair(params["top_layer"], params["bottom_layer"])
                            except Exception:
                                pass
                            try:
                                via.SetLocked(params["locked"])
                            except Exception:
                                pass
                            board.Add(via)
                            created += 1
                        else:
                            skipped += 1
                        x += spacing_nm
                    y += spacing_nm
                    row += 1

            try:
                pcbnew.Refresh()
            except Exception:
                pass

            self.show_msg(
                f"Done.\n\n"
                f"  - Vias created: {created}\n"
                f"  - Grid points skipped (foreign net / keep-out): {skipped}\n"
                f"  - Spacing: {params['spacing_mm']:.3f} mm  (offset {params['offset_x_mm']:.3f}, "
                f"{params['offset_y_mm']:.3f} mm{', staggered' if params['stagger'] else ''})\n"
                f"  - Via size: {params['dia_mm']:.3f} / {params['drill_mm']:.3f} mm\n"
                f"  - Clearance to other nets: {params['clearance_mm']:.3f} mm\n"
                f"  - Pad avoidance: foreign nets "
                f"{'on' if params['avoid_foreign_pads'] else 'off'}, own net "
                f"{'on' if params['avoid_same_net_pads'] else 'off'}"
                f"{', extra ' + format(params['pad_clearance_mm'], '.3f') + ' mm' if (params['avoid_foreign_pads'] or params['avoid_same_net_pads']) else ''}\n"
                f"  - Net: {params['net_name']}"
            )

        except Exception as e:
            self.show_msg(
                f"Uncaught error.\n\n"
                f"Type: {type(e).__name__}\n"
                f"Message: {e}\n\n"
                f"Traceback:\n{traceback.format_exc()}",
                is_error=True,
            )


if __name__ != '__main__':
    ViaStitcherPlugin().register()
else:
    print("[Carpet Bomber] Code loaded. Run ViaCarpetBomberPlugin().Run() in the console.")
