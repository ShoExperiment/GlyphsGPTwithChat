# Quirks & Gotchas (Short)

- Selection objects can be proxies; don’t mutate selection while iterating it.
- Prefer `layer.shapes` and `GSPath` over `layer.paths`.
- layer = font.selectedLayers[0] is the way to get single current selected layer
- `removeShape_` may be unavailable in some builds; fall back to `del layer.shapes[i]` (delete in reverse index order).
- Keep anchor names consistent across masters before bulk generation or movement.
- When in doubt about API availability, `hasattr(obj, "methodName")` before calling.
- Distinguish Glyphs 3 API from other API such as RoboFont, Glyphs 2 and FontLab