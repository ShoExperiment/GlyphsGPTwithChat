#MenuTitle: GlyphsGPT with Chat
# -*- coding: utf-8 -*-
__doc__="""
GlyphsGPT with Chat
"""

# GlyphsGPT · HTML Chat UI (WKWebView) — TABS + HARDENED BRIDGE + EDITABLE CODE + RELIABLE PYTHON COLORS
# - Real tabs: per-tab settings & history; "+" clones current tab
# - Bridge is crash-proof and JSON-safe (no ObjC containers leak out)
# - Per-tab Settings/History stored under PREFKEY
# - RAG optional; strict/grounded prompts available
# - Inline editor (Edit → Run / Done / Cancel), Execute and Copy
# - Output sanitizer for stray tool tokens
# - FIX: proper Python syntax highlighting for strings, comments, numbers, keywords, and common builtins
#        using class-based spans (no inline hex codes), and a placeholder system to avoid coloring inside strings

from GlyphsApp import Glyphs
import json, urllib.request, urllib.error, traceback, sys, io, contextlib, re, copy

import objc
from AppKit import NSWindow
from Foundation import NSObject, NSDictionary, NSString, NSArray, NSNull, NSNumber
from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController
import ssl, socket, urllib.parse

DEBUG = False
PREFKEY = "com.yourname.GlyphsGPT.HTMLChat"

# ---- Defaults for a single session (tab) ----
SESSION_DEFAULTS = dict(
    name=None,                     
    llmBase="http://<YourIP Address>/v1",# Set https://api.openai.com/v1  for gpt-5
    llmModel="openai/gpt-oss-20b", #Set gpt-5 for gpt-5 API
    llmKey="",#Set your OpenAI key for gpt-5
    ragURL="http://<YourIP Address>/search",#elete for gpt-5
    ragToken="",
    topK=8,
    useRAG=True, #set False for gpt-5
    mode=0,                        # 0 Auto, 1 Grounded, 2 Hybrid, 3 Chat
    remember=True,
    maxContext=20000,
    maxOutput=1024,  #Try setting 4048 for gpt-5
    headroom=512,
    history=[],
)

MAX_CONTEXT_CHUNKS   = 5
CHUNK_CHAR_LIMIT     = 1200

# ---- Prompt presets ----
PROMPT_GROUNDED = """You are a strict Glyphs 3 scripting assistant.

RULES
1) Use ONLY the APIs and facts that appear verbatim in CONTEXT.
2) After each API symbol you use (Class.method/prop), append its citation [S#] that matches the snippet it came from.
3) If code is requested but a required API is missing from CONTEXT, reply exactly: insufficient context
4) Never invent or guess API names. Do NOT use Glyphs 2, RoboFont, or FontLab APIs unless they appear in CONTEXT.
5) Prefer minimal, correct examples over cleverness.
6) Output format (decide based on the question):
   - If the user asks for a script/code or code is clearly the most direct answer, write a good enough explanation (2–5 sentences), then exactly ONE fenced `python` block, followed by an optional short note (≤ 3 sentences) if helpful.
   - Otherwise, write a concise text answer (no code). If you mention APIs, still add [S#] after them.
7) If you mention undo groups: do not use Font.beginUndoGroup or Font.endUndoGroup unless they appear in CONTEXT.

You are assisting an expert user; be precise and concise."""

PROMPT_HYBRID = """You are a Glyphs 3 scripting assistant.

RULES
1) Prefer APIs found in CONTEXT and cite them with [S#].
2) If you need an API not in CONTEXT, keep it conservative and add a trailing comment `# UNVERIFIED`.
3) Never mix in Glyphs 2/RoboFont/FontLab APIs.
4) Output format:
   - If code is explicitly requested or clearly best: one brief sentence + ONE fenced `python` block.
   - Otherwise: concise text answer, no code."""

PROMPT_CHAT = """You are a helpful assistant specialized in Glyphs 3 scripting.
- Keep answers minimal and correct.
- Avoid inventing APIs; if unsure, say so.
- Output:
  - Code only if asked or obviously required; otherwise plain text."""

HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>GlyphsGPT with Chat</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --muted:#a3acc3; --text:#e6ebff; --border:#262a36;
    --bubble-user:#1e2430; --bubble-assistant:#121722; --code-bg:#0b0e14; --code-border:#222736;
    --tab:#1b2030; --tab-active:#0e1422; --tab-hover:#232a3c; --red:#e26d6d;
  }
  *{box-sizing:border-box} html,body{height:100%;margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,Segoe UI,sans-serif}
  .wrap{display:flex;flex-direction:column;height:100%}
  .topbar{padding:8px 10px;border-bottom:1px solid var(--border);background:var(--panel);position:sticky;top:0;z-index:3}
  .row{display:flex;gap:8px;align-items:center}
  .title{font-weight:600;margin-right:8px}
  .spacer{flex:1}
  .tabEdit{
    font:inherit; color:var(--text);
    background:#0f1320; border:1px solid var(--border);
    border-radius:6px; padding:2px 6px; outline:none;
  }
  .tab .model{font-size:12px; opacity:0.75}
  .tabs{display:flex;gap:6px;overflow:auto;padding:6px 0;}
  .tab{background:var(--tab); border:1px solid var(--border); border-radius:8px; padding:6px 10px; cursor:pointer; display:flex; align-items:center; gap:8px; white-space:nowrap}
  .tab:hover{background:var(--tab-hover)}
  .tab.active{background:var(--tab-active); border-color:#334058}
  .x{font-size:12px; opacity:0.75; padding:0 4px;}
  .x:hover{opacity:1; color:var(--red)}
  .plus{background:#222739;color:#dce2ff;border:1px solid var(--border);border-radius:8px;padding:6px 10px;cursor:pointer}
  .plus:hover{border-color:#334058}

  .btn{background:#222739;color:#dce2ff;border:1px solid var(--border);border-radius:8px;padding:6px 10px;cursor:pointer}
  .btn:hover{border-color:#334058}

  .chat{flex:1;overflow:auto;padding:14px 14px 0 14px}
  .bubble{max-width:860px;margin:0 auto 12px auto;padding:12px 14px;border:1px solid var(--border);border-radius:12px;white-space:pre-wrap}
  .user{background:var(--bubble-user)}.assistant{background:var(--bubble-assistant)}.system{background:#151920;color:#a3acc3}
  .promptRow{padding:12px;border-top:1px solid var(--border);background:var(--panel);display:flex;gap:10px}
  textarea#prompt{flex:1;resize:vertical;min-height:70px;max-height:180px;padding:10px;background:#0f1320;color:#e6ebff;border:1px solid var(--border);border-radius:8px}

  pre{background:var(--code-bg);border:1px solid var(--code-border);border-radius:10px;padding:10px;overflow:auto}
  code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
  .codeHeader{display:flex;align-items:center;justify-content:flex-end;gap:8px;margin:6px 0 0 0}
  .codeBtn{border:1px solid var(--border);background:#1c2232;color:#cfe0ff;border-radius:6px;padding:2px 8px;font-size:12px;cursor:pointer}
  .codeBtn:hover{border-color:#334058}
  .codeEdit{width:100%;min-height:220px;background:var(--code-bg);color:var(--text);border:1px solid var(--code-border);border-radius:10px;padding:10px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;white-space:pre;overflow:auto}

  #contextPanel{display:none;position:fixed;right:16px;bottom:90px;width:420px;max-height:50%;overflow:auto;background:#0c0f17;border:1px solid var(--border);border-radius:10px;padding:10px;z-index:4}
  #modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:5;align-items:center;justify-content:center}
  .card{width:720px;background:#0c0f17;border:1px solid var(--border);border-radius:14px;padding:14px}
  .grid{display:grid;grid-template-columns: 1fr 1fr;gap:10px}
  .field input,.field select{width:100%;padding:8px;border:1px solid var(--border);border-radius:8px;background:#0f1320;color:#e6ebff}
  .muted{color:#a3acc3;font-size:12px}

  /* Syntax colors (class-based; no inline styles to avoid '#' issues) */
  pre code span.s { color:#a6e3a1 !important; }  /* strings */
  pre code span.c { color:#6c7086 !important; }  /* comments */
  pre code span.n { color:#cba6f7 !important; }  /* numbers */
  pre code span.k { color:#89b4fa !important; }  /* keywords */
  pre code span.b { color:#f38ba8 !important; }  /* builtins */
</style>
</head>
<body>
<div class="wrap">

  <div class="topbar">
    <div class="row">
      <div class="title">GlyphsGPT with Chat</div>
      <div class="spacer"></div>
      <button id="btnCtx" class="btn">Context</button>
      <button id="btnSettings" class="btn">⚙ Settings</button>
      <button id="btnNew" class="btn">New Chat</button>
    </div>
    <div id="tabbar" class="tabs" style="margin-top:6px"></div>
  </div>

  <div id="contextPanel"></div>
  <div id="chat" class="chat"></div>

  <div class="promptRow">
    <textarea id="prompt" placeholder="Type a message…"></textarea>
    <button id="btnSend" class="btn">Ask</button>
  </div>
</div>

<div id="modal">
  <div class="card">
    <h3 style="margin:6px 0 10px 0">Settings (this tab only)</h3>
    <div class="grid">
      <div class="field"><div class="muted">LM Base</div><input id="s_lmBase"/></div>
      <div class="field"><div class="muted">Model</div><input id="s_lmModel"/></div>
      <div class="field"><div class="muted">LM Key (optional)</div><input id="s_lmKey"/></div>
      <div class="field"><div class="muted">RAG URL</div><input id="s_ragURL"/></div>
      <div class="field"><div class="muted">RAG Token (optional)</div><input id="s_ragToken"/></div>
      <div class="field"><div class="muted">Top-K</div><input id="s_topK" type="number" min="1" max="20"/></div>

      <div class="field"><div class="muted">Max context (tokens)</div><input id="s_maxContext" type="number" min="1024" max="200000"/></div>
      <div class="field"><div class="muted">Max output tokens</div><input id="s_maxOutput" type="number" min="64" max="8000"/></div>
      <div class="field"><div class="muted">Headroom (safety)</div><input id="s_headroom" type="number" min="0" max="4000"/></div>
    </div>

    <div class="row" style="margin-top:10px">
      <label class="row"><input id="s_useRAG" type="checkbox" checked style="margin-right:6px"/>Include retrieval</label>
      <div class="spacer"></div>
      <div class="field">
        <select id="s_mode">
          <option value="0">Auto (prefer RAG)</option>
          <option value="1">Grounded (RAG only)</option>
          <option value="2">Hybrid (RAG + general)</option>
          <option value="3">Chat (no RAG)</option>
        </select>
      </div>
      <label class="row"><input id="s_remember" type="checkbox" checked style="margin-right:6px"/>Remember chat</label>
    </div>
    <div class="row" style="margin-top:12px; justify-content:flex-end">
      <button id="btnSave" class="btn">Save</button>
      <button id="btnClose" class="btn">Close</button>
    </div>
  </div>
</div>

<script>
  const chatEl = document.getElementById('chat');
  const promptEl = document.getElementById('prompt');
  const ctxEl = document.getElementById('contextPanel');
  const modal = document.getElementById('modal');
  const tabbar = document.getElementById('tabbar');
  let __activeTabIndex = 0;
  let __clickTimer = null;
  function asBool(v){
    if (v === true || v === false) return v;
    if (typeof v === "number") return v !== 0;
    if (typeof v === "string") {
      const s = v.trim().toLowerCase();
      return s === "1" || s === "true" || s === "yes" || s === "on" || s === "y" || s === "t";
    }
    return !!v; // fallback
  }
  function copyText(text){
    try { if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text); return; } } catch(e){}
    var ta = document.createElement('textarea'); ta.value=text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch(e){} document.body.removeChild(ta);
  }
  function esc(s){return String(s||"").replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}

  // Strip occasional tool-call markup
  function stripWeirdLLMTokens(s){
    if(!s) return s;
    s = s.replace(/<\|[^|>]{0,80}\|>/g, '');
    s = s.replace(/\b(?:analysis|commentary|final)\s+to=[^\s]+(?:\s+code)?/gi, '');
    s = s.replace(/```python_user_visible/g, '```python');
    return s;
  }

  // Python highlighter with placeholder protection for strings
  function colorPython(code){
    let t = esc(code);

    // 1) strings first (so '#' inside strings won't become comments)
    t = t.replace(/('{3}[\s\S]*?'{3}|"{3}[\s\S]*?"{3})/g, m => '<span class="s">'+m+'</span>');
    t = t.replace(/'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"/g, m => '<span class="s">'+m+'</span>');

    // protect string spans
    const store = [];
    t = t.replace(/<span class="s">[\s\S]*?<\/span>/g, function(m){ const i=store.push(m)-1; return '@@S'+i+'@@'; });

    // 2) keywords (not inside strings)
    const kw = /\b(?:def|class|return|if|elif|else|for|while|try|except|finally|with|as|lambda|yield|import|from|pass|break|continue|in|is|and|or|not|assert|raise|global|nonlocal|True|False|None)\b/g;
    t = t.replace(kw, m => '<span class="k">'+m+'</span>');

    // 3) builtins
    const bi = /\b(?:print|len|range|dict|list|set|tuple|int|float|str|bool|sum|min|max|abs|isinstance|enumerate|zip|map|filter|any|all|open|sorted|reversed|super)\b/g;
    t = t.replace(bi, m => '<span class="b">'+m+'</span>');

    // 4) numbers
    t = t.replace(/\b\d+(?:\.\d+)?\b/g, m => '<span class="n">'+m+'</span>');

    // 5) comments last
    t = t.replace(/#.*$/gm, m => '<span class="c">'+m+'</span>');

    // restore strings
    t = t.replace(/@@S(\d+)@@/g, (_,i) => store[+i]);

    return t;
  }


  function cleanZW(s){
    // strip zero-width spaces & BOM that sometimes sneak into fences
    return String(s||"").replace(/[\u200B\u200C\u200D\u2060\uFEFF]/g, "");
  }
  
  function looksLikePython(text){
    if(!text) return false;
    const t = String(text);
    return (
      /(^|\n)\s*(from|import|class|def|for|while|if|try|with|except|finally|lambda|return)\b/.test(t) ||
      /\bGlyphs\b|\bGS(?:Font|Glyph|Layer|Path)\b/.test(t) ||
      /^\s*#/.test(t)                                   // python comments at top
    );
  }
  function looksLikePlainText(text){
    if(!text) return true;
    const lines = String(text).trim().split(/\n/);
    if (lines.length > 6) return false;                // long blocks more likely code
    const hasCodey = /[{};=<>]/.test(text);
    return !looksLikePython(text) && !hasCodey;
  }
  
  function isPythonishLine(line){
    const t = String(line||"").trim();
    if (!t) return false;
    return /^(?:from|import|class|def|if|for|while|try|with|except|finally|return|lambda|@|#|"{3}|'{3}|pass|raise)/.test(t)
        || /\bGlyphs\b|\bGS(?:Font|Glyph|Layer|Path)\b/.test(t)
        || /[A-Za-z_]\w*\s*=/.test(t);
  }
  
  function isEnglishLine(line){
    const t = String(line||"").trim();
    if (!t) return false;
    // “Englishy” line: letters and spaces, not obviously code/punctuation soup
    return /[A-Za-z]/.test(t) && !isPythonishLine(t) && !/[{}<>;=]/.test(t);
  }
  
  function commentify(text){
    return String(text||"")
      .split("\n")
      .map(l => l.trim() ? ("# " + l) : "#")
      .join("\n");
  }
  
  function splitProsePython(raw){
    const lines = String(raw||"").split("\n");
  
    // leading prose (up to 3 lines)
    let i = 0; while(i < lines.length && !lines[i].trim()) i++;
    let leadEnd = i;
    if (i < lines.length && isEnglishLine(lines[i])) {
      let k = i, n = 0;
      while (k < lines.length && isEnglishLine(lines[k]) && n < 3) { k++; n++; }
      leadEnd = k;
    }
  
    // trailing prose (up to 3 lines)
    let j = lines.length - 1; while (j >= leadEnd && !lines[j].trim()) j--;
    let trailStart = j + 1;
    if (j >= leadEnd && isEnglishLine(lines[j])) {
      let k = j, n = 0;
      while (k >= leadEnd && isEnglishLine(lines[k]) && n < 3) { k--; n++; }
      trailStart = k + 1;
    }
  
    const lead  = lines.slice(0, leadEnd).join("\n").trim();
    const body  = lines.slice(leadEnd, trailStart).join("\n");
    const trail = lines.slice(trailStart).join("\n").trim();
  
    return { lead, body, trail };
  }
  
    function blockHasPythonCues(text){
    const lines = String(text||"").split("\n");
    let hits = 0;
    for (const l of lines){
      const t = l.trim();
      if (!t) continue;
      if (isPythonishLine(t)) hits++;
      if (hits >= 2) break; // require at least two pythonish lines to avoid false positives
    }
    return hits >= 2;
  }
  
  
  // Markdown → HTML (fenced code aware) + header with Edit/Execute/Copy
  function mdToHtml(md){
    const src   = cleanZW(String(md||"")).replace(/\r\n/g, "\n");
    const lines = src.split("\n");
  
    let html = "";
    let inCode = false, buf = [], lang = "", fenceChar = "```";
    let para = [];
    let sawPre = false;
  
    const openRe  = /^\s*(```|~~~)\s*([A-Za-z0-9._+-]*)\s*.*$/;    // forgiving open
    const closeRe = /^\s*(```|~~~)\s*.*$/;                          // forgiving close
  
    function flushPara(){
      if(!para.length) return;
      const txt = para.join("\n")
        .replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]))
        .replace(/\n/g,"<br>");
      html += "<p>"+txt+"</p>";
      para = [];
    }

    function flushCode(){
      const raw      = buf.join("\n");
      const langNorm = (lang||"").toLowerCase();
      const isPyLang = (langNorm==="python" || langNorm==="py" || langNorm==="python_user_visible");
      const unlabeled = !langNorm || langNorm==="text";
    
      // Treat unlabeled fences as Python if the block clearly looks like Python
      const looksLikePythonBlock = isPyLang || (unlabeled && blockHasPythonCues(raw));
    
      // Case A: Python (explicit or confidently guessed)
      if (looksLikePythonBlock){
        const parts = splitProsePython(raw);
        let render  = (parts.body || "").trim();
    
        if (parts.lead)  render = commentify(parts.lead) + (render ? "\n\n"+render : "");
        if (parts.trail) render = (render ? render + "\n\n" : "") + commentify(parts.trail);
    
        // If the fence had only prose, keep it as commented python so Execute still works
        if (!render) render = commentify(parts.lead || raw);
    
        const encoded = encodeURIComponent(render);
        const body    = colorPython(render);
        html += '<div class="codeHeader" data-raw="'+encoded+'">'
             +  '<button class="codeBtn" data-edit="'+encoded+'">Edit</button>'
             +  '<button class="codeBtn" data-exec="'+encoded+'">Execute</button>'
             +  '<button class="codeBtn" data-copy="'+encoded+'">Copy</button>'
             +  '</div>';
        html += '<pre><code class="lang-python">'+body+'</code></pre>';
        sawPre = true;
        buf=[]; lang=""; inCode=false;
        return;
      }
    
      // Case B: non-python fence — collapse short prose-only blocks to <p>
      const firstLineCodey = isPythonishLine(raw) || /[`$]/.test(raw);
      if (!firstLineCodey){
        const lines = raw.trim().split("\n");
        const shortProse = lines.length <= 6 && lines.every(isEnglishLine);
        if (shortProse){
          const txt = raw
            .replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]))
            .replace(/\n/g,"<br>");
          html += "<p>"+txt+"</p>";
          buf=[]; lang=""; inCode=false;
          return;
        }
      }
    
      // Default: render as-is (non-python code)
      const encoded = encodeURIComponent(raw);
      const body = (langNorm && langNorm !== "text") ? colorPython(raw) : esc(raw);
      html += '<div class="codeHeader" data-raw="'+encoded+'">'
           +  '<button class="codeBtn" data-edit="'+encoded+'">Edit</button>'
           +  '<button class="codeBtn" data-exec="'+encoded+'">Execute</button>'
           +  '<button class="codeBtn" data-copy="'+encoded+'">Copy</button>'
           +  '</div>';
      html += '<pre><code class="lang-'+(lang||"text")+'">'+body+'</code></pre>';
      sawPre = true;
    
      buf=[]; lang=""; inCode=false;
    }

  
    for(let i=0;i<lines.length;i++){
      const line = lines[i];
  
      if(!inCode){
        const m = line.match(openRe);
        if(m){
          flushPara();
          fenceChar = m[1];               // remember which fence opened (``` or ~~~)
          lang = (m[2]||"");
          if(lang==="py") lang="python";
          if(lang==="python_user_visible") lang="python";
          inCode = true;
          continue;
        }
        para.push(line);
      }else{
        // close on any fence line (``` or ~~~), even if indented or with trailing text
        if(closeRe.test(line)){
          flushCode();
          continue;
        }
        buf.push(line);
      }
    }
    if(inCode) flushCode();
    flushPara();
  

  
    return html;
  }

  function addBubble(role, text){
    var div=document.createElement('div'); div.className="bubble "+role;
    var t = (role==="assistant") ? stripWeirdLLMTokens(text) : text;
    div.innerHTML = role!=="user" ? mdToHtml(t) : esc(t);
    chatEl.appendChild(div); chatEl.scrollTop = chatEl.scrollHeight;
  }
  function setContext(text){ ctxEl.textContent = text || "(no context)"; }
  function toggle(el){ el.style.display = (el.style.display==="none"||!el.style.display) ? "block":"none"; }

  // TAB RENDERING
  function renderTabs(info){
    const names  = info.names  || [];
    const models = info.models || [];
    const active = info.active || 0;
    __activeTabIndex = active;            // <-- remember active tab
    tabbar.innerHTML = "";
    names.forEach((name, i) => {
      const t = document.createElement('div');
      t.className = "tab" + (i===active ? " active" : "");
      t.setAttribute("data-idx", i);
      const model = (models[i] || "");
      const shortModel = model.split("/").pop();
      t.innerHTML =
        '<span class="label">'+esc(name)+'</span>' +
        (shortModel ? ' <span class="model">· '+esc(shortModel)+'</span>' : '') +
        '<span class="x" title="Close" data-close="'+i+'">×</span>';
      tabbar.appendChild(t);
    });
    const plus = document.createElement('button');
    plus.id = "btnPlusTab";
    plus.className = "plus";
    plus.textContent = "＋";
    tabbar.appendChild(plus);
  }
  
  

  tabbar.addEventListener('click', function(e){
    const closeIdx = e.target.getAttribute('data-close');
    if (closeIdx !== null){
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
        window.webkit.messageHandlers.bridge.postMessage({type:"closeTab", index: parseInt(closeIdx)});
      }
      return;
    }
    if (e.target.id === "btnPlusTab"){
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
        window.webkit.messageHandlers.bridge.postMessage({type:"newTab"});
      }
      return;
    }
    let t = e.target; while(t && !t.classList.contains('tab')) t = t.parentNode;
    if(!t) return;
    const idx = parseInt(t.getAttribute('data-idx'), 10);
  
    // If you click the already-active tab, do nothing (so dblclick can rename)
    if (idx === __activeTabIndex) return;
  
    // Delay the switch slightly; if a dblclick happens, we'll cancel this
    if (__clickTimer) clearTimeout(__clickTimer);
    __clickTimer = setTimeout(function(){
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
        window.webkit.messageHandlers.bridge.postMessage({type:"switchTab", index: idx});
      }
      __clickTimer = null;
    }, 180);
  });
  

  tabbar.addEventListener('dblclick', function(e){
    if (__clickTimer) { clearTimeout(__clickTimer); __clickTimer = null; } // <-- cancel single-click
  
    // ignore dblclicks on the close “×”
    if (e.target && e.target.getAttribute('data-close') !== null) return;
  
    let t = e.target;
    while (t && !t.classList.contains('tab')) t = t.parentNode;
    if (!t) return;
  
    const idx = parseInt(t.getAttribute('data-idx'), 10);
    const labelEl = t.querySelector('.label');
    if (!labelEl) return;
  
    const orig = labelEl.textContent;
    const input = document.createElement('input');
    input.className = 'tabEdit';
    input.type = 'text';
    input.value = orig;
  
    const w = Math.max(100, Math.min(280, (labelEl.offsetWidth || 120) + 40));
    input.style.width = w + 'px';
  
    labelEl.style.display = 'none';
    t.insertBefore(input, labelEl);
    input.focus();
    input.select();
  
    let done = false;
    function commit(){
      if (done) return; done = true;
      const name = (input.value || '').trim();
      input.remove();
      labelEl.style.display = '';
      if (!name || name === orig) return;
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
        window.webkit.messageHandlers.bridge.postMessage({type:"renameTab", index: idx, name});
      }
    }
    function cancel(){
      if (done) return; done = true;
      input.remove();
      labelEl.style.display = '';
    }
  
  
    input.addEventListener('keydown', function(ev){
      if (ev.key === 'Enter') { commit(); }
      else if (ev.key === 'Escape') { cancel(); }
      ev.stopPropagation();
    });
    input.addEventListener('blur', commit);
  
    // don’t let the dblclick also trigger tab switching
    e.stopPropagation();
  });

  

  document.getElementById('btnCtx').onclick = function(){ toggle(ctxEl); };
  document.getElementById('btnSettings').onclick = function(){
    modal.style.display="flex";
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
      window.webkit.messageHandlers.bridge.postMessage({type:"getSettings"});
    }
  };
  document.getElementById('btnNew').onclick = function(){
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
      window.webkit.messageHandlers.bridge.postMessage({type:"newChat"});
    }
  };
  document.getElementById('btnSend').onclick = send;
  document.getElementById('btnSave').onclick = saveSettings;
  document.getElementById('btnClose').onclick = function(){ modal.style.display="none"; };

  // Execute/Copy/Edit delegation
  document.getElementById('chat').addEventListener('click', function(e){
    var t=e.target; while(t && !t.classList.contains('codeBtn')){ t=t.parentNode; }
    if(!t) return;

    const header = t.closest('.codeHeader');
    const next = header ? header.nextElementSibling : null;

    if (t.getAttribute('data-edit') !== null){
      const raw = decodeURIComponent(t.getAttribute('data-edit') || header.getAttribute('data-raw') || "");
      if (!next) return;
      const ta = document.createElement('textarea'); ta.className='codeEdit'; ta.value = raw;
      next.replaceWith(ta);
      header.setAttribute('data-raw', encodeURIComponent(raw));
      header.innerHTML =
        '<button class="codeBtn" data-run="1">Run</button>'+
        '<button class="codeBtn" data-done="1">Done</button>'+
        '<button class="codeBtn" data-cancel="1">Cancel</button>'+
        '<button class="codeBtn" data-copy="'+encodeURIComponent(raw)+'">Copy</button>';
      return;
    }

    if (t.getAttribute('data-run') !== null){
      let code = "";
      if (next && next.classList.contains('codeEdit')) code = next.value;
      else code = decodeURIComponent(header.getAttribute('data-raw') || "");
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
        window.webkit.messageHandlers.bridge.postMessage({type:"exec", code: code});
      }
      return;
    }

    if (t.getAttribute('data-done') !== null){
      if (!(next && next.classList.contains('codeEdit'))) return;
      const code = next.value;
      const encoded = encodeURIComponent(code);
      const pre = document.createElement('pre');
      const codeEl = document.createElement('code'); codeEl.className = 'lang-python';
      codeEl.innerHTML = colorPython(code);
      pre.appendChild(codeEl);
      next.replaceWith(pre);
      header.setAttribute('data-raw', encoded);
      header.innerHTML =
        '<button class="codeBtn" data-edit="'+encoded+'">Edit</button>'+
        '<button class="codeBtn" data-exec="'+encoded+'">Execute</button>'+
        '<button class="codeBtn" data-copy="'+encoded+'">Copy</button>';
      return;
    }

    if (t.getAttribute('data-cancel') !== null){
      const raw = decodeURIComponent(header.getAttribute('data-raw') || "");
      const pre = document.createElement('pre');
      const codeEl = document.createElement('code'); codeEl.className = 'lang-python';
      codeEl.innerHTML = colorPython(raw);
      if (next) next.replaceWith(pre);
      pre.appendChild(codeEl);
      const encoded = encodeURIComponent(raw);
      header.innerHTML =
        '<button class="codeBtn" data-edit="'+encoded+'">Edit</button>'+
        '<button class="codeBtn" data-exec="'+encoded+'">Execute</button>'+
        '<button class="codeBtn" data-copy="'+encoded+'">Copy</button>';
      return;
    }

    if (t.getAttribute('data-exec') !== null){
      var code = decodeURIComponent((t.getAttribute('data-exec') || header.getAttribute('data-raw') || ""));
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
        window.webkit.messageHandlers.bridge.postMessage({type:"exec", code: code});
      }
      return;
    }

    if (t.getAttribute('data-copy') !== null){
      let code = (next && next.classList.contains('codeEdit'))
        ? next.value
        : decodeURIComponent(t.getAttribute('data-copy') || header.getAttribute('data-raw') || "");
      copyText(code);
      return;
    }
  });

  function send(){
    var q = promptEl.value.trim(); if(!q) return;
    addBubble("user", q); promptEl.value="";
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
      window.webkit.messageHandlers.bridge.postMessage({type:"ask", prompt:q});
    }
  }

  function saveSettings(){
    var payload = {
      llmBase:  document.getElementById('s_lmBase').value.trim(),
      llmModel: document.getElementById('s_lmModel').value.trim(),
      llmKey:   document.getElementById('s_lmKey').value.trim(),
      ragURL:   document.getElementById('s_ragURL').value.trim(),
      ragToken: document.getElementById('s_ragToken').value.trim(),
      topK:     parseInt(document.getElementById('s_topK').value||"8"),
      useRAG:   document.getElementById('s_useRAG').checked,
      mode:     parseInt(document.getElementById('s_mode').value),
      remember: document.getElementById('s_remember').checked,
      maxContext: parseInt(document.getElementById('s_maxContext').value||"20000"),
      maxOutput:  parseInt(document.getElementById('s_maxOutput').value||"1024"),
      headroom:   parseInt(document.getElementById('s_headroom').value||"512"),
    };
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
      window.webkit.messageHandlers.bridge.postMessage({type:"saveSettings", settings: payload});
    }
  }

  // Native → JS
  window.__fromNative = function(msg){
    var type = msg.type, data = msg.data || {};
    if(type==="settings"){
      var p = data;
      document.getElementById('s_lmBase').value = p.llmBase||"";
      document.getElementById('s_lmModel').value = p.llmModel||"";
      document.getElementById('s_lmKey').value = p.llmKey||"";
      document.getElementById('s_ragURL').value = p.ragURL||"";
      document.getElementById('s_ragToken').value = p.ragToken||"";
      document.getElementById('s_topK').value = p.topK||8;
      document.getElementById('s_useRAG').checked   = asBool(p.useRAG);
      document.getElementById('s_mode').value = (p.mode||0);
      document.getElementById('s_remember').checked = asBool(p.remember);
      document.getElementById('s_maxContext').value = p.maxContext||20000;
      document.getElementById('s_maxOutput').value  = p.maxOutput||1024;
      document.getElementById('s_headroom').value   = p.headroom||512;

    } else if(type==="tabs"){
      renderTabs(data);

    } else if(type==="hydrate"){
      chatEl.innerHTML = "";
      (data.history||[]).forEach(item => addBubble(item.role, item.content));
      setContext("");

    } else if(type==="answer"){
      var answer = data.answer||"(no answer)";
      var context = data.context||"";
      addBubble("assistant", answer);
      if(context){ setContext(context); }

    } else if(type==="execResult"){
      var out = data.output || "";
      addBubble("system", "Execution output:\n```text\n"+out+"\n```");
      chatEl.scrollTop = chatEl.scrollHeight;

    } else if(type==="resetChat"){
      chatEl.innerHTML = ""; setContext(""); addBubble("system","New chat started.");

    } else if(type==="error"){
      addBubble("assistant", "ERROR: " + (data.message||""));

    } else if(type==="debug"){
      addBubble("system", data.message||"");
    }
  };

  if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
    window.webkit.messageHandlers.bridge.postMessage({type:"getSettings"});
  }
</script>
</body>
</html>
"""

_PRIVATE_HOST = re.compile(
    r"^(?:localhost|127\.0\.0\.1|10\..*|192\.168\..*|172\.(?:1[6-9]|2\d|3[0-1])\..*)$"
)

def _is_private_url(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        return bool(_PRIVATE_HOST.match(host))
    except Exception:
        return False

def _build_opener(url: str, insecure_https: bool) -> urllib.request.OpenerDirector:
    handlers = []
    # HTTPS handler (optionally unverified for private/self-signed)
    if url.lower().startswith("https"):
        ctx = ssl._create_unverified_context() if insecure_https else ssl.create_default_context()
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    # Bypass proxies for private/local hosts so corp proxies don’t interfere
    if _is_private_url(url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)

def http_post_json(url, payload, headers=None, timeout=25):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type":"application/json", **(headers or {})})
    try:
        if _is_private_url(url):  # bypass corp proxies only for 10./192.168./172.16-31/localhost
            opener = _build_opener(url, insecure_https=url.lower().startswith("https"))
            with opener.open(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
        else:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", "ignore")
        except Exception: pass
        raise RuntimeError(f"HTTP {e.code} {e.reason} from {url}\n{body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error calling {url}: {e.reason or e}") from e
    except Exception as e:
        raise RuntimeError(f"Request to {url} failed: {e}") from e
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}

def http_get_json(url, headers=None, timeout=10):
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    try:
        if _is_private_url(url):
            opener = _build_opener(url, insecure_https=url.lower().startswith("https"))
            with opener.open(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
        else:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", "ignore")
        except Exception: pass
        raise RuntimeError(f"HTTP {e.code} {e.reason} from {url}\n{body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error calling {url}: {e.reason or e}") from e
    except Exception as e:
        raise RuntimeError(f"Request to {url} failed: {e}") from e
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}


CHAT_ROLES = {"system","user","assistant"}

def _sanitize_messages(messages):
    fixed = []
    for m in messages:
        role = str(m.get("role","user"))
        if role not in CHAT_ROLES:
            # drop unknown roles (e.g. 'tool') for chat.completions
            continue
        content = m.get("content","")
        if not isinstance(content, str):
            content = str(content)
        fixed.append({"role": role, "content": content})
    return fixed

def _assert_chat_model_exists(p):
    base = (p.get("llmBase","")).rstrip("/")
    key  = p.get("llmKey","")
    if not base or not key: return
    url = base + "/models"
    headers = {"Authorization": "Bearer " + key}
    res = http_get_json(url, headers=headers)
    wanted = p.get("llmModel","").strip()
    ids = [ (it.get("id") or it.get("name") or "") for it in (res.get("data") or []) ]
    if wanted and wanted not in ids:
        # surface a clear, actionable message in the UI
        some = ", ".join(ids[:6]) + ("…" if len(ids)>6 else "")
        raise RuntimeError(
            f"Model '{wanted}' not found in your account. "
            f"Pick a Chat Completions model (e.g. 'gpt-4o' or 'gpt-4.1').\n"
            f"Available (sample): {some}"
        )

def rough_tokens(s):
    return max(1, len(s)//4)

def fit_messages_to_budget(messages, budget):
    def count(ms): return sum(rough_tokens(m.get("content","")) for m in ms)
    ms = messages[:]
    while count(ms) > budget and len(ms) > 2:
        for i,m in enumerate(ms):
            if m.get("role")!="system":
                del ms[i]; break
    return ms

# ---------- ObjC <-> Python coercion ----------
def objc_to_py(x):
    # Pass through None/JSON basics
    if x is None or isinstance(x, NSNull):
        return None
    if isinstance(x, NSString):
        return str(x)
    if isinstance(x, NSNumber):
        try:
            iv = int(x); fv = float(x)
            return iv if iv == fv else fv
        except Exception:
            try: return float(x)
            except Exception: return bool(x)

    # If GUI objects ever sneak in, don't try to iterate them
    if isinstance(x, (NSWindow, WKWebView, WKWebViewConfiguration, WKUserContentController)):
        return None  # or: return str(x)

    # NSDictionary: avoid direct NSArray iteration pitfalls
    if isinstance(x, NSDictionary):
        try:
            keys_arr = x.allKeys()
            n = int(getattr(keys_arr, "count", lambda: 0)())
            out = {}
            for i in range(n):
                k = keys_arr.objectAtIndex_(i)
                out[str(k)] = objc_to_py(x.objectForKey_(k))
            return out
        except Exception:
            # Not a normal dictionary after all — bail gracefully
            return str(x)

    # NSArray: index by count instead of for-in to dodge nsarray__iter__ surprises
    if isinstance(x, NSArray):
        try:
            n = int(x.count())
            return [objc_to_py(x.objectAtIndex_(i)) for i in range(n)]
        except Exception:
            return [str(x)]

    # Native Python containers
    if isinstance(x, dict):
        return {str(k): objc_to_py(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [objc_to_py(v) for v in x]
    if isinstance(x, bytes):
        return x.decode("utf-8", "ignore")

    # Fallback: stringify unknown objc objects
    return str(x)


def jsonable(x):
    x = objc_to_py(x)
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k,v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    return str(x)

def sanitize_output(s):
    if not isinstance(s, str):
        s = str(s)
    s = re.sub(r"<\|[^|>]{0,80}\|>", "", s)
    s = re.sub(r"\b(?:analysis|commentary|final)\s+to=\S+(?:\s+code)?", "", s, flags=re.I)
    s = s.replace("```python_user_visible", "```python").replace("```py", "```python")
    return s.strip()
    
def _as_bool(v):
    if isinstance(v, bool): return v
    if isinstance(v, NSNumber):
        try: return bool(int(v))
        except Exception: return False
    if isinstance(v, (int, float)): return v != 0
    if isinstance(v, str): return v.strip().lower() in ("1","true","yes","on","y","t")
    return bool(v)
    
import re

def normalize_model_markdown(text: str) -> str:
    """
    Only wrap as Python if the content actually looks like Python.
    Leave plain text alone. If fences exist, just standardize them.
    """
    s = (text or "").strip()
    if not s:
        return s

    # Already fenced? Standardize aliases and return.
    if "```" in s or "~~~" in s:
        return (
            s.replace("```py", "```python")
             .replace("```python_user_visible", "```python")
        )

    # Heuristic: is this Python-ish?
    lines = s.splitlines()
    cue = re.compile(
        r'^\s*(?:from|import|class|def|#|try:|except|finally:|with\b|for\b|while\b|if\b|@|'
        r'Glyphs\b|GS(?:Font|Glyph|Layer|Path)\b|[A-Za-z_]\w*\s*=)'
    )
    hits = sum(1 for l in lines if cue.search(l))

    # Not code → return as-is (this is the critical change)
    if hits < 2:
        return s

    # Looks like code → wrap (optionally keep a short lead-in if present)
    # find first cue line to split off any brief lead
    start = None
    for i, l in enumerate(lines):
        if cue.search(l):
            start = i
            break
    lead = ("\n".join(lines[:start]).strip() if start not in (None, 0) else "")
    body = ("\n".join(lines[start:]).strip() if start is not None else s)

    caption = "Here’s the code you asked for:"
    intro = (lead + "\n\n") if lead else (caption + "\n\n")
    return f"{intro}```python\n{body}\n```"

    
def _ensure_intro_line(user_q: str, answer: str) -> str:
    """
    If the model returned only code, prepend a one-line caption and ensure
    there is exactly one fenced ```python block. Works for:
    - fenced content starting with ``` or ~~~
    - raw Python code without fences (we'll wrap it)
    Otherwise, returns the answer unchanged.
    """
    if not isinstance(answer, str):
        return answer

    s = answer.lstrip()

    # Case A: already fenced – just add the caption above it
    if s.startswith("```") or s.startswith("~~~"):
        caption = "Here’s the code you asked for:"
        return caption + "\n\n" + answer

    # Case B: looks like raw Python (no fences): wrap + caption
    lines = s.splitlines()
    looks_like_py = (
        len(lines) >= 2 and
        (
            re.search(r'^\s*(from|import|class|def|#)', lines[0]) or
            re.search(r'\bGlyphs\b|\bGS(Font|Glyph|Layer|Path)\b', s)
        )
    )
    if looks_like_py:
        caption = "Here’s the code you asked for:"
        code = s.strip()
        return f"{caption}\n\n```python\n{code}\n```"

    # Not obviously code – leave it alone
    return answer


# -------- ObjC bridge (V3, hardened) --------
BRIDGE_CLASS_NAME = "GlyphsGPTBridgeV6"
try:
    Bridge = objc.lookUpClass(BRIDGE_CLASS_NAME)
except objc.nosuchclass_error:
    class GlyphsGPTBridgeV6(NSObject):
        def initWithOwner_(self, owner):
            self = objc.super(GlyphsGPTBridgeV6, self).init()
            if self is None:
                return None
            self.owner = owner
            return self

        def _coerce_dict(self, dct):
            py = {}
            for k in dct.allKeys():
                key = str(k); val = dct.objectForKey_(k)
                py[key] = self._coerce_any(val)
            return py

        def _coerce_array(self, arr):
            return [self._coerce_any(arr.objectAtIndex_(i)) for i in range(arr.count())]

        def _coerce_any(self, x):
            if x is None or isinstance(x, NSNull):
                return None
            if isinstance(x, dict) or isinstance(x, list):
                return x
            if isinstance(x, NSString):
                s = str(x)
                try: return json.loads(s)
                except Exception: return s
            if isinstance(x, NSDictionary): return self._coerce_dict(x)
            if isinstance(x, NSArray):      return self._coerce_array(x)
            if isinstance(x, (bytes, str)):
                try: return json.loads(x)
                except Exception: return x if not isinstance(x, bytes) else x.decode("utf-8","ignore")
            return x

        def userContentController_didReceiveScriptMessage_(self, controller, message):
            try:
                raw = message.body()
                payload = self._coerce_any(raw)
                t = payload.get("type") if isinstance(payload, dict) else None
                if not t:
                    try: self.owner.send_error("Bad message body (type missing)")
                    finally: return

                if   t == "getSettings": self.owner.send_settings()
                elif t == "saveSettings": self.owner.update_settings(payload.get("settings") or {})
                elif t == "ask":         self.owner.handle_ask(payload.get("prompt",""))
                elif t == "exec":        self.owner.handle_exec(payload.get("code",""))
                elif t == "newChat":     self.owner.new_chat()
                elif t == "switchTab":   self.owner.switch_tab(int(payload.get("index",0) or 0))
                elif t == "newTab":      self.owner.new_tab()
                elif t == "closeTab":    self.owner.close_tab(int(payload.get("index",0) or 0))
                elif t == "renameTab":
                    self.owner.rename_tab(payload.get("index", 0), payload.get("name", ""))
                else:
                    self.owner.send_error("Unknown type: %s" % t)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                try:
                    self.owner.send_error("Bridge error: %s" % e)
                    self.owner.debug(tb)
                except Exception:
                    print("[GlyphsGPT Bridge error]", e); print(tb)

    Bridge = GlyphsGPTBridgeV6

# -------------------------- Main window / logic --------------------------
class HTMLChatUI(object):
    def __init__(self):
        self._load_state()
        self._build_ui()
        try: self._auto_detect_limits()
        except Exception: pass

    # ---------- state (sessions/tabs) ----------
    def _load_state(self):
        root_raw = Glyphs.defaults.get(PREFKEY)
        try:
            root = objc_to_py(root_raw) if root_raw else {}
        except Exception as e:
            # stored defaults are corrupt (e.g. NSWindow snuck in) → reset
            try:
                # best effort wipe
                del Glyphs.defaults[PREFKEY]
            except Exception:
                Glyphs.defaults[PREFKEY] = {}
            root = {}
        if not isinstance(root, dict):
            root = {}

        if "sessions" not in root:
            ses = copy.deepcopy(SESSION_DEFAULTS)
            for k in SESSION_DEFAULTS.keys():
                if k in root and k != "history":
                    ses[k] = objc_to_py(root.get(k))
            ses["history"] = objc_to_py(root.get("history", []))
            ses["name"] = ses.get("name") or "Tab 1"
            self.sessions = [ses]
            self.active = 0
            self._save_all()
        else:
            self.sessions = objc_to_py(root.get("sessions")) or []
            if not isinstance(self.sessions, list) or not self.sessions:
                s = copy.deepcopy(SESSION_DEFAULTS); s["name"] = "Tab 1"
                self.sessions = [s]
            self.sessions = [objc_to_py(s) for s in self.sessions]
            for i,s in enumerate(self.sessions,1):
                if not s.get("name"): s["name"] = "Tab %d" % i
                s["history"] = [dict(role=str(h.get("role","")), content=str(h.get("content",""))) for h in (s.get("history") or []) if isinstance(h, dict)]
            self.active = int(root.get("active", 0) or 0)
            self.active = max(0, min(self.active, len(self.sessions)-1))

    def _save_all(self):
        payload = {"sessions": self.sessions, "active": int(self.active)}
        Glyphs.defaults[PREFKEY] = jsonable(payload)


    def cur(self):
        return self.sessions[self.active]

    def tab_names(self):
        return [s.get("name") or ("Tab %d"%(i+1)) for i,s in enumerate(self.sessions)]

    # ---------- UI ----------
    def _build_ui(self):
        self.cfg = WKWebViewConfiguration.alloc().init()
        self.ucc = WKUserContentController.alloc().init()
        self.bridge = Bridge.alloc().initWithOwner_(self)
        self.ucc.addScriptMessageHandler_name_(self.bridge, "bridge")
        self.cfg.setUserContentController_(self.ucc)

        self.win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(((100,100),(900,760)), 15, 2, False)
        self.win.setTitle_("GlyphsGPT with Chat (HTML)")
        self.web = WKWebView.alloc().initWithFrame_configuration_(((0,0),(900,760)), self.cfg)
        self.win.setContentView_(self.web)
        self.win.makeKeyAndOrderFront_(None)
        self.web.loadHTMLString_baseURL_(HTML, None)

    def _js(self, expression): self.web.evaluateJavaScript_completionHandler_(expression, None)
    def send(self, type_, data=None):
        payload = {"type": type_, "data": jsonable(data or {})}
        js_arg = json.dumps(payload, ensure_ascii=False)
        self._js("window.__fromNative(%s);" % js_arg)

    # after
    def debug(self, msg):
        if not DEBUG:
            return                 # <- don’t render a UI bubble unless DEBUG is on
        try:
            self.send("debug", {"message": str(msg)})
        except Exception:
            pass

    # ---------- tabs ----------
    def send_tabs(self):
        self.send("tabs", {
            "names":  [s.get("name") or ("Tab %d" % (i+1)) for i, s in enumerate(self.sessions)],
            "models": [s.get("llmModel","") for s in self.sessions],
            "active": int(self.active),
        })

    def switch_tab(self, idx):
        idx = int(idx)
        if idx<0 or idx>=len(self.sessions): return
        self.active = idx
        self._save_all()
        self.send_tabs()
        self.send_settings()
        self.send("hydrate", {"history": self.cur().get("history", [])})

    def new_tab(self):
        base = copy.deepcopy(objc_to_py(self.cur()))
        base["history"] = []
        base["name"] = "Tab %d" % (len(self.sessions)+1)
        self.sessions.append(base)
        self.active = len(self.sessions)-1
        self._save_all()
        self.send_tabs()
        self.send_settings()
        self.send("hydrate", {"history": []})

    def close_tab(self, idx):
        if len(self.sessions)<=1:
            self.cur()["history"] = []
            self._save_all()
            self.send_tabs()
            self.send("hydrate", {"history": []})
            return
        idx = int(idx)
        if idx<0 or idx>=len(self.sessions): return
        del self.sessions[idx]
        if self.active >= len(self.sessions):
            self.active = len(self.sessions)-1
        self._save_all()
        self.send_tabs()
        self.send_settings()
        self.send("hydrate", {"history": self.cur().get("history", [])})

    # ---------- settings ----------
    def send_settings(self):
        p = self.cur()
        self.send("settings", {
            "llmBase": p["llmBase"], "llmModel": p["llmModel"], "llmKey": p["llmKey"],
            "ragURL": p["ragURL"], "ragToken": p["ragToken"], "topK": p["topK"],
            "useRAG": _as_bool(p.get("useRAG", True)),          # ← was: bool(p["useRAG"])
            "mode": int(p.get("mode", 0) or 0),
            "remember": _as_bool(p.get("remember", True)),      # ← was: bool(p["remember"])
            "maxContext": int(p.get("maxContext",20000) or 20000),
            "maxOutput":  int(p.get("maxOutput",1024) or 1024),
            "headroom":   int(p.get("headroom",512) or 512),
        })
        self.send_tabs()
        self.send("hydrate", {"history": p.get("history", [])})

    def update_settings(self, s):
        p = self.cur()
        s = objc_to_py(s or {})
        p.update(dict(
            llmBase=str(s.get("llmBase","")).strip(),
            llmModel=str(s.get("llmModel","")).strip(),
            llmKey=str(s.get("llmKey","")).strip(),
            ragURL=str(s.get("ragURL","")).strip(),
            ragToken=str(s.get("ragToken","")).strip(),
            topK=int(s.get("topK",8) or 8),
            useRAG=_as_bool(s.get("useRAG", p.get("useRAG", True))),
            mode=int(s.get("mode",0) or 0),
            remember=_as_bool(s.get("remember", p.get("remember", True))),
            maxContext=int(s.get("maxContext", p.get("maxContext",20000)) or 20000),
            maxOutput=int(s.get("maxOutput", p.get("maxOutput",1024)) or 1024),
            headroom=int(s.get("headroom", p.get("headroom",512)) or 512),
        ))
        self._save_all()
        try: self._auto_detect_limits()
        except Exception: pass
        self.send_settings()

    def _budget_for(self, p):
        model_context  = int(p.get("maxContext", 20000) or 20000)
        reserved_output= int(p.get("maxOutput", 1024) or 1024)
        headroom       = int(p.get("headroom", 512) or 512)
        return max(512, model_context - reserved_output - headroom)

    def _auto_detect_limits(self):
        p = self.cur()
        base = (p.get("llmBase") or "").rstrip("/")
        model = p.get("llmModel") or ""
        if not base or not model: return
        url = base + "/models"
        headers = {"Authorization": "Bearer "+p["llmKey"]} if p.get("llmKey") else {}
        res = http_get_json(url, headers=headers)
        ctx = None
        try:
            for item in (res.get("data") or []):
                mid = item.get("id") or item.get("name") or ""
                if mid == model:
                    ctx = item.get("context_length") or item.get("max_context_length") \
                          or item.get("n_ctx") or item.get("max_position_embeddings")
                    break
        except Exception:
            ctx = None
        if isinstance(ctx, int) and ctx > 0:
            p["maxContext"] = ctx
            self._save_all()
            self.send("debug", {"message": "Detected model context: %d" % ctx})

    # ---------- chat ----------
    def new_chat(self):
        self.cur()["history"] = []
        self._save_all()
        self.send("resetChat", {})

    def handle_ask(self, prompt):
        q = (prompt or "").strip()
        if not q:
            self.send_error("Empty prompt")
            return

        p = self.cur()
        mode = int(p.get("mode", 0))
        include_rag = bool(p.get("useRAG", True))

        needs_code = bool(re.search(
            r"(?:\bscript\b|\bcode\b|\bpython\b|\bwrite\b|\bgenerate\b|```|GS(?:Font|Glyph|Layer|Path)|\bvanilla\b)",
            q, re.IGNORECASE
        ))
        
        # Preferred output instructions for code requests
        format_hint = (
            "\n\nRESPONSE FORMAT (preferred):\n"
            "1) A good enough explanation (2–5 sentences) describing what the script does and any caveats.\n"
            "2) Exactly ONE fenced ```python code block.\n"
            "3) Optional short note AFTER the code (≤ 3 sentences) for warnings, variants, or next steps.\n"
            "Do not include additional code blocks outside the one Python block."
        ) if needs_code else "\n\nFORMAT: text"
        
        ctx_text = ""; rag_chunks = 0; rag_top_score = None
        if include_rag and (mode in (0,1,2)):
            try:
                headers = {}
                if p.get("ragToken"):
                    headers["Authorization"] = "Bearer " + p["ragToken"]
                top_k_req = min(int(p.get("topK", 8) or 8), MAX_CONTEXT_CHUNKS)
                rag = http_post_json(p["ragURL"], {"query": q, "top_k": top_k_req}, headers=headers)
                results = rag.get("results", []) or []
                rag_chunks = len(results)
                if results and isinstance(results[0], dict):
                    rag_top_score = results[0].get("score", None)

                parts = []
                for i, r in enumerate(results[:MAX_CONTEXT_CHUNKS], 1):
                    meta = (r.get("meta", {}) or {})
                    src = meta.get("path") or meta.get("source") or "?"
                    txt = (r.get("text", "") or "").strip()
                    if len(txt) > CHUNK_CHAR_LIMIT:
                        txt = txt[:CHUNK_CHAR_LIMIT] + " …"
                    parts.append(f"[S{i}] {src}\n{txt}")
                ctx_text = "\n\n".join(parts)
            except Exception as e:
                self.debug("RAG error: %s" % e)
                ctx_text = ""; rag_chunks = 0; rag_top_score = None

        has_context = (rag_chunks > 0) and (rag_top_score is None or rag_top_score >= 0.40)

        hist = (p.get("history") or [])[-40:]
        messages = []

        if mode == 1:  # Grounded
            if not has_context and needs_code:
                self._finish_answer(p, q, "insufficient context", ctx_text)
                return
            base_prompt = PROMPT_GROUNDED if has_context else PROMPT_CHAT
            sys_prompt = base_prompt + format_hint
            if has_context:
                messages = [{"role": "system", "content": sys_prompt}] + hist + [
                    {"role": "user", "content": f"QUESTION:\n{q}\n\nCONTEXT:\n{ctx_text}"}
                ]
            else:
                messages = [{"role": "system", "content": sys_prompt}] + hist + [
                    {"role": "user", "content": q}
                ]

        elif mode == 2:  # Hybrid
            sys_prompt = PROMPT_GROUNDED + format_hint
            messages = [{"role": "system", "content": sys_prompt}] + hist + [
                {"role": "user", "content": f"QUESTION:\n{q}\n\nCONTEXT (optional):\n{ctx_text}"}
            ]

        elif mode == 3:  # Chat
            sys_prompt = PROMPT_CHAT + format_hint
            messages = [{"role": "system", "content": sys_prompt}] + hist + [
                {"role": "user", "content": q}
            ]

        else:  # Auto
            if has_context:
                sys_prompt = PROMPT_GROUNDED + format_hint
                messages = [{"role": "system", "content": sys_prompt}] + hist + [
                    {"role": "user", "content": f"QUESTION:\n{q}\n\nCONTEXT:\n{ctx_text}"}
                ]
            else:
                sys_prompt = PROMPT_CHAT + format_hint
                messages = [{"role": "system", "content": sys_prompt}] + hist + [
                    {"role": "user", "content": q}
                ]

        budget = self._budget_for(p)
        messages = fit_messages_to_budget(messages, budget)
        ans = self._chat(p, messages, temperature=0.2).strip()
        self._finish_answer(p, q, ans, ctx_text)

    def _finish_answer(self, p, q, ans, ctx_text):
        # Turn “prose + unfenced code” into “prose + ```python ...```”
        ans = normalize_model_markdown(ans)
    
        # Final cleanup of odd tokens / tag variants
        ans = sanitize_output(ans)
    
        if bool(p.get("remember", True)):
            p["history"] = (p.get("history") or []) + [
                {"role":"user","content": q},
                {"role":"assistant","content": ans},
            ]
            p["history"] = p["history"][-80:]
            self._save_all()
    
        self.send("answer", {"answer": ans, "context": ctx_text})


    # ---- Debug + compat shims (paste inside class HTMLChatUI) -----------------

    def _short(self, x, limit=1500):
        """Pretty, trimmed text for debug bubbles."""
        try:
            s = json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x
        except Exception:
            s = str(x)
        return s if len(s) <= limit else s[:limit] + " …(truncated)…"

    def _post_chat_with_compat(self, url, headers, payload_base, max_out):
        """
        POST to /chat/completions with graceful fallbacks:
        - max_tokens -> max_completion_tokens
        - drop temperature/top_p if the model only supports defaults
        - hint if the model expects the /v1/responses API
        - **NEW**: dynamic timeout + retry backoff for slow generations
        """
        pay = dict(payload_base)
        pay["max_tokens"] = int(max_out)
    
        # --- NEW: timeout based on size of the requested completion ---
        # 20s base + ~40ms per requested token, capped between 35s and 300s
        timeout_s = max(35, min(300, 20 + int(int(max_out) * 0.04)))
    
        attempts = 0
        last_err = None
        while attempts < 6:
            attempts += 1
            try:
                return http_post_json(url, pay, headers=headers, timeout=timeout_s)
            except RuntimeError as e:
                s = str(e)
                last_err = e
    
                # --- NEW: if we hit a timeout, back off and try again ---
                if ("timed out" in s.lower()) or ("timeout" in s.lower()) or ("deadline" in s.lower()):
                    timeout_s = min(300, int(timeout_s * 1.6) + 5)  # backoff
                    continue
    
                # 1) Token knob rename
                if ("Unsupported parameter" in s and "'max_tokens'" in s) and ("max_tokens" in pay):
                    pay.pop("max_tokens", None)
                    pay["max_completion_tokens"] = int(max_out)
                    continue
    
                # 2) Temperature locked to default
                if (('"param": "temperature"' in s) or ("Unsupported value: 'temperature'" in s)) and ("temperature" in pay):
                    pay.pop("temperature", None)
                    continue
    
                # 3) top_p locked to default
                if (('"param": "top_p"' in s) or ("Unsupported value: 'top_p'" in s)) and ("top_p" in pay):
                    pay.pop("top_p", None)
                    continue
    
                # 4) Responses API hint
                if ("responses" in s.lower()) or ("max_output_tokens" in s.lower()):
                    raise RuntimeError("This model expects the /v1/responses API (use max_output_tokens).") from e
    
                break  # not a known recoverable error
    
        raise last_err
    

    def _chat(self, p, messages, temperature=0.2):
        # Soft preflight: don't let /models failure block the request
        try:
            _assert_chat_model_exists(p)
        except Exception as e:
            self.debug(f"Model check skipped: {e}")

        url = "%s/chat/completions" % (p['llmBase'].rstrip('/'))
        headers = {"Authorization": "Bearer "+p['llmKey']} if p.get("llmKey") else {}

        payload_base = {
            "model": p["llmModel"].strip(),
            "messages": _sanitize_messages(messages),
            # knobs (server may prune these via the compat shim)
            "temperature": float(temperature),
            "top_p": 0.9,
        }
        max_out = int(p.get("maxOutput", 1024) or 1024)

        # Compact request summary (no message text)
        self.debug("Request → " + self._short({
            "url": url,
            "model": payload_base["model"],
            "n_messages": len(payload_base["messages"]),
            "has_key": bool(p.get("llmKey")),
            "max_out": max_out,
            "knobs": [k for k in ("temperature","top_p") if k in payload_base],
        }))

        res = self._post_chat_with_compat(url, headers, payload_base, max_out)

        # Surface server-side 'error' objects verbosely
        if isinstance(res, dict) and "error" in res:
            self.debug("Raw response (error):\n" + self._short(res))
            raise RuntimeError(self._short(res))

        # Normal chat.completions shape
        try:
            content = res["choices"][0]["message"]["content"]
        except Exception:
            # Show the whole JSON so we can see what's wrong
            import json
            self.debug("Unexpected response shape:\n```json\n"+json.dumps(res, ensure_ascii=False, indent=2)[:12000]+"\n```")
            raise RuntimeError("Unexpected LLM response (see debug bubble).")
            
        # >>> ADD THIS: show EXACT model text before any processing <<<
        try:
            self.debug("RAW model content (verbatim, before edits):\n```text\n"+content+"\n```")
        except Exception:
            pass

        if not isinstance(content, str) or not content.strip():
            self.debug("Empty content; raw response:\n" + self._short(res))
            raise RuntimeError("Model returned empty message (see debug bubble).")

        return content

    def rename_tab(self, idx, name):
        try: idx = int(idx)
        except Exception: return
        name = (name or "").strip()
        if not (0 <= idx < len(self.sessions)) or not name:
            return
        self.sessions[idx]["name"] = name
        self._save_all()
        self.send_tabs()
        
    def handle_exec(self, code):
        code = (code or "").strip()
        if not code:
            self.send("error", {"message":"(nothing to execute)"}); return
        buf = io.StringIO()
        try:
            import GlyphsApp as GA
            env = {"__builtins__": __builtins__, "Glyphs": GA.Glyphs}
            for name in dir(GA):
                if not name.startswith("_"):
                    try: env[name] = getattr(GA, name)
                    except Exception: pass
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                exec(code, env, env)
            out = buf.getvalue().strip() or "(done; no output)"
            self.send("execResult", {"output": out})
        except Exception:
            self.send("execResult", {"output": traceback.format_exc()})

    def send_error(self, msg):
        if DEBUG: print("[ERROR]", msg)
        self.send("error", {"message": str(msg)})

HTMLChatUI()
