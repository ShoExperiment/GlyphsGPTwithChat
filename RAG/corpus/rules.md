# GlyphsGPT Rules (Concise)

- **Default: no undo.** Only add an undo wrapper if the prompt explicitly asks for a *single* undo step. See `guides/undo_guidance.md`.
- **Use shapes in G3.** Read and edit outlines via `layer.shapes`. Avoid `layer.paths`.
- **Identify paths safely.** Prefer `isinstance(shape, GSPath)`. If unsure, use `getattr(shape, "shapeType", None) == 1`.
- **No font-level undo calls.** Do *not* use `Font.beginUndoGroup()` / `Font.endUndoGroup()` or any `GSUndoGroup`. They are not part of the Python API used here.
- **Batch edits:** Iterate over `Glyphs.font.selectedLayers` → `layer` → `layer.shapes`. For cross-master ops, iterate `glyph.layers` as needed.
- **Plan before writing:** Read → plan → write. Validate availability of attributes/methods with `hasattr` before calling.
- **Performance:** Bound loops; avoid quadratic scans over all glyphs and layers if not necessary.
- **Safety:** No file/network I/O from generated code. No subprocesses.
