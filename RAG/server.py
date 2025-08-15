import os, json
from pathlib import Path
from typing import List, Optional
import numpy as np
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

INDEX_DIR = Path(os.environ.get("RAG_INDEX_DIR", "index"))
API_TOKEN = os.environ.get("RAG_API_TOKEN", "")
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL")  # optional override

app = FastAPI(title="GlyphsGPT RAG Server", version="0.1.0")

_vecs = None; _meta = None; _model = None

def load_index():
    global _vecs, _meta, _model
    if _vecs is not None: return
    v = INDEX_DIR / "vectors.npy"; m = INDEX_DIR / "meta.json"; c = INDEX_DIR / "config.json"
    if not v.exists() or not m.exists(): raise RuntimeError("Index missing. Build it first.")
    _vecs = np.load(v)
    _meta = json.load(open(m, "r", encoding="utf-8"))
    cfg = json.load(open(c, "r", encoding="utf-8")) if c.exists() else {}
    model_id = EMBED_MODEL or cfg.get("model") or "sentence-transformers/all-MiniLM-L6-v2"
    _model = SentenceTransformer(model_id)

def auth(authorization: Optional[str] = Header(None)):
    if not API_TOKEN: return True
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

class SearchQuery(BaseModel):
    query: str
    top_k: int = 5

class SearchItem(BaseModel):
    id: str; score: float; text: str; meta: dict

class SearchResponse(BaseModel):
    results: List[SearchItem]

@app.on_event("startup")
def startup(): load_index()

@app.post("/search", response_model=SearchResponse)
def search(q: SearchQuery, ok: bool = Depends(auth)):
    qv = _model.encode([q.query], normalize_embeddings=True, convert_to_numpy=True)[0]
    sims = (_vecs @ qv).tolist()
    import numpy as np
    idxs = np.argsort(sims)[::-1][:q.top_k]
    items = [SearchItem(id=_meta[i]["id"], score=float(sims[i]),
                        text=_meta[i]["text"], meta=_meta[i]["meta"]) for i in idxs]
    return SearchResponse(results=items)
