# Run this in Glyphs' Macro Panel to generate API markdown files on your Desktop.
import os, inspect, datetime
import GlyphsApp

OUT_DIR = os.path.expanduser("~/Desktop/glyphs_api_corpus")
os.makedirs(OUT_DIR, exist_ok=True)

def dump_obj(prefix, obj):
    fields, methods = [], []
    for attr in dir(obj):
        if attr.startswith("_"):
            continue
        try:
            val = getattr(obj, attr)
        except Exception:
            continue
        if callable(val):
            try:
                sig = str(inspect.signature(val))
            except Exception:
                sig = "(...)"
            methods.append(f"- `{prefix}.{attr}{sig}`")
        else:
            t = type(val).__name__
            fields.append(f"- `{prefix}.{attr}`  *(type: {t})*")
    return fields, methods

def write_md(name, fields, methods):
    path = os.path.join(OUT_DIR, f"{name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {name} (auto-generated)\n\n")
        f.write(f"_Generated on {datetime.datetime.now().isoformat()}_\n\n")
        if fields:
            f.write("## Attributes\n\n" + "\n".join(fields) + "\n\n")
        if methods:
            f.write("## Methods\n\n" + "\n".join(methods) + "\n")
    print("Wrote", path)

targets = []
for name in ("GSFont", "GSGlyph", "GSLayer", "GSAnchor", "GSPath", "GSNode"):
    try:
        cls = getattr(GlyphsApp, name)
        targets.append((name, cls))
    except Exception:
        pass

for name, cls in targets:
    fields, methods = dump_obj(name, cls)
    # Try instance inspection too:
    try:
        inst = cls.alloc().init()
        f2, m2 = dump_obj(f"{name} (instance)", inst)
        fields += f2; methods += m2
    except Exception:
        pass
    write_md(name, fields, methods)

print("Done. Copy the generated .md files into your RAG corpus `corpus/api/` and rebuild the index.")
