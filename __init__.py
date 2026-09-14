"""
Package entry point for the Via Stitcher (a.k.a. Via Carpet Bomber) KiCad
plugin.

KiCad discovers plugins by importing the `__init__.py` of each
subdirectory under its `plugins/` (or `scripting/plugins/`) folder.
Importing `via_carpet_bomber` here is what actually runs its
registration code (`ViaStitcherPlugin().register()`), which is executed
as a side effect of the import at the bottom of that module.
"""

from . import via_carpet_bomber  # noqa: F401
