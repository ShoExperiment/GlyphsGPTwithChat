# Delete all path shapes from the current layer (no undo by default)

```python
from GlyphsApp import Glyphs, GSPath

layer = Glyphs.font.selectedLayers[0] if Glyphs.font and Glyphs.font.selectedLayers else None
if not layer:
    print("No layer selected.")
else:
    idxs = [i for i, s in enumerate(layer.shapes) if isinstance(s, GSPath) or getattr(s, "shapeType", None) == 1]
    if not idxs:
        print("No path shapes found.")
    else:
        for i in reversed(idxs):
            try:
                layer.removeShape_(layer.shapes[i])
            except Exception:
                del layer.shapes[i]
        print(f"Deleted {len(idxs)} path shape(s).")
```
