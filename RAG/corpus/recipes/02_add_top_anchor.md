# Add a `top` anchor if missing (current glyph, all master layers; no undo by default)

```python
from GlyphsApp import Glyphs, GSAnchor
from AppKit import NSPoint

font = Glyphs.font
layer = font.selectedLayers[0] if font and font.selectedLayers else None
glyph = layer.parent if layer else None

if not glyph:
    print("No glyph selected.")
else:
    added = 0
    for L in glyph.layers:
        if getattr(L, "anchors", None) is not None:
            if not L.anchorNamed_("top"):
                a = GSAnchor.alloc().init()
                a.setName_("top")
                a.setPosition_(NSPoint(0,0))
                L.addAnchor_(a)
                added += 1
    print(f"Added {added} 'top' anchor(s).")
```
