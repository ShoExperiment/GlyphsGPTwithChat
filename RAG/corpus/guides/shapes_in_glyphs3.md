# Shapes in Glyphs 3 (Canonical Outline API)

Key Points
- Use `layer.shapes` to access all shape objects (paths, components, etc.).
- Avoid the deprecated/legacy `layer.paths` in generated code.
- Identify path shapes:
  - Preferred: `isinstance(shape, GSPath)`
  - Fallback: `getattr(shape, "shapeType", None) == 1`
- Removing a shape:
  - Preferred when available: `layer.removeShape_(shape)`
  - Fallback: `del layer.shapes[index]` (delete from the end to avoid index shifts)
- Iteration order matters when deleting by index: collect indexes first, then delete in reverse.
