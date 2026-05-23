# MenuTitle: GlyphsGPT with Chat
# -*- coding: utf-8 -*-
from __future__ import division, print_function, unicode_literals

__doc__ = """
GlyphsGPT with Chat
A standalone Glyphs script with an HTML chat UI that uses Codex.
- Direct mode: Codex + Glyphs MCP
- Code mode: Codex returns Glyphs Python code, displayed/editable/executable in-app
- Multi-tab sessions with persistent history
- Compact top chrome merged with the safe close-box version; prompt/response/console kept from the safe version
"""

import builtins
import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import traceback
import uuid
import ssl
import urllib.request
import urllib.error
import urllib.parse

import objc
import AppKit as AK
import Foundation as FN
from AppKit import NSWindow, NSPasteboard, NSPasteboardTypeString
from Foundation import NSObject, NSDictionary, NSString, NSArray, NSNull, NSNumber
from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController
from PyObjCTools.AppHelper import callAfter

from GlyphsApp import Glyphs

# --- Apple TLS bridge (NSURLSession + macOS trust store) --------------------
try:
    from Foundation import (
        NSURL, NSMutableURLRequest, NSData,
        NSURLSession, NSURLSessionConfiguration,
        NSDate, NSRunLoop
    )
    HAS_NSURLSESSION = True
except Exception:
    HAS_NSURLSESSION = False

REQUEST_TIMEOUT_S = 20.0
RESOURCE_TIMEOUT_S = 45.0
_PRIVATE_HOST = re.compile(
    r"^(localhost|127(?:\.\d{1,3}){3}|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2})$"
)
PREFER_APPLE_TLS = True

def _ns_request_json(method, url, body, headers, timeout=None):
    if not HAS_NSURLSESSION:
        raise RuntimeError("Apple TLS bridge unavailable on this Python.")

    req_to = float(timeout or REQUEST_TIMEOUT_S)
    res_to = float(max(timeout or RESOURCE_TIMEOUT_S, req_to * 2.0))

    req = NSMutableURLRequest.requestWithURL_(NSURL.URLWithString_(url))
    req.setHTTPMethod_(method or "GET")
    req.setTimeoutInterval_(req_to)
    req.setCachePolicy_(1)

    headers = dict(headers or {})
    headers.setdefault("User-Agent", "GlyphsGPTwithChat/AppleTLS")
    headers.setdefault("Connection", "close")
    for k, v in headers.items():
        req.setValue_forHTTPHeaderField_(str(v), str(k))

    if body is not None:
        data = NSData.dataWithBytes_length_(body, len(body))
        req.setHTTPBody_(data)

    cfg = NSURLSessionConfiguration.ephemeralSessionConfiguration()
    cfg.setTimeoutIntervalForRequest_(req_to)
    cfg.setTimeoutIntervalForResource_(res_to)
    session = NSURLSession.sessionWithConfiguration_(cfg)

    result = {"data": None, "error": None}

    def _done(data, response, error):
        result["data"] = data
        result["error"] = error

    task = session.dataTaskWithRequest_completionHandler_(req, _done)
    task.resume()

    deadline = NSDate.dateWithTimeIntervalSinceNow_(res_to)
    while result["data"] is None and result["error"] is None:
        if NSDate.date().timeIntervalSinceDate_(deadline) > 0:
            task.cancel()
            result["error"] = "Timed out"
            break
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))

    session.finishTasksAndInvalidate()

    err = result["error"]
    if err is not None:
        if err == "Timed out":
            raise RuntimeError("Apple TLS request failed: The request timed out.")
        try:
            msg = str(err.localizedDescription())
        except Exception:
            msg = str(err)
        raise RuntimeError("Apple TLS request failed: %s" % msg)

    data = result["data"] or NSData.data()
    raw = bytes(data).decode("utf-8", "ignore")
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"_raw": raw}

def _is_private_url(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        return bool(_PRIVATE_HOST.match(host))
    except Exception:
        return False

def _build_opener(url, insecure_https):
    handlers = []
    if str(url or "").lower().startswith("https"):
        ctx = ssl._create_unverified_context() if insecure_https else ssl.create_default_context()
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    if _is_private_url(url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)

def http_post_json(url, payload, headers=None, timeout=25):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", **(headers or {})}

    if str(url or "").lower().startswith("https") and HAS_NSURLSESSION and PREFER_APPLE_TLS:
        return _ns_request_json("POST", url, body, headers, timeout)

    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        if _is_private_url(url):
            opener = _build_opener(url, insecure_https=str(url or "").lower().startswith("https"))
            with opener.open(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
        else:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode("utf-8", "ignore")
        except Exception:
            body_txt = ""
        raise RuntimeError("HTTP %s %s from %s\n%s" % (e.code, e.reason, url, body_txt or e.reason))
    except Exception as e:
        s = str(e)
        if str(url or "").lower().startswith("https") and HAS_NSURLSESSION and (
            "CERTIFICATE_VERIFY_FAILED" in s or "ssl" in s.lower()
        ):
            return _ns_request_json("POST", url, body, headers, timeout)
        raise RuntimeError("Request failed for %s\n%s" % (url, e))

    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"_raw": raw}

def http_get_json(url, headers=None, timeout=10):
    headers = headers or {}

    if str(url or "").lower().startswith("https") and HAS_NSURLSESSION and PREFER_APPLE_TLS:
        return _ns_request_json("GET", url, None, headers, timeout)

    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        if _is_private_url(url):
            opener = _build_opener(url, insecure_https=str(url or "").lower().startswith("https"))
            with opener.open(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
        else:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode("utf-8", "ignore")
        except Exception:
            body_txt = ""
        raise RuntimeError("HTTP %s %s from %s\n%s" % (e.code, e.reason, url, body_txt or e.reason))
    except Exception as e:
        s = str(e)
        if str(url or "").lower().startswith("https") and HAS_NSURLSESSION and (
            "CERTIFICATE_VERIFY_FAILED" in s or "ssl" in s.lower()
        ):
            return _ns_request_json("GET", url, None, headers, timeout)
        raise RuntimeError("Request to %s failed: %s" % (url, e))

    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"_raw": raw}

def http_get(url, headers=None, timeout=2.0):
    try:
        res = http_get_json(url, headers=headers, timeout=timeout)
        if isinstance(res, dict) and "_raw" in res:
            return str(res.get("_raw") or "")
        return json.dumps(res, ensure_ascii=False)
    except Exception:
        return None

WINDOW_AUTOSAVE = "com.shotaronakano.GlyphsGPTwithChat.window"
APP_SINGLETON_KEY = "__GlyphsGPTwithChat_singleton__"
DEFAULT_SERVER = "glyphs-mcp-server"
DEFAULT_MODE = "direct"
DEFAULT_MODEL = ""
DEFAULT_PROVIDER = "codex"
DEFAULT_THEME = "dark"
STATE_DIR = os.path.expanduser("~/Library/Application Support/Glyphs 3")
STATE_PATH = os.path.join(STATE_DIR, "GlyphsGPTwithChat_state.json")
SCRIPT_BUILD = "2026-03-15.responses_api_appletls2"
DEFAULT_LMSTUDIO_PLUGIN = "mcp/glyphs-mcp"
DEFAULT_GLYPHS_MCP_URL = "http://127.0.0.1:9680/mcp/"

SESSION_DEFAULTS = {
    "name": "Chat 1",
    "mode": DEFAULT_MODE,
    "server": DEFAULT_SERVER,
    "model": DEFAULT_MODEL,
    "provider": DEFAULT_PROVIDER,
    "apiBase": "",
    "apiKey": "",
    "theme": DEFAULT_THEME,
    "copyToMacro": False,
    "history": [],
}

HTML = r'''<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>GlyphsGPT with Chat</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --panel2:#121722; --muted:#9ba6c4; --text:#e8ecff;
    --border:#262b36; --accent:#7aa2ff; --user:#1f2533; --assistant:#141a26; --code:#0b0f15;
    --good:#80d39b; --warn:#e6c070; --bad:#e57a7a;
    --tab:#151b27; --tab-active:#212a3d; --tab-hover:#29344a; --tab-edit:#202838;
    --shadow:0 18px 60px rgba(0,0,0,.28);
  }
  body.light {
    --bg:#f5f7fb; --panel:#ffffff; --panel2:#f3f5fa; --muted:#66718c; --text:#162033;
    --border:#d8deea; --accent:#355ee8; --user:#eaf0ff; --assistant:#ffffff; --code:#f6f8fc;
    --tab:#e9eef8; --tab-active:#ffffff; --tab-hover:#dfe7f7; --tab-edit:#ffffff;
    --shadow:0 18px 60px rgba(31,55,107,.12);
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,Segoe UI,sans-serif}
  .wrap{display:flex;flex-direction:column;height:100%;overflow:hidden}
  .top{padding:6px 10px 5px 10px;border-bottom:1px solid var(--border);background:var(--panel);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .titleRow{display:flex;align-items:center;gap:8px;min-width:0}
  .title{font-weight:700;letter-spacing:.15px;font-size:15px;line-height:1.2}
  .muted{color:var(--muted);font-size:12px}
  .subtitle{display:none}
  .topActions{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
  .pillset{display:flex;gap:6px;align-items:center}
  .pill{border:1px solid var(--border);background:#111722;color:var(--text);border-radius:7px;padding:5px 10px;cursor:pointer}
  body.light .pill{background:#edf2ff}
  .pill.active{border-color:#49639d;background:#18233a;color:#dfe8ff}
  body.light .pill.active{background:#dfe7ff;color:#173268;border-color:#90a6ea}
  .field{display:flex;align-items:center;gap:6px}
  .field input[type="text"], .field input[type="password"], .field select{
    height:30px;padding:0 10px;border:1px solid var(--border);border-radius:8px;background:#0f1320;color:var(--text)
  }
  body.light .field input[type="text"], body.light .field input[type="password"], body.light .field select,
  body.light textarea#prompt, body.light .modalCard input, body.light .modalCard select{
    background:#ffffff;
  }
  .field input.small{width:158px}
  .field input.tiny{width:110px}
  .check{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)}
  .spacer{flex:1}
  .btn{border:1px solid var(--border);background:#222739;color:#dce2ff;border-radius:8px;padding:7px 11px;cursor:pointer;white-space:nowrap}
  body.light .btn{background:#eef2ff;color:#16305f}
  .btn:hover{border-color:#334058}
  .btn:disabled{opacity:.45;cursor:default}
  .btn.compact{padding:5px 9px;font-size:12px;border-radius:7px}
  .btn.mini{padding:5px 10px;font-size:12px;border-radius:7px;min-width:60px}
  .btn.icon{width:28px;height:28px;padding:0;display:inline-flex;align-items:center;justify-content:center;font-size:14px;line-height:1}

  .tabWrap{padding:0 10px 6px 10px;border-bottom:1px solid var(--border);background:var(--panel)}
  .tabs{display:flex;gap:6px;overflow:auto;padding-top:4px}
  .tab{background:var(--tab); border:1px solid var(--border); border-radius:7px; padding:4px 8px; cursor:pointer; display:flex; align-items:center; gap:7px; white-space:nowrap; transition:background .15s ease,border-color .15s ease,box-shadow .15s ease}
  .tab:hover{background:var(--tab-hover)}
  .tab.active{background:var(--tab-active); border-color:#4c638f; box-shadow:inset 0 0 0 1px rgba(122,162,255,.18)}
  .tabLabel{display:inline-block;max-width:180px;overflow:hidden;text-overflow:ellipsis;font-size:13px}
  .x{font-size:12px; opacity:0.75; padding:0 3px; user-select:none}
  .x:hover{opacity:1; color:var(--bad)}
  .plus{background:#222739;color:#dce2ff;border:1px solid var(--border);border-radius:7px;padding:4px 8px;cursor:pointer;flex:0 0 auto;font-size:13px}
  body.light .plus{background:#eef2ff;color:#16305f}
  .plus:hover{border-color:#334058}
  .tabEdit{font:inherit; color:var(--text); background:var(--tab-edit); border:1px solid var(--border); border-radius:6px; padding:2px 6px; outline:none; box-shadow:inset 0 0 0 1px rgba(122,162,255,.08)}

  .advancedHost{border-bottom:1px solid var(--border);background:var(--panel)}
  .advancedPeek{padding:5px 10px 6px 10px;color:var(--muted);font-size:12px;line-height:1.35;display:flex;align-items:center;gap:8px;user-select:none;min-height:30px;flex-wrap:wrap}
  .advancedPeek::before{content:"⌄";font-size:11px;opacity:.8;line-height:1}
  .advancedLabel{white-space:nowrap}
  .peekModes{display:flex;gap:6px;align-items:center;margin-left:auto}
  .pill.peek{padding:3px 8px;font-size:11px;border-radius:999px}
  .advancedBar{max-height:0;opacity:0;overflow:hidden;padding:0 10px;display:flex;gap:10px 12px;align-items:center;flex-wrap:wrap;transition:max-height .18s ease, opacity .18s ease, padding .18s ease}
  .advancedHost:hover .advancedBar, .advancedHost:focus-within .advancedBar, .advancedHost.open .advancedBar{max-height:260px;opacity:1;padding:8px 10px 10px 10px}
  .advancedHost:hover .advancedPeek, .advancedHost:focus-within .advancedPeek, .advancedHost.open .advancedPeek{color:var(--text)}

  .chat{flex:1;overflow:auto;padding:14px 14px 0 14px}
  .msg{max-width:980px;margin:0 auto 12px auto}
  .msgHead{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:0 6px 4px 6px;color:var(--muted);font-size:11px;line-height:1.2}
  .msgRole{text-transform:capitalize;letter-spacing:.2px}
  .msgClose{appearance:none;border:1px solid var(--border);background:transparent;color:var(--muted);border-radius:6px;padding:0 7px;height:22px;line-height:20px;cursor:pointer;flex:0 0 auto}
  .msgClose:hover{color:var(--text);border-color:#4a5878;background:rgba(122,162,255,.08)}
  .bubble{padding:12px 14px;border:1px solid var(--border);border-radius:12px;white-space:pre-wrap;box-shadow:var(--shadow)}
  .bubble p{margin:0 0 10px 0}
  .bubble p:last-child{margin-bottom:0}
  .user{background:var(--user)} .assistant{background:var(--assistant)} .system{background:var(--panel2);color:var(--text)}

  .bar{padding:10px 12px;border-top:1px solid var(--border);background:var(--panel);display:flex;flex-direction:column;gap:8px;align-items:stretch;position:relative;z-index:20}
  textarea#prompt{width:100%;min-height:138px;max-height:280px;resize:vertical;padding:12px;border:1px solid var(--border);border-radius:10px;background:#0f1320;color:var(--text)}
  .bottomRow{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:34px}
  .leftActions,.rightActions{display:flex;align-items:center;gap:8px}
  .status{font-size:12px;color:var(--muted);min-height:18px}

  pre{background:var(--code);border:1px solid #1e2534;border-radius:10px;padding:12px;overflow:auto;margin:8px 0 0 0}
  body.light pre{border-color:#d6ddeb}
  code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
  .codeHeader{display:flex;gap:8px;justify-content:flex-end;margin-top:6px;flex-wrap:wrap}
  .codeBtn{border:1px solid var(--border);background:#1c2232;color:#cfe0ff;border-radius:6px;padding:3px 9px;font-size:12px;cursor:pointer}
  body.light .codeBtn{background:#eaf0ff;color:#14376f}
  .codeBtn:hover{border-color:#334058}

  .codeEditorWrap{position:relative;margin-top:8px;min-height:320px;border:1px solid #1e2534;border-radius:10px;background:var(--code);overflow:hidden}
  body.light .codeEditorWrap{border-color:#d6ddeb}
  .codePreview,.codePreview code,.codeEdit{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;line-height:1.45;letter-spacing:0;font-kerning:none;font-variant-ligatures:none;font-feature-settings:"liga" 0, "calt" 0;tab-size:4}
  .codePreview{position:absolute;inset:0;z-index:1;margin:0;padding:12px 12px 24px 12px;border:none;background:transparent;white-space:pre;overflow:auto;pointer-events:none;scrollbar-gutter:stable both-edges}
  .codePreview code{display:block;min-height:100%;white-space:inherit}
  .codeEdit{position:absolute;inset:0;z-index:2;width:100%;height:100%;min-height:320px;margin:0;padding:12px 12px 24px 12px;background:transparent;color:transparent;-webkit-text-fill-color:transparent;caret-color:var(--text);border:none;outline:none;white-space:pre;overflow:auto;resize:none;scrollbar-gutter:stable both-edges}
  .codeEdit::selection{background:rgba(122,162,255,.22)}

  pre code span.s { color:#a6e3a1 !important; }
  pre code span.c { color:#6c7086 !important; }
  pre code span.n { color:#cba6f7 !important; }
  pre code span.k { color:#89b4fa !important; }
  pre code span.b { color:#f38ba8 !important; }
  body.light pre code span.s { color:#0d7b3b !important; }
  body.light pre code span.c { color:#7a859f !important; }
  body.light pre code span.n { color:#7f42d9 !important; }
  body.light pre code span.k { color:#1756d1 !important; }
  body.light pre code span.b { color:#c53166 !important; }

  .modalOverlay{position:fixed;inset:0;background:rgba(0,0,0,.36);display:none;align-items:center;justify-content:center;padding:24px;z-index:1000}
  .modalOverlay.open{display:flex}
  .modalCard{width:min(720px,96vw);background:var(--panel);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);padding:18px}
  .modalHead{display:flex;align-items:center;gap:12px;margin-bottom:12px}
  .modalTitle{font-weight:700;font-size:16px}
  .modalGrid{display:grid;grid-template-columns:160px 1fr;gap:10px 12px;align-items:center}
  .modalGrid input,.modalGrid select{width:100%;height:36px;padding:0 10px;border:1px solid var(--border);border-radius:10px;background:#0f1320;color:var(--text)}
  .modalHint{font-size:12px;color:var(--muted);margin-top:10px}
  .modalActions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="titleRow">
      <div class="title" title="Direct = Codex + Glyphs MCP / Code = return editable Glyphs Python">GlyphsGPT with Chat</div>
      <div class="muted subtitle">Direct = Codex + Glyphs MCP / Code = return editable Glyphs Python</div>
    </div>
    <div class="spacer"></div>
    <div class="topActions">
      <button id="settingsBtn" class="btn icon" title="Settings" aria-label="Settings">⚙︎</button>
      <button id="clearBtn" class="btn compact">Clear Tab</button>
      <button id="openMacroBtn" class="btn compact">Open Macro</button>
    </div>
  </div>

  <div class="tabWrap">
    <div id="tabbar" class="tabs"></div>
  </div>

  <div id="advancedHost" class="advancedHost">
    <div id="advancedPeek" class="advancedPeek">
      <span id="advancedLabel" class="advancedLabel">Controls · Codex CLI</span>
      <div class="peekModes">
        <button id="modeDirect" class="pill peek active">Direct</button>
        <button id="modeCode" class="pill peek">Code</button>
      </div>
    </div>
    <div class="advancedBar">
      <div class="field"><span class="muted">Server</span><input id="server" class="small" type="text"/></div>
      <div class="field"><span class="muted">Model</span><input id="model" class="small" type="text" placeholder="default"/></div>
      <label class="check"><input id="copyToMacro" type="checkbox"/>Copy code to Macro</label>
      <div class="muted" id="providerBadge"></div>
      <div class="spacer"></div>
      <button id="sendBtnTop" class="btn mini">Send</button>
    </div>
  </div>

  <div id="chat" class="chat"></div>

  <div class="bar">
    <textarea id="prompt" placeholder="Ask Codex to control Glyphs, or ask for Glyphs Python code…"></textarea>
    <div class="bottomRow">
      <div class="leftActions">
        <button id="blankSnippetBtn" class="btn compact">Blank Snippet</button>
        <div id="status" class="status">Ready</div>
      </div>
      <div class="rightActions">
        <button id="sendBtn" class="btn">Send</button>
      </div>
    </div>
  </div>
</div>

<div id="settingsOverlay" class="modalOverlay">
  <div class="modalCard">
    <div class="modalHead">
      <div class="modalTitle">Tab Settings</div>
      <div class="muted">Saved per tab. New tabs inherit the current tab.</div>
    </div>
    <div class="modalGrid">
      <div class="muted">Provider</div>
      <select id="settingsProvider">
        <option value="codex">Codex CLI</option>
        <option value="openai">OpenAI API</option>
        <option value="anthropic">Claude API</option>
        <option value="openai_compat">Local / OpenAI-compatible</option>
      </select>

      <div class="muted">Model</div>
      <input id="settingsModel" type="text" placeholder="Model name"/>

      <div class="muted">Theme</div>
      <select id="settingsTheme">
        <option value="dark">Dark</option>
        <option value="light">Light</option>
      </select>

      <div class="muted">API Base</div>
      <input id="settingsApiBase" type="text" placeholder="https://api.openai.com/v1"/>

      <div class="muted">API Key</div>
      <input id="settingsApiKey" type="password" placeholder="Stored locally for this script" autocomplete="off"/>
    </div>
    <div class="modalHint" id="settingsHint">Codex uses the local CLI. OpenAI-compatible can point to LM Studio / Ollama-compatible gateways.</div>
    <div class="modalActions">
      <button id="settingsCancel" class="btn">Cancel</button>
      <button id="settingsSave" class="btn">Save</button>
    </div>
  </div>
</div>

<script>
const chatEl = document.getElementById('chat');
const promptEl = document.getElementById('prompt');
const statusEl = document.getElementById('status');
const modeDirectEl = document.getElementById('modeDirect');
const modeCodeEl = document.getElementById('modeCode');
const serverEl = document.getElementById('server');
const modelEl = document.getElementById('model');
const copyToMacroEl = document.getElementById('copyToMacro');
const sendBtn = document.getElementById('sendBtn');
const sendBtnTop = document.getElementById('sendBtnTop');
const blankSnippetBtn = document.getElementById('blankSnippetBtn');
const tabbar = document.getElementById('tabbar');
const providerBadge = document.getElementById('providerBadge');
const settingsOverlay = document.getElementById('settingsOverlay');
const settingsProviderEl = document.getElementById('settingsProvider');
const settingsModelEl = document.getElementById('settingsModel');
const settingsThemeEl = document.getElementById('settingsTheme');
const settingsApiBaseEl = document.getElementById('settingsApiBase');
const settingsApiKeyEl = document.getElementById('settingsApiKey');
const settingsHintEl = document.getElementById('settingsHint');
const advancedLabelEl = document.getElementById('advancedLabel');
let state = {mode:'direct', server:'glyphs-mcp-server', model:'', copyToMacro:false, provider:'codex', apiBase:'', apiKey:'', theme:'dark'};
let tabInfo = {names:['Chat 1'], active:0};
let __clickTimer = null;

function providerLabel(v){ return ({codex:'Codex CLI', openai:'OpenAI API', anthropic:'Claude API', openai_compat:'Local / OpenAI-compatible'})[v] || v; }
function applyTheme(){ document.body.classList.toggle('light', state.theme === 'light'); }
function looksLikeSentence(line){
  const t = String(line || '').trim();
  if (!t) return false;
  if (/[。！？.!?]$/.test(t)) return true;
  if (/\b(?:the|this|that|there|here|should|would|could|because|please|thanks|error|issue|mode|direct|code)\b/i.test(t) && /\s/.test(t)) return true;
  return false;
}
function esc(s){ return String(s||'').replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m])); }
function copyText(text){
  try { if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text); return; } } catch(e){}
  const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); } catch(e) {} document.body.removeChild(ta);
}
function cleanZW(s){ return String(s||'').replace(/[\u200B\u200C\u200D\u2060\uFEFF]/g, ''); }
function colorPython(code){
  let t = esc(code);
  t = t.replace(/('{3}[\s\S]*?'{3}|"{3}[\s\S]*?"{3})/g, m => '<span class="s">'+m+'</span>');
  t = t.replace(/'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"/g, m => '<span class="s">'+m+'</span>');
  const store = [];
  t = t.replace(/<span class="s">[\s\S]*?<\/span>/g, m => '@@S'+(store.push(m)-1)+'@@');
  t = t.replace(/\b(?:def|class|return|if|elif|else|for|while|try|except|finally|with|as|lambda|yield|import|from|pass|break|continue|in|is|and|or|not|assert|raise|global|nonlocal|True|False|None)\b/g, m => '<span class="k">'+m+'</span>');
  t = t.replace(/\b(?:print|len|range|dict|list|set|tuple|int|float|str|bool|sum|min|max|abs|isinstance|enumerate|zip|map|filter|any|all|open|sorted|reversed|super)\b/g, m => '<span class="b">'+m+'</span>');
  t = t.replace(/\b\d+(?:\.\d+)?\b/g, m => '<span class="n">'+m+'</span>');
  t = t.replace(/#.*$/gm, m => '<span class="c">'+m+'</span>');
  t = t.replace(/@@S(\d+)@@/g, (_,i) => store[+i]);
  return t;
}
function isPythonishLine(line){
  const t = String(line||'').trim();
  if (!t) return false;
  return /^(?:from|import|class|def|if|for|while|try|with|except|finally|return|lambda|@|#|pass|raise)\b/.test(t)
      || /\bGlyphs\b|\bGS(?:Font|Glyph|Layer|Path|Node|Component|Anchor|Instance|FontMaster)\b/.test(t)
      || /[A-Za-z_]\w*\s*=/.test(t)
      || /\.append\(|\.extend\(|\.remove\(|\.beginUndo\(|\.endUndo\(/.test(t);
}
function likelyPythonBlock(text){
  const t = cleanZW(String(text||'')).trim();
  if (!t) return false;
  if (/```|~~~/.test(t)) return false;
  const lines = t.split('\n').filter(line => String(line).trim());
  if (!lines.length) return false;
  let codeish = 0, sentenceish = 0;
  for (const line of lines){ if (isPythonishLine(line)) codeish++; if (looksLikeSentence(line)) sentenceish++; }
  const ratio = codeish / lines.length;
  if (lines.length >= 4 && ratio >= 0.75 && sentenceish <= 1) return true;
  if (lines.length >= 2 && ratio >= 0.9 && sentenceish === 0) return true;
  return false;
}
function renderTextBlock(block){ const txt = String(block || ''); if (!txt.trim()) return ''; if (likelyPythonBlock(txt)) return codeBlockHtml(txt.trim()); return '<p>' + esc(txt).replace(/\n/g, '<br>') + '</p>'; }
function renderMixedTextChunk(chunk){
  const src = String(chunk || '');
  if (!src.trim()) return '';
  const parts = src.split(/\n\s*\n/);
  let html = '';
  for (const part of parts){ if (part.trim()) html += renderTextBlock(part.trim()); }
  return html;
}
function makeReadOnlyBlock(code){ const pre = document.createElement('pre'); const codeEl = document.createElement('code'); codeEl.className = 'lang-python'; codeEl.innerHTML = colorPython(code || ''); pre.appendChild(codeEl); return pre; }
function initCodeEditorWrap(wrap){
  if (!wrap || wrap.getAttribute('data-ready') === '1') return;
  const preview = wrap.querySelector('.codePreview');
  const previewCode = preview ? preview.querySelector('code') : null;
  const ta = wrap.querySelector('textarea.codeEdit');
  if (!preview || !previewCode || !ta) return;
  wrap.setAttribute('data-ready', '1');
  function syncHeight(){
    const cs = window.getComputedStyle(ta);
    const lineHeight = parseFloat(cs.lineHeight) || 19;
    const padTop = parseFloat(cs.paddingTop) || 0;
    const padBottom = parseFloat(cs.paddingBottom) || 0;
    const lines = Math.max(1, String(ta.value || '').split('\n').length);
    const nextHeight = Math.max(320, Math.ceil(lines * lineHeight + padTop + padBottom + 28));
    wrap.style.height = nextHeight + 'px';
  }
  function syncScroll(){
    preview.scrollTop = ta.scrollTop;
    preview.scrollLeft = ta.scrollLeft;
  }
  function sync(){
    const value = ta.value || '';
    previewCode.innerHTML = colorPython(value || ' ');
    syncHeight();
    syncScroll();
  }
  ta.addEventListener('input', sync);
  ta.addEventListener('scroll', syncScroll);
  ta.addEventListener('keydown', function(e){
    if (e.key === 'Tab') {
      e.preventDefault();
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const value = ta.value || '';
      ta.value = value.slice(0, start) + '    ' + value.slice(end);
      ta.selectionStart = ta.selectionEnd = start + 4;
      sync();
    }
  });
  window.requestAnimationFrame(function(){ sync(); window.requestAnimationFrame(sync); });
}
function initCodeEditors(root){
  const scope = root || document;
  scope.querySelectorAll('.codeEditorWrap').forEach(initCodeEditorWrap);
}
function codeEditorHtml(code){
  const raw = String(code || '');
  return ''
    + '<div class="codeEditorWrap">'
    +   '<pre class="codePreview" aria-hidden="true"><code class="lang-python">'+colorPython(raw || ' ')+'</code></pre>'
    +   '<textarea class="codeEdit" spellcheck="false" wrap="off" autocapitalize="off" autocomplete="off" autocorrect="off">'+esc(raw)+'</textarea>'
    + '</div>';
}
function makeEditorWrap(code){
  const host = document.createElement('div');
  host.innerHTML = codeEditorHtml(code || '');
  const wrap = host.firstElementChild;
  initCodeEditorWrap(wrap);
  return wrap;
}
function getCodeFromRendered(header){
  const next = header ? header.nextElementSibling : null;
  const raw = decodeURIComponent((header && header.getAttribute('data-raw')) || '');
  if (!next) return raw;
  if (next.classList.contains('codeEditorWrap')) { const ta = next.querySelector('textarea.codeEdit'); return ta ? ta.value : raw; }
  if (next.classList.contains('codeEdit')) return next.value || raw;
  return raw;
}
function codeBlockHtml(code){
  const raw = String(code || ''); const encoded = encodeURIComponent(raw);
  return ''
    + '<div class="codeHeader" data-raw="'+encoded+'">'
    +   '<button class="codeBtn" data-run="1">Run</button>'
    +   '<button class="codeBtn" data-copy="1">Copy</button>'
    +   '<button class="codeBtn" data-copy-macro="1">Copy to Macro</button>'
    + '</div>'
    + codeEditorHtml(raw);
}
function mdToHtml(md){
  const src = cleanZW(String(md||'')).replace(/\r\n/g, '\n');
  if (!src.trim()) return '';
  const lines = src.split('\n');
  const openRe = /^\s*(```|~~~)\s*([A-Za-z0-9._+-]*)\s*.*$/;
  const closeRe = /^\s*(```|~~~)\s*.*$/;
  let html = '', inCode = false, lang = '', buf = [], textBuf = [];
  function flushText(){ if (!textBuf.length) return; html += renderMixedTextChunk(textBuf.join('\n')); textBuf = []; }
  function flushCode(){
    const raw = buf.join('\n'); const langNorm = (lang || '').toLowerCase(); if (langNorm === 'python_user_visible') lang = 'python';
    if (langNorm === 'python' || langNorm === 'py' || likelyPythonBlock(raw)) html += codeBlockHtml(raw);
    else {
      const encoded = encodeURIComponent(raw);
      html += ''
        + '<div class="codeHeader" data-raw="'+encoded+'">'
        +   '<button class="codeBtn" data-run="1">Run</button>'
        +   '<button class="codeBtn" data-copy="1">Copy</button>'
        +   '<button class="codeBtn" data-copy-macro="1">Copy to Macro</button>'
        + '</div>'
        + codeEditorHtml(raw);
    }
    buf = []; lang = ''; inCode = false;
  }
  for (let i = 0; i < lines.length; i++){
    const line = lines[i];
    if (!inCode){ const m = line.match(openRe); if (m){ flushText(); inCode = true; lang = m[2] || ''; continue; } textBuf.push(line); }
    else { if (closeRe.test(line)) { flushCode(); continue; } buf.push(line); }
  }
  if (inCode) flushCode(); flushText(); return html;
}
function createMsgShell(role, id){
  const wrap = document.createElement('div');
  wrap.className = 'msg';
  wrap.setAttribute('data-msg-id', id || '');
  wrap.innerHTML = ''
    + '<div class="msgHead">'
    +   '<span class="msgRole">'+esc(role || 'assistant')+'</span>'
    +   '<button class="msgClose" type="button" data-close-msg="'+esc(id || '')+'">×</button>'
    + '</div>';
  return wrap;
}
function addBubbleHtml(role, html, id){
  if (!String(html || '').trim()) return;
  const wrap = createMsgShell(role, id);
  const bubble = document.createElement('div');
  bubble.className = 'bubble ' + role;
  bubble.innerHTML = html;
  wrap.appendChild(bubble);
  chatEl.appendChild(wrap);
  initCodeEditors(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
}
function addUser(text, id){ addBubbleHtml('user', esc(text), id); }
function addText(role, text, id){ const t = String(text || '').trim(); if (!t) return; if (role === 'assistant') addBubbleHtml('assistant', mdToHtml(t), id); else addBubbleHtml(role, esc(t).replace(/\n/g,'<br>'), id); }
function addCode(role, code, id){ addBubbleHtml(role, codeBlockHtml(code || ''), id); }
function hydrateHistory(items){
  chatEl.innerHTML = '';
  (items || []).forEach(item => {
    const role = item.role || 'assistant', kind = item.kind || 'text', content = item.content || '', id = item.id || '';
    if (role === 'user') addUser(content, id); else if (kind === 'code') addCode(role, content, id); else addText(role, content, id);
  });
}
function syncProviderFields(){
  const provider = settingsProviderEl.value; const isCodex = provider === 'codex'; const isAnthropic = provider === 'anthropic'; const isLocal = provider === 'openai_compat';
  settingsApiBaseEl.placeholder = isLocal ? 'http://127.0.0.1:1234/v1' : (isAnthropic ? 'https://api.anthropic.com/v1' : 'https://api.openai.com/v1');
  settingsHintEl.textContent = isCodex
    ? 'Codex uses the local CLI. Direct mode can use Glyphs MCP.'
    : (isAnthropic
      ? 'Claude API uses the Anthropic Messages endpoint. Direct mode becomes normal chat; Code mode returns Glyphs Python.'
      : (isLocal
        ? 'Local / OpenAI-compatible uses the Responses API when available, with chat completions fallback for older servers. If API Base points to LM Studio, Direct mode can use Glyphs MCP through /v1/responses.'
        : 'OpenAI uses the Responses API. Direct mode becomes normal chat; Code mode returns Glyphs Python.'));
}
function syncUI(){
  modeDirectEl.classList.toggle('active', state.mode === 'direct');
  modeCodeEl.classList.toggle('active', state.mode === 'code');
  serverEl.value = state.server || 'glyphs-mcp-server';
  modelEl.value = state.model || '';
  copyToMacroEl.checked = !!state.copyToMacro;
  serverEl.disabled = !(state.mode === 'direct' && (state.provider === 'codex' || state.provider === 'openai_compat'));
  providerBadge.textContent = 'Provider: ' + providerLabel(state.provider || 'codex');
  if (advancedLabelEl) advancedLabelEl.textContent = 'Controls · ' + providerLabel(state.provider || 'codex');
  applyTheme();
}
function renderTabs(info){
  tabInfo = info || {names:['Chat 1'], active:0}; const names = tabInfo.names || ['Chat 1']; const active = tabInfo.active || 0; tabbar.innerHTML = '';
  names.forEach((name, i) => { const t = document.createElement('div'); t.className = 'tab' + (i === active ? ' active' : ''); t.setAttribute('data-idx', i); t.innerHTML = '<span class="tabLabel">'+esc(name || ('Chat ' + (i+1)))+'</span><span class="x" title="Close" data-close="'+i+'">×</span>'; tabbar.appendChild(t); });
  const plus = document.createElement('button'); plus.id = 'btnPlusTab'; plus.className = 'plus'; plus.textContent = '＋'; tabbar.appendChild(plus);
}
function openSettings(){ settingsProviderEl.value = state.provider || 'codex'; settingsModelEl.value = state.model || ''; settingsThemeEl.value = state.theme || 'dark'; settingsApiBaseEl.value = state.apiBase || ''; settingsApiKeyEl.value = state.apiKey || ''; syncProviderFields(); settingsOverlay.classList.add('open'); }
function closeSettings(){ settingsOverlay.classList.remove('open'); }
function sendAsk(){
  const prompt = (promptEl.value || '').trim(); if (!prompt) return;
  state.server = serverEl.value.trim() || 'glyphs-mcp-server'; state.model = modelEl.value.trim(); state.copyToMacro = copyToMacroEl.checked;
  promptEl.value = '';
  if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'ask', prompt:prompt, mode:state.mode, server:state.server, model:state.model, copyToMacro:state.copyToMacro, provider:state.provider, apiBase:state.apiBase, apiKey:state.apiKey, theme:state.theme});
}
function postBlankSnippet(){
  if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'blankSnippet'});
}
modeDirectEl.onclick = function(){ state.mode = 'direct'; syncUI(); };
modeCodeEl.onclick = function(){ state.mode = 'code'; syncUI(); };
sendBtn.onclick = sendAsk; sendBtnTop.onclick = sendAsk;
blankSnippetBtn.onclick = postBlankSnippet;
document.getElementById('clearBtn').onclick = function(){ if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'clearChat'}); };
document.getElementById('openMacroBtn').onclick = function(){ if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'openMacro'}); };
document.getElementById('settingsBtn').onclick = openSettings;
document.getElementById('settingsCancel').onclick = closeSettings;
document.getElementById('settingsSave').onclick = function(){
  state.provider = settingsProviderEl.value; state.model = settingsModelEl.value.trim(); state.theme = settingsThemeEl.value; state.apiBase = settingsApiBaseEl.value.trim(); state.apiKey = settingsApiKeyEl.value; modelEl.value = state.model || ''; syncUI(); closeSettings();
  if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'saveSettings', settings:{provider:state.provider, model:state.model, theme:state.theme, apiBase:state.apiBase, apiKey:state.apiKey}});
};
settingsProviderEl.onchange = syncProviderFields;
settingsOverlay.addEventListener('click', function(e){ if (e.target === settingsOverlay) closeSettings(); });
promptEl.addEventListener('keydown', function(e){ if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'Enter') { e.preventDefault(); postBlankSnippet(); return; } if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') sendAsk(); });

tabbar.addEventListener('click', function(e){
  const closeIdx = e.target.getAttribute('data-close');
  if (closeIdx !== null){ if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'closeTab', index: parseInt(closeIdx, 10)}); return; }
  if (e.target.id === 'btnPlusTab'){ if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'newTab'}); return; }
  let t = e.target; while (t && !t.classList.contains('tab')) t = t.parentNode; if (!t) return;
  const idx = parseInt(t.getAttribute('data-idx'), 10); if (idx === (tabInfo.active || 0)) return;
  if (__clickTimer) clearTimeout(__clickTimer);
  __clickTimer = setTimeout(function(){ if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'switchTab', index: idx}); __clickTimer = null; }, 180);
});

tabbar.addEventListener('dblclick', function(e){
  if (__clickTimer) { clearTimeout(__clickTimer); __clickTimer = null; }
  if (e.target && e.target.getAttribute('data-close') !== null) return;
  let t = e.target; while (t && !t.classList.contains('tab')) t = t.parentNode; if (!t) return;
  const idx = parseInt(t.getAttribute('data-idx'), 10); const labelEl = t.querySelector('.tabLabel'); if (!labelEl) return;
  const orig = labelEl.textContent; const input = document.createElement('input'); input.className = 'tabEdit'; input.type = 'text'; input.value = orig; input.style.width = Math.max(100, Math.min(260, (labelEl.offsetWidth || 120) + 40)) + 'px';
  labelEl.style.display = 'none'; t.insertBefore(input, labelEl); input.focus(); input.select();
  let done = false;
  function commit(){ if (done) return; done = true; const name = (input.value || '').trim(); input.remove(); labelEl.style.display = ''; if (!name || name === orig) return; if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'renameTab', index: idx, name: name}); }
  function cancel(){ if (done) return; done = true; input.remove(); labelEl.style.display = ''; }
  input.addEventListener('keydown', function(ev){ if (ev.key === 'Enter') commit(); else if (ev.key === 'Escape') cancel(); ev.stopPropagation(); });
  input.addEventListener('blur', commit); e.stopPropagation();
});

chatEl.addEventListener('click', function(e){
  const closeId = e.target && e.target.getAttribute && e.target.getAttribute('data-close-msg');
  if (closeId !== null && closeId !== '') {
    e.preventDefault();
    e.stopPropagation();
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
      window.webkit.messageHandlers.bridge.postMessage({type:'deleteMessage', id: closeId});
    }
    return;
  }

  let t = e.target; while (t && !t.classList.contains('codeBtn')) t = t.parentNode; if (!t) return;
  const header = t.closest('.codeHeader'); const next = header ? header.nextElementSibling : null; const raw = decodeURIComponent((header && header.getAttribute('data-raw')) || '');
  if (t.getAttribute('data-copy') !== null) { copyText(getCodeFromRendered(header)); return; }
  if (t.getAttribute('data-copy-macro') !== null) { const code = getCodeFromRendered(header); if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'copyToMacro', code:code}); return; }
  if (t.getAttribute('data-run') !== null) { const code = getCodeFromRendered(header); if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) window.webkit.messageHandlers.bridge.postMessage({type:'exec', code:code}); return; }
});

window.__fromNative = function(msg){
  const type = msg.type, data = msg.data || {};
  if (type === 'state') { state = Object.assign({}, state, data || {}); syncUI(); }
  else if (type === 'tabs') renderTabs(data);
  else if (type === 'hydrate') hydrateHistory(data.history || []);
  else if (type === 'busy') { sendBtn.disabled = !!data.busy; sendBtnTop.disabled = !!data.busy; blankSnippetBtn.disabled = !!data.busy; statusEl.textContent = data.message || (data.busy ? 'Running…' : 'Ready'); }
  else if (type === 'answerText') addText('assistant', data.text || '', data.id || '');
  else if (type === 'answerCode') addCode('assistant', data.code || '', data.id || '');
  else if (type === 'system') addText('system', data.text || '', data.id || '');
  else if (type === 'error') addText('assistant', 'ERROR\n' + (data.message || ''), data.id || '');
  else if (type === 'execResult') { const out = String(data.output || '').trim(); if (out) addText('system', 'Execution output\n' + out, data.id || ''); }
};

syncUI();
initCodeEditors(document);
if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.bridge) {
  window.webkit.messageHandlers.bridge.postMessage({type:'uiReady'});
  window.webkit.messageHandlers.bridge.postMessage({type:'getState'});
}
</script>
</body>
</html>
'''


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def objc_to_py(x):
    if x is None or isinstance(x, NSNull):
        return None
    if isinstance(x, NSString):
        return str(x)
    if isinstance(x, NSNumber):
        try:
            iv = int(x)
            fv = float(x)
            return iv if iv == fv else fv
        except Exception:
            try:
                return float(x)
            except Exception:
                return bool(x)
    if isinstance(x, NSDictionary):
        try:
            keys_arr = x.allKeys()
            n = int(keys_arr.count())
            out = {}
            for i in range(n):
                k = keys_arr.objectAtIndex_(i)
                out[str(k)] = objc_to_py(x.objectForKey_(k))
            return out
        except Exception:
            return str(x)
    if isinstance(x, NSArray):
        try:
            n = int(x.count())
            return [objc_to_py(x.objectAtIndex_(i)) for i in range(n)]
        except Exception:
            return [str(x)]
    if isinstance(x, dict):
        return {str(k): objc_to_py(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [objc_to_py(v) for v in x]
    if isinstance(x, bytes):
        return x.decode("utf-8", "ignore")
    return x


def jsonable(x):
    x = objc_to_py(x)
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    return str(x)


def extract_code_block(text):
    if not text:
        return ""
    m = re.search(r"```(?:python|py)?\s*\n([\s\S]*?)```", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text.strip()


BRIDGE_CLASS_NAME = "GlyphsGPTwithChatBridge"
try:
    GlyphsGPTwithChatBridge = objc.lookUpClass(BRIDGE_CLASS_NAME)
except objc.nosuchclass_error:
    class GlyphsGPTwithChatBridge(NSObject):
        def initWithOwner_(self, owner):
            self = objc.super(GlyphsGPTwithChatBridge, self).init()
            if self is None:
                return None
            self.owner = owner
            return self

        def userContentController_didReceiveScriptMessage_(self, controller, message):
            try:
                payload = objc_to_py(message.body()) or {}
                msgType = payload.get("type")
                if msgType == "uiReady":
                    self.owner.on_ui_ready()
                elif msgType == "getState":
                    self.owner.send_state()
                    self.owner.send_tabs()
                    self.owner.send_hydrate()
                elif msgType == "ask":
                    self.owner.handle_ask(payload)
                elif msgType == "stop":
                    self.owner.stop_run()
                elif msgType == "exec":
                    self.owner.handle_exec(payload.get("code", ""))
                elif msgType == "copyToMacro":
                    self.owner.copy_to_macro(payload.get("code", ""))
                elif msgType == "openMacro":
                    self.owner.open_macro()
                elif msgType == "switchTab":
                    self.owner.switch_tab(int(payload.get("index", 0) or 0))
                elif msgType == "newTab":
                    self.owner.new_tab()
                elif msgType == "closeTab":
                    self.owner.close_tab(int(payload.get("index", 0) or 0))
                elif msgType == "renameTab":
                    self.owner.rename_tab(int(payload.get("index", 0) or 0), payload.get("name", ""))
                elif msgType == "clearChat":
                    self.owner.clear_chat()
                elif msgType == "saveSettings":
                    self.owner.save_settings(payload.get("settings") or {})
                elif msgType == "blankSnippet":
                    self.owner.post_blank_snippet()
                elif msgType == "deleteMessage":
                    self.owner.delete_message(str(payload.get("id") or ""))
            except Exception as e:
                try:
                    self.owner.send_error("Bridge error: %s\n%s" % (e, traceback.format_exc()))
                except Exception:
                    print(traceback.format_exc())


class GlyphsGPTwithChat(object):

    def __init__(self):
        self._script_build = SCRIPT_BUILD
        self.window = None
        self.web = None
        self.bridge = None
        self.codexProcess = None
        self._busy = False
        self.active = 0
        self.sessions = []
        self._pageReady = False
        self._pendingMessages = []
        self._load_store()
        self._build_ui()

    # ---------- persistence ----------
    def _default_session(self, index=1):
        ses = copy.deepcopy(SESSION_DEFAULTS)
        ses["name"] = "Chat %d" % index
        ses["history"] = []
        return ses

    def _normalize_session(self, s, index):
        out = self._default_session(index)
        if isinstance(s, dict):
            out.update({
                "name": str(s.get("name") or out["name"]),
                "mode": str(s.get("mode") or out["mode"]).lower(),
                "server": str(s.get("server") or out["server"]),
                "model": str(s.get("model") or out["model"]),
                "provider": str(s.get("provider") or out["provider"]),
                "apiBase": str(s.get("apiBase") or out["apiBase"]),
                "apiKey": str(s.get("apiKey") or out["apiKey"]),
                "theme": str(s.get("theme") or out["theme"]),
                "copyToMacro": bool(s.get("copyToMacro", out["copyToMacro"])),
            })
            if out["mode"] not in ("direct", "code"):
                out["mode"] = DEFAULT_MODE
            if out["provider"] not in ("codex", "openai", "anthropic", "openai_compat"):
                out["provider"] = DEFAULT_PROVIDER
            if out["theme"] not in ("dark", "light"):
                out["theme"] = DEFAULT_THEME
            hist = []
            for item in objc_to_py(s.get("history") or []):
                if isinstance(item, dict):
                    hist.append({
                        "id": str(item.get("id") or uuid.uuid4().hex),
                        "role": str(item.get("role") or "assistant"),
                        "kind": str(item.get("kind") or "text"),
                        "content": str(item.get("content") or ""),
                    })
            out["history"] = hist
        return out

    def _load_store(self):
        data = {}
        try:
            if os.path.isfile(STATE_PATH):
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
        except Exception:
            data = {}
        sessions = data.get("sessions") or []
        if not isinstance(sessions, list) or not sessions:
            sessions = [self._default_session(1)]
        self.sessions = [self._normalize_session(s, i + 1) for i, s in enumerate(sessions)]
        try:
            self.active = int(data.get("active", 0) or 0)
        except Exception:
            self.active = 0
        self.active = max(0, min(self.active, len(self.sessions) - 1))

    def _save_store(self):
        try:
            ensure_dir(STATE_DIR)
            tmp = STATE_PATH + '.tmp'
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"sessions": self.sessions, "active": self.active}, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STATE_PATH)
        except Exception:
            pass

    def cur(self):
        return self.sessions[self.active]

    def _record(self, role, content, kind="text"):
        item_id = uuid.uuid4().hex
        self.cur().setdefault("history", []).append({
            "id": item_id,
            "role": str(role),
            "kind": str(kind or "text"),
            "content": str(content or ""),
        })
        self._save_store()
        return item_id

    # ---------- session / tab UI ----------
    def _session_ui_state(self):
        s = self.cur()
        return {
            "mode": s.get("mode", DEFAULT_MODE),
            "server": s.get("server", DEFAULT_SERVER),
            "model": s.get("model", DEFAULT_MODEL),
            "provider": s.get("provider", DEFAULT_PROVIDER),
            "apiBase": s.get("apiBase", ""),
            "apiKey": s.get("apiKey", ""),
            "theme": s.get("theme", DEFAULT_THEME),
            "copyToMacro": bool(s.get("copyToMacro", False)),
        }

    def send_tabs(self):
        self.send("tabs", {
            "names": [s.get("name") or ("Chat %d" % (i + 1)) for i, s in enumerate(self.sessions)],
            "active": int(self.active),
        })

    def send_hydrate(self):
        self.send("hydrate", {"history": self.cur().get("history", [])})

    def switch_tab(self, idx):
        idx = int(idx)
        if idx < 0 or idx >= len(self.sessions):
            return
        self.active = idx
        self._save_store()
        self.send_tabs()
        self.send_state()
        self.send_hydrate()

    def new_tab(self):
        src = self.cur()
        ses = self._default_session(len(self.sessions) + 1)
        ses["mode"] = src.get("mode", DEFAULT_MODE)
        ses["server"] = src.get("server", DEFAULT_SERVER)
        ses["model"] = src.get("model", DEFAULT_MODEL)
        ses["provider"] = src.get("provider", DEFAULT_PROVIDER)
        ses["apiBase"] = src.get("apiBase", "")
        ses["apiKey"] = src.get("apiKey", "")
        ses["theme"] = src.get("theme", DEFAULT_THEME)
        ses["copyToMacro"] = bool(src.get("copyToMacro", False))
        self.sessions.append(ses)
        self.active = len(self.sessions) - 1
        self._save_store()
        self.send_tabs()
        self.send_state()
        self.send_hydrate()

    def close_tab(self, idx):
        idx = int(idx)
        if idx < 0 or idx >= len(self.sessions):
            return
        if len(self.sessions) == 1:
            self.clear_chat()
            return
        del self.sessions[idx]
        if self.active >= len(self.sessions):
            self.active = len(self.sessions) - 1
        self._save_store()
        self.send_tabs()
        self.send_state()
        self.send_hydrate()

    def rename_tab(self, idx, name):
        idx = int(idx)
        if idx < 0 or idx >= len(self.sessions):
            return
        name = str(name or "").strip()
        if not name:
            return
        self.sessions[idx]["name"] = name
        self._save_store()
        self.send_tabs()

    def clear_chat(self):
        self.cur()["history"] = []
        self._save_store()
        self.send_hydrate()

    def post_blank_snippet(self):
        item_id = self._record("assistant", "", "code")
        self.send("answerCode", {"code": "", "id": item_id})

    def delete_message(self, message_id):
        message_id = str(message_id or "").strip()
        if not message_id:
            return
        cur = self.cur()
        old = cur.get("history", [])
        new = [item for item in old if str(item.get("id") or "") != message_id]
        if len(new) == len(old):
            return
        cur["history"] = new
        self._save_store()
        self.send_hydrate()

    def save_settings(self, settings):
        settings = objc_to_py(settings or {})
        cur = self.cur()
        provider = str(settings.get("provider") or cur.get("provider") or DEFAULT_PROVIDER).strip().lower()
        if provider not in ("codex", "openai", "anthropic", "openai_compat"):
            provider = DEFAULT_PROVIDER
        theme = str(settings.get("theme") or cur.get("theme") or DEFAULT_THEME).strip().lower()
        if theme not in ("dark", "light"):
            theme = DEFAULT_THEME
        cur["provider"] = provider
        cur["model"] = str(settings.get("model") or cur.get("model") or "")
        cur["apiBase"] = str(settings.get("apiBase") or cur.get("apiBase") or "")
        cur["apiKey"] = str(settings.get("apiKey") or cur.get("apiKey") or "")
        cur["theme"] = theme
        if "mode" in settings:
            mode = str(settings.get("mode") or cur.get("mode") or DEFAULT_MODE).strip().lower()
            cur["mode"] = mode if mode in ("direct", "code") else DEFAULT_MODE
        if "server" in settings:
            cur["server"] = str(settings.get("server") or cur.get("server") or DEFAULT_SERVER)
        if "copyToMacro" in settings:
            cur["copyToMacro"] = bool(settings.get("copyToMacro"))
        self._save_store()
        self.send_state()

    # ---------- window ----------
    def _ui_is_alive(self):
        try:
            return self.window is not None and self.web is not None and self.window.contentView() is not None
        except Exception:
            return False

    def _ensure_ui(self):
        if self._ui_is_alive():
            return
        self.window = None
        self.web = None
        self.bridge = None
        self._build_ui()

    def _build_ui(self):
        self._pageReady = False
        self._pendingMessages = []
        cfg = WKWebViewConfiguration.alloc().init()
        ucc = WKUserContentController.alloc().init()
        self.bridge = GlyphsGPTwithChatBridge.alloc().initWithOwner_(self)
        ucc.addScriptMessageHandler_name_(self.bridge, "bridge")
        cfg.setUserContentController_(ucc)

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(((80, 80), (1100, 820)), 15, 2, False)
        self.window.setTitle_("GlyphsGPT with Chat")
        try:
            self.window.setReleasedWhenClosed_(False)
        except Exception:
            pass
        try:
            self.window.setFrameAutosaveName_(WINDOW_AUTOSAVE)
        except Exception:
            pass

        self.web = WKWebView.alloc().initWithFrame_configuration_(((0, 0), (1100, 820)), cfg)
        self.window.setContentView_(self.web)
        self.web.loadHTMLString_baseURL_(HTML, None)

    def show(self):
        self._ensure_ui()
        self.window.makeKeyAndOrderFront_(None)
        self.send_state()
        self.send_tabs()
        self.send_hydrate()

    def _js(self, expression):
        if self.web is not None:
            self.web.evaluateJavaScript_completionHandler_(expression, None)

    def on_ui_ready(self):
        self._pageReady = True
        pending = list(self._pendingMessages)
        self._pendingMessages = []
        for payload in pending:
            self._js("window.__fromNative(%s);" % json.dumps(payload, ensure_ascii=False))
        self.send_state()
        self.send_tabs()
        self.send_hydrate()

    def send(self, type_, data=None):
        payload = {"type": type_, "data": jsonable(data or {})}
        if not self._pageReady:
            self._pendingMessages.append(payload)
            return
        self._js("window.__fromNative(%s);" % json.dumps(payload, ensure_ascii=False))

    def send_state(self):
        self.send("state", self._session_ui_state())

    def set_busy(self, busy, message=None):
        self._busy = bool(busy)
        self.send("busy", {"busy": self._busy, "message": message or ("Running…" if busy else "Ready")})

    def send_error(self, message, record=True):
        item_id = self._record("assistant", "ERROR\n" + str(message), "text") if record else ""
        self.send("error", {"message": str(message), "id": item_id})

    def send_system(self, text, record=True):
        item_id = self._record("system", str(text), "text") if record else ""
        self.send("system", {"text": str(text), "id": item_id})

    # ---------- codex ----------
    def _codex_path(self):
        candidates = [
            shutil.which("codex"),
            "/Applications/Codex.app/Contents/Resources/codex",
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
            os.path.expanduser("~/.local/bin/codex"),
        ]
        for path in candidates:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def _workspace_dir(self):
        try:
            font = Glyphs.font
            if font and font.filepath:
                folder = os.path.dirname(str(font.filepath))
                if os.path.isdir(folder):
                    return folder
        except Exception:
            pass
        return os.path.expanduser("~")

    def _font_context(self):
        lines = []
        try:
            font = Glyphs.font
            if font is None:
                return "No font is open in Glyphs."
            lines.append("Open font familyName: %s" % (font.familyName or ""))
            if font.filepath:
                lines.append("Open font path: %s" % font.filepath)
            try:
                selected = []
                for layer in font.selectedLayers:
                    if layer and layer.parent:
                        selected.append(layer.parent.name)
                if selected:
                    lines.append("Selected glyphs: %s" % ", ".join(selected[:20]))
            except Exception:
                pass
            try:
                if font.currentTab is not None and getattr(font.currentTab, "text", None):
                    lines.append("Current tab text: %s" % font.currentTab.text)
            except Exception:
                pass
        except Exception as e:
            lines.append("Font context unavailable: %s" % e)
        return "\n".join(lines)

    def _mcp_is_alive(self):
        raw = http_get("http://127.0.0.1:9680/mcp/", headers={"Accept": "application/json"}, timeout=1.5)
        return raw is not None

    def _history_for_prompt(self, limit=12):
        items = self.cur().get("history", [])[-limit:]
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "assistant").upper()
            kind = str(item.get("kind") or "text")
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if role == "SYSTEM" and (content.startswith("Execution output") or content == "Stopped."):
                continue
            if kind == "code":
                content = "```python\n%s\n```" % content
            out.append("[%s]\n%s" % (role, content))
        return "\n\n".join(out)

    def _build_prompt(self, provider, mode, server, userPrompt):
        ctx = self._font_context()
        hist = self._history_for_prompt()
        if mode == "code":
            return (
                "You are in CODE mode for Glyphs.\n"
                "Return only Python code for Glyphs 3 Python 3.11.\n"
                "Do NOT include explanation.\n"
                "Do NOT include markdown fences unless necessary.\n"
                "Include all required imports.\n"
                "Prefer current font and current selection rather than hard-coded paths.\n\n"
                "Glyphs context:\n%s\n\n"
                "Recent tab history:\n%s\n\n"
                "Current request:\n%s"
            ) % (ctx, hist or "(none)", userPrompt)
        if provider == "codex":
            return (
                "You are controlling Glyphs through Codex.\n"
                "Use the configured MCP server named '%s'.\n"
                "Do the task directly through MCP tools when possible.\n"
                "Do not return Python code unless explicitly asked.\n"
                "Return a concise summary of what you changed or found.\n\n"
                "Glyphs context:\n%s\n\n"
                "Recent tab history:\n%s\n\n"
                "Current request:\n%s"
            ) % (server, ctx, hist or "(none)", userPrompt)
        return (
            "You are a helpful assistant for Glyphs.\n"
            "In Direct mode here, answer normally because you cannot execute MCP tools.\n"
            "When code is useful, include fenced python blocks.\n\n"
            "Glyphs context:\n%s\n\n"
            "Recent tab history:\n%s\n\n"
            "Current request:\n%s"
        ) % (ctx, hist or "(none)", userPrompt)

    def _build_lmstudio_direct_system_prompt(self, plugin_id):
        ctx = self._font_context()
        hist = self._history_for_prompt()
        return (
            "You are controlling Glyphs through LM Studio with MCP access.\n"
            "Use the configured LM Studio integration '%s' whenever live Glyphs state or actions are needed.\n"
            "Prefer MCP tools over guessing whenever the request depends on the current font, selection, tab, layers, paths, or any mutable Glyphs state.\n"
            "Do not return Python code unless explicitly asked.\n"
            "Return a concise summary of what you changed or found.\n\n"
            "Glyphs context:\n%s\n\n"
            "Recent tab history:\n%s" 
        ) % (plugin_id, ctx, hist or "(none)")

    def _build_command(self, mode, server, model, outputPath):
        codex = self._codex_path()
        if not codex:
            raise RuntimeError("Could not find Codex CLI. Checked PATH and Codex.app bundled binary.")
        cmd = [
            codex,
            "exec",
            "--skip-git-repo-check",
            "--full-auto",
            "--output-last-message", outputPath,
        ]
        if model:
            cmd.extend(["-m", model])
        cmd.append("-")
        return cmd

    def _extract_first_code_block(self, text):
        if not text:
            return ""
        m = re.search(r"```(?:python|py)?\s*\n([\s\S]*?)```", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return ""

    def _build_api_messages(self, mode):
        system = self._build_prompt("api", mode, self.cur().get("server", DEFAULT_SERVER), "")
        system = re.sub(r"\n\nCurrent request:\n\s*$", "", system)
        messages = []
        for item in self.cur().get("history", [])[-14:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "assistant")
            kind = str(item.get("kind") or "text")
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if role == "system" and (content.startswith("Execution output") or content == "Stopped."):
                continue
            api_role = "user" if role == "user" else "assistant"
            if kind == "code":
                content = "```python\n%s\n```" % content
            elif role == "system":
                content = "[System note]\n" + content
            messages.append({"role": api_role, "content": content})
        return system, messages

    def _http_post_json(self, url, headers, payload, timeout=90):
        res = http_post_json(url, payload, headers=headers, timeout=timeout)
        if isinstance(res, dict):
            return res
        raise RuntimeError("Invalid JSON from %s\n%s" % (url, str(res)[:1000]))

    def _is_lmstudio_base(self, apiBase):
        base = str(apiBase or "").strip().lower()
        if not base:
            return True
        return ("127.0.0.1:1234" in base) or ("localhost:1234" in base)

    def _lmstudio_root(self, apiBase):
        base = str(apiBase or "http://127.0.0.1:1234/v1").strip() or "http://127.0.0.1:1234/v1"
        base = base.rstrip("/")
        for suffix in ("/api/v1", "/v1"):
            if base.endswith(suffix):
                return base[:-len(suffix)]
        return base

    def _lmstudio_headers(self, apiKey):
        headers = {"Content-Type": "application/json"}
        key = str(apiKey or "").strip()
        if key:
            headers["Authorization"] = "Bearer %s" % key
        return headers

    def _lmstudio_plugin_id(self, server):
        raw = str(server or "").strip()
        if not raw or raw == DEFAULT_SERVER:
            return DEFAULT_LMSTUDIO_PLUGIN
        if raw.startswith("mcp/"):
            return raw
        if raw in ("glyphs-mcp", "glyphs_mcp", "glyphsmcp"):
            return DEFAULT_LMSTUDIO_PLUGIN
        if raw == "glyphs-mcp-server":
            return DEFAULT_LMSTUDIO_PLUGIN
        if "/" in raw:
            return raw
        return "mcp/%s" % raw

    def _normalize_openai_base(self, apiBase, defaultBase):
        base = str(apiBase or defaultBase or "").strip()
        if not base:
            return ""
        base = base.rstrip("/")
        if base.endswith("/v1"):
            return base
        if re.search(r"/v1(?:/.*)?$", base):
            return re.sub(r"(/v1)(?:/.*)?$", r"\1", base)
        return base + "/v1"

    def _responses_input_from_messages(self, messages):
        items = []
        for item in messages or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip().lower() or "user"
            if role not in ("user", "assistant", "system", "developer"):
                role = "user"
            content_type = "output_text" if role == "assistant" else "input_text"
            content = item.get("content")
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        ptype = str(part.get("type") or "").strip()
                        if ptype == "refusal" and role == "assistant":
                            refusal = str(part.get("refusal") or part.get("text") or "").strip()
                            if refusal:
                                parts.append({"type": "refusal", "refusal": refusal})
                            continue
                        text = str(part.get("text") or part.get("content") or part.get("value") or "").strip()
                    else:
                        text = str(part or "").strip()
                    if text:
                        parts.append({"type": content_type, "text": text})
                if not parts:
                    continue
                items.append({"role": role, "content": parts})
                continue
            text = str(content or "").strip()
            if not text:
                continue
            items.append({"role": role, "content": [{"type": content_type, "text": text}]})
        return items

    def _extract_responses_text(self, res):
        texts = []
        tool_notes = []
        approval_notes = []

        def _append_textish(val):
            if val is None:
                return
            if isinstance(val, str):
                s = val.strip()
                if s:
                    texts.append(s)
                return
            if isinstance(val, list):
                for it in val:
                    _append_textish(it)
                return
            if isinstance(val, dict):
                t = str(val.get("type") or "").strip()
                if t in ("output_text", "text", "input_text", "summary_text"):
                    txt = val.get("text")
                    if isinstance(txt, dict):
                        txt = txt.get("value") or txt.get("text") or ""
                    s = str(txt or "").strip()
                    if s:
                        texts.append(s)
                    return
                if "text" in val or "content" in val or "value" in val or "summary" in val:
                    _append_textish(val.get("text") or val.get("content") or val.get("value") or val.get("summary"))
                return

        _append_textish(res.get("output_text"))

        for item in (res.get("output") or []):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            if item_type == "message":
                _append_textish(item.get("content"))
            elif item_type in ("reasoning", "summary"):
                _append_textish(item.get("summary") or item.get("content"))
            elif item_type in ("function_call", "mcp_call", "mcp_tool_call"):
                name = str(item.get("name") or item.get("tool_name") or item.get("tool") or item_type).strip()
                if name:
                    tool_notes.append(name)
            elif item_type == "mcp_approval_request":
                label = str(item.get("server_label") or "MCP").strip()
                name = str(item.get("name") or "").strip()
                approval_notes.append("Approval required for %s%s" % (label, (": " + name) if name else ""))

        texts = [t for t in texts if t]
        if texts:
            return "\n\n".join(texts)

        err = res.get("error") or {}
        err_msg = str(err.get("message") or "").strip()
        if err_msg:
            raise RuntimeError(err_msg)

        incomplete = res.get("incomplete_details") or {}
        reason = str(incomplete.get("reason") or "").strip()
        if reason:
            raise RuntimeError("Response incomplete: %s" % reason)

        if approval_notes:
            raise RuntimeError("\n".join(approval_notes))
        if tool_notes:
            return "Tools ran but no final message was returned.\n" + "\n".join(tool_notes)

        try:
            sample = json.dumps({
                "status": res.get("status"),
                "output": res.get("output"),
                "incomplete_details": res.get("incomplete_details"),
            }, ensure_ascii=False)[:2000]
        except Exception:
            sample = str(res)[:2000]
        raise RuntimeError("Provider returned no message. Raw response: %s" % sample)

    def _call_openai_responses(self, apiBase, apiKey, model, system, messages, tools=None, temperature=None, timeout=180):
        base = self._normalize_openai_base(apiBase, "https://api.openai.com/v1")
        if not model:
            raise RuntimeError("Set a model in Settings.")
        headers = {"Content-Type": "application/json"}
        if apiKey:
            headers["Authorization"] = "Bearer %s" % apiKey
        payload = {
            "model": model,
            "instructions": system,
            "input": self._responses_input_from_messages(messages),
            "text": {"format": {"type": "text"}},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if temperature is not None:
            payload["temperature"] = temperature
        res = self._http_post_json(base + "/responses", headers, payload, timeout=timeout)
        return self._extract_responses_text(res)

    def _call_openai_like(self, apiBase, apiKey, model, system, messages):
        base = self._normalize_openai_base(apiBase, "https://api.openai.com/v1")
        if not model:
            raise RuntimeError("Set a model in Settings.")
        headers = {"Content-Type": "application/json"}
        if apiKey:
            headers["Authorization"] = "Bearer %s" % apiKey
        payload = {"model": model, "messages": [{"role": "system", "content": system}] + messages}
        res = self._http_post_json(base + "/chat/completions", headers, payload, timeout=120)
        choice = ((res.get("choices") or [{}])[0] or {})
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content or "")

    def _use_chat_completions_fallback(self, exc):
        txt = str(exc or "")
        lowered = txt.lower()
        return (
            "http 404" in lowered
            or "not found" in lowered
            or "/responses" in lowered
            or "unknown path" in lowered
            or "unsupported" in lowered
            or "unrecognized request url" in lowered
        )

    def _lmstudio_mcp_label_and_url(self, server):
        raw = str(server or "").strip()
        if not raw or raw == DEFAULT_SERVER:
            return "glyphs-mcp", DEFAULT_GLYPHS_MCP_URL
        if raw.startswith("http://") or raw.startswith("https://"):
            label = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw.split("://", 1)[-1]).strip("-") or "glyphs-mcp"
            return label, raw
        if raw.startswith("mcp/"):
            label = raw.split("/", 1)[1].strip() or "glyphs-mcp"
            return label, DEFAULT_GLYPHS_MCP_URL
        if raw in ("glyphs-mcp", "glyphs_mcp", "glyphsmcp", "glyphs-mcp-server"):
            return "glyphs-mcp", DEFAULT_GLYPHS_MCP_URL
        return re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-") or "glyphs-mcp", DEFAULT_GLYPHS_MCP_URL

    def _build_lmstudio_responses_tools(self, server):
        label, url = self._lmstudio_mcp_label_and_url(server)
        return [{"type": "mcp", "server_label": label, "server_url": url}]

    def _flatten_messages_for_input(self, messages):
        parts = []
        for item in messages or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip().lower() or "user"
            content = item.get("content")
            if isinstance(content, list):
                text = "\n".join(str((part or {}).get("text") or (part or {}).get("content") or "").strip() if isinstance(part, dict) else str(part or "").strip() for part in content)
            else:
                text = str(content or "").strip()
            text = text.strip()
            if text:
                parts.append("%s: %s" % (role.capitalize(), text))
        return "\n\n".join(parts).strip()

    def _call_lmstudio_responses(self, apiBase, apiKey, model, server, system, messages):
        tools = self._build_lmstudio_responses_tools(server)
        try:
            return self._call_openai_responses(apiBase, apiKey, model, system, messages, tools=tools, temperature=0, timeout=180)
        except Exception as e:
            lowered = str(e or "").lower()
            if "invalid type for 'input'" not in lowered and "invalid_union" not in lowered:
                raise
            base = self._normalize_openai_base(apiBase, "http://127.0.0.1:1234/v1")
            if not model:
                raise RuntimeError("Set a model in Settings.")
            headers = {"Content-Type": "application/json"}
            if apiKey:
                headers["Authorization"] = "Bearer %s" % apiKey
            payload = {
                "model": model,
                "instructions": system,
                "input": self._flatten_messages_for_input(messages),
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0,
            }
            res = self._http_post_json(base + "/responses", headers, payload, timeout=180)
            return self._extract_responses_text(res)

    def _call_lmstudio_chat(self, apiBase, apiKey, model, server, prompt):
        if not model:
            raise RuntimeError("Set a model in Settings.")
        root = self._lmstudio_root(apiBase)
        headers = self._lmstudio_headers(apiKey)
        label, url = self._lmstudio_mcp_label_and_url(server)
        system = self._build_lmstudio_direct_system_prompt("mcp/%s" % label)
        payload = {
            "model": model,
            "input": prompt,
            "system_prompt": system,
            "integrations": [{
                "type": "ephemeral_mcp",
                "server_label": label,
                "server_url": url,
            }],
            "temperature": 0,
        }
        res = self._http_post_json(root + "/api/v1/chat", headers, payload, timeout=180)
        output = res.get("output") or []
        texts = []
        tool_notes = []
        invalids = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type == "message":
                content = str(item.get("content") or "").strip()
                if content:
                    texts.append(content)
            elif item_type == "tool_call":
                tool = str(item.get("tool") or "").strip()
                provider_info = item.get("provider_info") or {}
                source = str(provider_info.get("plugin_id") or provider_info.get("server_label") or "").strip()
                note = tool or "tool_call"
                if source:
                    note += " @ " + source
                tool_notes.append(note)
            elif item_type == "invalid_tool_call":
                reason = str(item.get("reason") or "Invalid LM Studio tool call.").strip()
                meta = item.get("metadata") or {}
                tool_name = str(meta.get("tool_name") or "").strip()
                if tool_name:
                    reason += " Tool: %s" % tool_name
                invalids.append(reason)
        texts = [t for t in texts if t]
        if texts:
            return texts[-1]
        if invalids:
            raise RuntimeError("\n".join(invalids))
        if tool_notes:
            return "Tools ran but no final message was returned.\n" + "\n".join(tool_notes)
        raise RuntimeError("LM Studio returned no message.")

    def _call_anthropic(self, apiBase, apiKey, model, system, messages):
        if not apiKey:
            raise RuntimeError("Set an Anthropic API key in Settings.")
        if not model:
            raise RuntimeError("Set a model in Settings.")
        base = (apiBase or "https://api.anthropic.com/v1").rstrip("/")
        headers = {"Content-Type": "application/json", "x-api-key": apiKey, "anthropic-version": "2023-06-01"}
        payload = {"model": model, "max_tokens": 4096, "system": system, "messages": messages}
        res = self._http_post_json(base + "/messages", headers, payload, timeout=120)
        return "".join(part.get("text") or "" for part in (res.get("content") or []) if isinstance(part, dict) and part.get("type") == "text")

    def _run_api_thread(self, provider, mode, copyToMacro):
        text = ""
        errorText = ""
        try:
            cur = self.cur()
            system, messages = self._build_api_messages(mode)
            if provider == "anthropic":
                text = self._call_anthropic(cur.get("apiBase", ""), cur.get("apiKey", ""), cur.get("model", ""), system, messages)
            else:
                base = cur.get("apiBase", "")
                key = cur.get("apiKey", "")
                if provider == "openai_compat" and not base:
                    base = "http://127.0.0.1:1234/v1"
                elif provider == "openai_compat" and self._is_lmstudio_base(base):
                    base = self._lmstudio_root(base) + "/v1"
                elif provider == "openai" and not key:
                    raise RuntimeError("Set an OpenAI API key in Settings.")

                if provider == "openai":
                    try:
                        text = self._call_openai_responses(base, key, cur.get("model", ""), system, messages)
                    except Exception as e:
                        lowered = str(e or "").lower()
                        if mode == "direct" and ("provider returned no message" in lowered or "response incomplete" in lowered):
                            text = self._call_openai_like(base, key, cur.get("model", ""), system, messages)
                        else:
                            raise
                elif provider == "openai_compat" and mode == "direct" and self._is_lmstudio_base(base):
                    try:
                        text = self._call_lmstudio_responses(base, key, cur.get("model", ""), cur.get("server", DEFAULT_SERVER), system, messages)
                    except Exception as e:
                        if self._use_chat_completions_fallback(e) or "invalid type for 'input'" in str(e or "").lower() or "invalid_union" in str(e or "").lower():
                            text = self._call_lmstudio_chat(base, key, cur.get("model", ""), cur.get("server", DEFAULT_SERVER), self._last_user_prompt())
                        else:
                            raise
                else:
                    try:
                        text = self._call_openai_responses(base, key, cur.get("model", ""), system, messages)
                    except Exception as e:
                        if self._use_chat_completions_fallback(e):
                            text = self._call_openai_like(base, key, cur.get("model", ""), system, messages)
                        else:
                            raise
        except Exception:
            errorText = traceback.format_exc()
        callAfter(self._finish_run, provider, mode, text, "", "", errorText, copyToMacro)

    def _last_user_prompt(self):
        hist = self.cur().get("history", [])
        for item in reversed(hist):
            if isinstance(item, dict) and str(item.get("role") or "") == "user":
                return str(item.get("content") or "")
        return ""

    def handle_ask(self, payload):
        if self._busy:
            return
        payload = objc_to_py(payload or {})
        prompt = (payload.get("prompt") or "").strip()
        mode = (payload.get("mode") or DEFAULT_MODE).strip().lower()
        server = (payload.get("server") or DEFAULT_SERVER).strip() or DEFAULT_SERVER
        model = (payload.get("model") or "").strip()
        copyToMacro = bool(payload.get("copyToMacro"))

        cur = self.cur()
        provider = str(payload.get("provider") or cur.get("provider") or DEFAULT_PROVIDER).strip().lower()
        if provider not in ("codex", "openai", "anthropic", "openai_compat"):
            provider = DEFAULT_PROVIDER
        theme = str(payload.get("theme") or cur.get("theme") or DEFAULT_THEME).strip().lower()
        if theme not in ("dark", "light"):
            theme = DEFAULT_THEME

        cur.update({
            "mode": mode if mode in ("direct", "code") else DEFAULT_MODE,
            "server": server,
            "model": model,
            "copyToMacro": copyToMacro,
            "provider": provider,
            "apiBase": str(payload.get("apiBase") or cur.get("apiBase") or ""),
            "apiKey": str(payload.get("apiKey") or cur.get("apiKey") or ""),
            "theme": theme,
        })
        self._save_store()
        self.send_state()

        if not prompt:
            self.send_error("Empty prompt.")
            return

        user_id = self._record("user", prompt, "text")
        self.send("answerText", {"text": prompt, "id": user_id})

        provider = cur.get("provider", DEFAULT_PROVIDER)
        if provider == "codex" and cur["mode"] == "direct" and not self._mcp_is_alive():
            self.send_error(
                "Glyphs MCP server is not responding at http://127.0.0.1:9680/mcp/\n"
                "In Glyphs, run: Edit → Start Glyphs MCP Server"
            )
            return

        if provider == "codex":
            finalPrompt = self._build_prompt(provider, cur["mode"], server, prompt)
            self.set_busy(True, "Running Codex…")
            thread = threading.Thread(target=self._run_codex_thread, args=(cur["mode"], finalPrompt, model, copyToMacro, server))
        else:
            self.set_busy(True, "Running %s…" % provider)
            thread = threading.Thread(target=self._run_api_thread, args=(provider, cur["mode"], copyToMacro))
        thread.daemon = True
        thread.start()

    def _run_codex_thread(self, mode, finalPrompt, model, copyToMacro, server):
        stdoutText = ""
        stderrText = ""
        outputText = ""
        errorText = ""
        tmp = None
        try:
            fd, outputPath = tempfile.mkstemp(prefix="glyphsgptcodex_", suffix=".txt")
            os.close(fd)
            tmp = outputPath
            cmd = self._build_command(mode, server, model, outputPath)
            env = os.environ.copy()
            env["PATH"] = ":".join([
                "/Applications/Codex.app/Contents/Resources",
                "/opt/homebrew/bin",
                "/usr/local/bin",
                env.get("PATH", ""),
            ])
            cwd = self._workspace_dir() if mode == "direct" else os.path.expanduser("~")
            self.codexProcess = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                text=True,
            )
            stdoutText, stderrText = self.codexProcess.communicate(finalPrompt)
            if os.path.exists(outputPath):
                with open(outputPath, "r", encoding="utf-8", errors="ignore") as f:
                    outputText = f.read()
            if self.codexProcess.returncode not in (0, None):
                if not outputText.strip() and stderrText.strip():
                    errorText = stderrText.strip()
        except Exception:
            errorText = traceback.format_exc()
        finally:
            self.codexProcess = None
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        callAfter(self._finish_run, "codex", mode, outputText, stdoutText, stderrText, errorText, copyToMacro)

    @objc.python_method
    def _finish_run(self, provider, mode, outputText, stdoutText, stderrText, errorText, copyToMacro):
        self.set_busy(False, "Ready")
        if errorText:
            self.send_error(errorText)
            return

        text = (outputText or "").strip()
        if not text and (stdoutText or stderrText):
            text = ((stdoutText or "") + ("\n" + stderrText if stderrText else "")).strip()
        if not text:
            self.send_error("Provider returned no message.")
            return

        if mode == "code":
            code = extract_code_block(text)
            item_id = self._record("assistant", code, "code")
            self.send("answerCode", {"code": code, "id": item_id})
            if copyToMacro and code.strip():
                self.copy_to_macro(code, announce=False)
        else:
            item_id = self._record("assistant", text, "text")
            self.send("answerText", {"text": text, "id": item_id})
            if copyToMacro:
                code = self._extract_first_code_block(text)
                if code:
                    self.copy_to_macro(code, announce=False)

    def stop_run(self):
        proc = self.codexProcess
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        self.codexProcess = None
        self.send_system("Stopped.")
        self.set_busy(False, "Stopped")

    # ---------- execution ----------
    def _build_exec_env(self):
        import GlyphsApp as GA
        env = {
            "__builtins__": __builtins__,
            "__name__": "__main__",
            "__file__": "<GlyphsGPT with Chat>",
            "Glyphs": GA.Glyphs,
            "objc": objc,
            "AppKit": AK,
            "Foundation": FN,
            "os": os,
            "re": re,
            "json": json,
            "traceback": traceback,
            "subprocess": subprocess,
            "tempfile": tempfile,
            "shutil": shutil,
            "threading": threading,
            "io": io,
            "contextlib": contextlib,
        }
        for module in (GA, AK, FN):
            for name in dir(module):
                if name.startswith("_"):
                    continue
                try:
                    env[name] = getattr(module, name)
                except Exception:
                    pass
        try:
            font = GA.Glyphs.font
            env["font"] = font
            env["currentFont"] = font
            env["selectedLayers"] = list(font.selectedLayers) if font is not None else []
            env["currentTab"] = font.currentTab if font is not None else None
            env["selectedFontMaster"] = font.selectedFontMaster if font is not None else None
        except Exception:
            env["font"] = None
            env["currentFont"] = None
            env["selectedLayers"] = []
            env["currentTab"] = None
            env["selectedFontMaster"] = None
        return env

    def _append_to_macro_log(self, text):
        text = str(text or "").rstrip()
        if not text:
            return
        try:
            Glyphs.showMacroWindow()
        except Exception:
            pass
        try:
            print(text)
        except Exception:
            pass

    def handle_exec(self, code):
        code = str(code or "").replace("\r\n", "\n").strip()
        if not code:
            self.send_error("Nothing to execute.", record=False)
            return
        buf = io.StringIO()
        try:
            env = self._build_exec_env()
            compiled = compile(code, "<GlyphsGPT with Chat>", "exec")
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                exec(compiled, env, env)
            out = buf.getvalue().strip()
            if out:
                item_id = self._record("system", "Execution output\n" + out, "text")
                self.send("execResult", {"output": out, "id": item_id})
                self._append_to_macro_log(out)
        except SystemExit:
            out = buf.getvalue().strip()
            if out:
                out += "\n"
            out += "SystemExit"
            item_id = self._record("system", "Execution output\n" + out, "text")
            self.send("execResult", {"output": out, "id": item_id})
            self._append_to_macro_log(out)
        except Exception:
            out = buf.getvalue().strip()
            tb = traceback.format_exc()
            merged = ((out + "\n") if out else "") + tb
            item_id = self._record("system", "Execution output\n" + merged, "text")
            self.send("execResult", {"output": merged, "id": item_id})
            self._append_to_macro_log(merged)

    def _walk_views(self, view):
        if view is None:
            return
        yield view
        try:
            subviews = list(view.subviews())
        except Exception:
            subviews = []
        for sub in subviews:
            for item in self._walk_views(sub):
                yield item
        try:
            if isinstance(view, AK.NSScrollView):
                doc = view.documentView()
                if doc is not None:
                    for item in self._walk_views(doc):
                        yield item
        except Exception:
            pass

    def _find_macro_text_view(self):
        controller = None
        try:
            controller = Glyphs.delegate().macroPanelController()
        except Exception:
            controller = None
        if controller is None:
            return None

        for name in ("textView", "codeTextView", "macroTextView", "editorTextView"):
            try:
                candidate = getattr(controller, name)()
            except Exception:
                candidate = None
            if candidate is not None and isinstance(candidate, AK.NSTextView):
                try:
                    if candidate.isEditable():
                        return candidate
                except Exception:
                    return candidate

        try:
            window = controller.window()
        except Exception:
            window = None
        if window is None:
            return None

        best = None
        bestScore = -1
        try:
            firstResponder = window.firstResponder()
        except Exception:
            firstResponder = None

        for view in self._walk_views(window.contentView()):
            if not isinstance(view, AK.NSTextView):
                continue
            try:
                if not view.isEditable():
                    continue
            except Exception:
                pass
            score = 0
            if firstResponder is view:
                score += 1000
            try:
                if hasattr(view, 'isFieldEditor') and view.isFieldEditor():
                    score -= 500
            except Exception:
                pass
            try:
                if hasattr(view, 'isRichText') and not view.isRichText():
                    score += 100
            except Exception:
                pass
            try:
                frame = view.frame()
                score += int(frame.size.width * frame.size.height)
            except Exception:
                pass
            if score > bestScore:
                best = view
                bestScore = score
        return best

    def _set_text_view_string(self, textView, code):
        try:
            if textView is None:
                return False
            try:
                textView.window().makeKeyAndOrderFront_(None)
            except Exception:
                pass
            try:
                textView.window().makeFirstResponder_(textView)
            except Exception:
                pass

            current = ''
            try:
                current = str(textView.string() or '')
            except Exception:
                current = ''
            fullRange = FN.NSMakeRange(0, len(current))

            replaced = False
            try:
                storage = textView.textStorage()
                if storage is not None:
                    storage.beginEditing()
                    storage.replaceCharactersInRange_withString_(fullRange, code)
                    storage.endEditing()
                    replaced = True
            except Exception:
                replaced = False

            if not replaced:
                try:
                    textView.setString_(code)
                    replaced = True
                except Exception:
                    replaced = False

            if not replaced:
                return False

            sel = FN.NSMakeRange(len(code), 0)
            try:
                textView.setSelectedRange_(sel)
            except Exception:
                pass
            try:
                textView.scrollRangeToVisible_(sel)
            except Exception:
                pass
            try:
                textView.didChangeText()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def copy_to_macro(self, code, announce=True):
        code = (code or "").replace("\r\n", "\n").strip()
        if not code:
            self.send_error("Nothing to copy.", record=False)
            return

        clipboardOK = False
        try:
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            try:
                clipboardOK = bool(pb.setString_forType_(code, NSPasteboardTypeString))
            except Exception:
                clipboardOK = False
            if not clipboardOK:
                try:
                    clipboardOK = bool(pb.writeObjects_([NSString.stringWithString_(code)]))
                except Exception:
                    clipboardOK = False
        except Exception:
            clipboardOK = False

        self.open_macro()

        def _finish_copy():
            inserted = False
            try:
                textView = self._find_macro_text_view()
                inserted = self._set_text_view_string(textView, code)
            except Exception:
                inserted = False
            if announce and not (clipboardOK or inserted):
                self.send_error("Could not send code to Macro Panel.", record=False)

        callAfter(_finish_copy)

    def open_macro(self):
        try:
            Glyphs.showMacroWindow()
        except Exception as e:
            self.send_error("Could not open Macro Window: %s" % e, record=False)


def _get_app_singleton():
    app = getattr(builtins, APP_SINGLETON_KEY, None)
    if app is None or not isinstance(app, GlyphsGPTwithChat) or getattr(app, '_script_build', None) != SCRIPT_BUILD:
        try:
            if app is not None and getattr(app, 'window', None) is not None:
                app.window.close()
        except Exception:
            pass
        app = GlyphsGPTwithChat()
        setattr(builtins, APP_SINGLETON_KEY, app)
    return app


__GlyphsGPTwithChat__ = _get_app_singleton()
__GlyphsGPTwithChat__.show()
