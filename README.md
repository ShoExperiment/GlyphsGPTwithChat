### GlyphsGPT with Chat — AI-driven Macro Editor for Glyphs App

Build and **edit code by chatting**, then **run the script instantly**—no scripting background needed.  
GlyphsGPT turns your plain-language requests into ready-to-run Python macros inside Glyphs, so you can stay in design flow.

**Why designers love it**
- **AI-driven:** describe the change (“rename selected glyphs to .alt and update components”) and get the macro.
- **Chat to edit:** say “make it only affect uppercase” or “add an undo group” and the code updates.
- **One-click run:** execute right in the panel; no switching tools or saving files.
- **Cost-free option:** works with **local LLMs** (e.g., LM Studio), so you can use it with **no API cost**.
- **Optional citations (RAG):** plug in your own docs/specs to get grounded, cited answers when needed.

**Great for**
- Batch renaming, kerning tweaks, anchors/metrics housekeeping
- Generating repetitive drawing or export helpers
- Learning by example—see clean Python you can keep and reuse

Keep designing. Let the assistant handle the code.

<img width="898" height="789" alt="Screenshot 2025-08-15 at 17 45 14" src="https://github.com/user-attachments/assets/2cbe0511-8faf-424e-b17f-4bb676c18b52" />

This script adds a compact chat window inside Glyphs. Type a request in plain English, get a ready-to-run Python snippet, edit it inline, and execute without leaving the app.

- Works with **GPT-5** or any **gpt-oss**.

---

## Install (designer-friendly)
1. In Glyphs, open **Scripts → Open Scripts Folder**.  
2. From this repo, download and place ONE of these files into that folder:

   - **Recommended (no SSL fuss):**  
     `GlyphsGPTwithChat/GlyphsGPTwithChat/GlyphsGPT with Chat for Default Python.py`

   - **Advanced (uses Python’s ssl):**  
     `GlyphsGPTwithChat/GlyphsGPTwithChat/GlyphsGPT with Chat.py`

3. Back in Glyphs, choose **Scripts → Reload Scripts** (⌥⌘⇧Y).  
4. Run **Scripts → GlyphsGPT with Chat**.

> Tip: Start with the **“for Default Python”** version—it's the least picky about certificates.

---

## First run (about a minute)
1. Click **⚙ Settings** in the chat window.  
2. Pick a model:
   
<img width="717" height="441" alt="Screenshot 2025-08-15 at 17 52 36" src="https://github.com/user-attachments/assets/d0e33340-4535-402f-a09b-e7ce552e2fa8" />

   **OpenAI**
   - **LM Base:** `https://api.openai.com/v1`  
   - **Model:** e.g. `gpt-5`  
   - **LM Key:** your API key
     
<img width="719" height="442" alt="Screenshot 2025-08-15 at 18 01 52" src="https://github.com/user-attachments/assets/3573d31a-b414-423d-bce0-73a3474a93bb" />

   **Local server (OpenAI-compatible)**
   - **LM Base:** e.g. `http://localhost:1234/v1`  
   - **Model:** whatever your server exposes

3. Type:  
   *“Select all open paths in the current glyph and close them.”*  
   Then hit **Ask**.

---
## Optional: Grounded answers (RAG)

<img width="718" height="443" alt="Screenshot 2025-08-15 at 17 51 29" src="https://github.com/user-attachments/assets/1afccef2-3951-40a8-9d88-76225e755ef7" />
Want the assistant to cite *your* docs/specs? Run a tiny local RAG server.

### What you’ll do
1. Put files in `RAG/corpus/` (TXT, MD, and PDFs work best).
2. Build a searchable index from those files.
3. Start the local search server.
4. In the Glyphs script **Settings**, set **RAG URL** to `http://localhost:8001/search` and use **Auto** or **Grounded** mode.

---

### 1) Install (first time only)
```bash
cd RAG
pip3 install -U fastapi uvicorn sentence-transformers pypdf faiss-cpu
```

### 2) Build the index (run whenever you add/change docs)
```bash
cd RAG
python3 build_index.py \
  --source ./corpus \
  --outdir ./index \
  --model sentence-transformers/all-MiniLM-L6-v2 \
  --chunk 900 --overlap 150
# If your Python is 'python' instead of 'python3', use: python build_index.py ...
```

### 3) Start the server
```bash
cd RAG
export RAG_INDEX_DIR="$(pwd)/index"     # macOS/Linux
uvicorn server:app --host 0.0.0.0 --port 8001
```

Windows (PowerShell):
```powershell
cd RAG
$env:RAG_INDEX_DIR = (Get-Location).Path + "\index"
uvicorn server:app --host 0.0.0.0 --port 8001
```

Leave this Terminal window open while you use the tool.

---

### 4) Point the Glyphs script to your server
In **Settings**, set:
- **RAG URL**: `http://localhost:8001/search`
- **Mode**: **Auto** or **Grounded**

### Quick check
Open a browser to: `http://127.0.0.1:8001/search?q=kerning`  
If you see JSON results, your RAG server is working.

### Notes
- Re-run the **Build the index** step anytime you add or change files in `RAG/corpus/`.
- If `pip3` isn’t found, try `pip`.



---

## Troubleshooting
- **SSL / certificate error**  
  Use **“GlyphsGPT with Chat for Default Python.py.”** It routes network calls via Apple’s APIs to avoid common embedded-Python SSL issues.
- **No response / blank answers**  
  Check **LM Base / Model / Key** in Settings. Try **Chat** mode first.
- **“Unsupported parameter: max_tokens”**  
  Update to the latest script (already handled).
- **Blank window**  
  Requires a recent Glyphs 3 with WKWebView. Restart Glyphs after install.

---

## Use a Local Model (Recommended: LM Studio)
<img width="1723" height="1032" alt="スクリーンショット 2025-08-15 18 12 47" src="https://github.com/user-attachments/assets/e8e725b8-aa6c-46b9-924a-b275cf57962c" />

You can run GlyphsGPT entirely offline using **LM Studio**. This is the easiest way for non-developers.

### 1) Install LM Studio
- Download LM Studio (macOS app) from **lmstudio.ai** and open it.

### 2) Get the model
- In LM Studio, open **Discover → Models** and search for **`gpt-oss-20b`**.
- Click **Download** (choose any quantization that fits your Mac; if unsure, start with the default).

> Tip: If your Mac runs out of memory, pick a *smaller* quantization or lower the Context Length later.

### 3) Load the model & start the server
- Click **Load** on `gpt-oss-20b`.
- Open the small **⚙ Settings** panel (next to “Status: Running”) and use these toggles:
  - **Server Port**: `1234`
  - **Serve on Local Network**: **ON**
  - **Enable CORS**: **ON** (makes the in-app web UI happier)
  - **Just-in-Time Model Loading**: ON (default)
- In the right sidebar (Load tab), suggested starting values:
  - **Context Length**: `20000` (you can reduce if memory is tight)
  - **GPU Offload**: max your slider allows
  - **CPU Thread Pool**: ~`8–12` threads

**Baseline that works well on my machine**
- Mac Studio **M2 Max**, **32 GB** RAM
- Model: `openai/gpt-oss-20b`
- GPU Offload: full slider
- Context Length: 20k
- Threads: 11

LM Studio will show “Reachable at: `http://<your-local-ip>:1234`”.

### 4) Point GlyphsGPT to LM Studio
Open **Settings** in GlyphsGPT and fill:

- **LM Base**: `http://<your-local-ip>:1234/v1`
  - Example if you’re on the same Mac: `http://localhost:1234/v1`
- **Model**: `openai/gpt-oss-20b`
- **LM Key**: *(leave empty)*
- **Include retrieval (RAG)**: off unless you’re using the optional RAG server

Leave the other defaults as is:
- **Max context (tokens)**: `20000`
- **Max output tokens**: `1024`
- **Headroom (safety)**: `512`

Click **Save**.

### 5) Quick check
Open a new chat and ask: “What model are you?”  
You should see a response generated locally by LM Studio.

---

#### Troubleshooting

- **No connection / timeouts**  
  Make sure LM Studio shows “Reachable at: `http://…:1234`” and **Serve on Local Network** is ON.
- **Model name mismatch**  
  The **Model** field in GlyphsGPT must match LM Studio’s model ID exactly: `openai/gpt-oss-20b`.
- **Out of memory / crashes**  
  Lower **Context Length** (e.g., 8k) or download a smaller quantization of the model.
- **Slow or short answers**  
  Increase **Max output tokens** in GlyphsGPT (e.g., 1536) or reduce **Headroom** slightly (e.g., 384).

> Optional: If you later use the RAG server, set **RAG URL** to `http://localhost:8001/search` and switch mode to **Auto** or **Grounded**.

---

## License
Apache-2.0 — see `LICENSE`.

---

