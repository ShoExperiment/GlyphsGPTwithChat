# Undo Guidance (Text, not code)

Purpose: minimize failures caused by misuse of undo.

Defaults
- Do not open undo groups unless the user explicitly asks for a single undo step.

If a single-step undo is requested
- Per-glyph changes: wrap each glyph’s mutations with the glyph’s own undo facilities (API availability may vary by version/build).
- Multi-glyph batch as one step: use the *document*'s undo manager grouping facilities. This is owned by the document, not by the font object.

Do **not**
- Do not use Font.beginUndoGroup / Font.endUndoGroup (these are not the Python APIs to use here).
- Do not import GSUndoGroup.
- Do not leave an undo group unclosed (always pair begin/end).

Notes
- Some methods vary across app versions; test with `hasattr` when an API may be absent in a given build.
- Prefer correctness and simplicity over fancy undo semantics.
