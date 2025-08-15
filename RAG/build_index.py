import argparse, os, json, hashlib
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer

def chunk_text(text, max_chars=1800, overlap=200):
    chunks, n, i = [], len(text), 0
    while i < n:
        j = min(n, i + max_chars)
        chunks.append(text[i:j])
        if j == n: break
        i = max(0, j - overlap)
    return [c for c in chunks if c.strip()]

def read_texts(folder):
    paths, texts = [], []
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file() and p.suffix.lower() in {".md", ".txt"}:
            try:
                texts.append(p.read_text(encoding="utf-8", errors="ignore"))
                paths.append(str(p))
            except Exception:
                pass
    return paths, texts

def sha(x): return hashlib.sha1(x.encode("utf-8")).hexdigest()[:16]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--outdir", default="index")
    ap.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    ap.add_argument("--chunk", type=int, default=1800)
    ap.add_argument("--overlap", type=int, default=200)
    args = ap.parse_args()

    out = Path(args.outdir); out.mkdir(parents=True, exist_ok=True)
    model = SentenceTransformer(args.model)

    paths, docs = read_texts(args.source)
    records = []
    for path, doc in zip(paths, docs):
        for ch in chunk_text(doc, args.chunk, args.overlap):
            rid = sha(path + "::" + ch[:64])
            records.append({"id": rid, "text": ch, "meta": {"path": path}})
    if not records:
        print("No text found"); return

    texts = [r["text"] for r in records]
    vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    np.save(out / "vectors.npy", vecs)
    with open(out / "meta.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    with open(out / "config.json", "w", encoding="utf-8") as f:
        json.dump({"model": args.model, "built_from": os.path.abspath(args.source)}, f, indent=2)
    print(f"Indexed {len(records)} chunks from {len(paths)} files.")
if __name__ == "__main__": main()
