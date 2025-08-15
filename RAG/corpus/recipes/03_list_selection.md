# Report selected glyph names and active layers (read-only)

```python
from GlyphsApp import Glyphs

font = Glyphs.font
layers = list(font.selectedLayers) if font and font.selectedLayers else []
if not layers:
    print("Nothing selected.")
else:
    names = [L.parent.name for L in layers if getattr(L, "parent", None)]
    print("Selected glyphs:", ", ".join(names))
```
