#!/usr/bin/env python3
"""
Export a Claude Code session to a self-contained HTML file, byte-for-byte
identical in look and behavior to pi's (`earendil-works/pi`) `/export`.

It does this by reusing pi's exact export template (template.html / .css / .js
and the vendored marked + highlight.js), and converting Claude Code's session
JSONL into the `SessionData` shape that pi's template.js consumes
({ header, entries, leafId }). The conversion mirrors
packages/coding-agent/src/core/export-html/index.ts.

No third-party dependencies — Python 3 standard library only.
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
EXPORT_DIR = PLUGIN_ROOT / "assets" / "export-html"
THEMES_DIR = PLUGIN_ROOT / "assets" / "themes"

APP_NAME = "claude"  # used for the default output filename, like pi's APP_NAME

# Tools that pi's template.js renders natively (nice headers/diffs/output).
# Map Claude Code's PascalCase tool names onto pi's lowercase names so they get
# the same rich rendering. Everything else falls through to pi's generic
# "tool name + JSON args + output" renderer — exactly as pi treats unknown tools.
KNOWN_TOOL_NAMES = {
    "Bash": "bash",
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "LS": "ls",
}

# Per-token pricing (USD). Cache-write = 1.25x input, cache-read = 0.1x input
# (standard Anthropic ephemeral-cache pricing). Keyed by substring of the model id.
PRICING = {
    "opus":   {"input": 5e-6, "output": 25e-6, "cacheWrite": 6.25e-6, "cacheRead": 0.5e-6},
    "sonnet": {"input": 3e-6, "output": 15e-6, "cacheWrite": 3.75e-6, "cacheRead": 0.3e-6},
    "haiku":  {"input": 1e-6, "output": 5e-6,  "cacheWrite": 1.25e-6, "cacheRead": 0.1e-6},
}


# --------------------------------------------------------------------------- #
# Theme resolution (mirrors theme.ts: resolveVarRefs + getResolvedThemeColors  #
# + getThemeExportColors + deriveExportColors).                                #
# --------------------------------------------------------------------------- #

def resolve_var(value, variables, seen=None):
    if seen is None:
        seen = set()
    if isinstance(value, (int, float)):
        return value
    if value == "" or (isinstance(value, str) and value.startswith("#")):
        return value
    if value in seen:
        raise ValueError(f"Circular variable reference: {value}")
    if value not in variables:
        raise ValueError(f"Variable reference not found: {value}")
    seen.add(value)
    return resolve_var(variables[value], variables, seen)


def load_theme(name):
    with open(THEMES_DIR / f"{name}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def resolved_theme_colors(name):
    theme = load_theme(name)
    variables = theme.get("vars", {})
    default_text = "#000000" if name == "light" else "#e5e5e7"
    out = {}
    for key, val in theme.get("colors", {}).items():
        resolved = resolve_var(val, variables)
        out[key] = default_text if resolved == "" else resolved
    return out, theme


def parse_color(color):
    m = re.match(r"^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$", color or "")
    if m:
        return tuple(int(m.group(i), 16) for i in (1, 2, 3))
    m = re.match(r"^rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", color or "")
    if m:
        return tuple(int(m.group(i)) for i in (1, 2, 3))
    return None


def adjust_brightness(color, factor):
    rgb = parse_color(color)
    if not rgb:
        return color
    a = lambda c: min(255, max(0, round(c * factor)))
    return f"rgb({a(rgb[0])}, {a(rgb[1])}, {a(rgb[2])})"


def luminance(r, g, b):
    def lin(c):
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def derive_export_colors(base):
    rgb = parse_color(base)
    if not rgb:
        return {"pageBg": "rgb(24, 24, 30)", "cardBg": "rgb(30, 30, 36)", "infoBg": "rgb(60, 55, 40)"}
    r, g, b = rgb
    if luminance(r, g, b) > 0.5:
        return {
            "pageBg": adjust_brightness(base, 0.96),
            "cardBg": base,
            "infoBg": f"rgb({min(255, r + 10)}, {min(255, g + 5)}, {max(0, b - 20)})",
        }
    return {
        "pageBg": adjust_brightness(base, 0.7),
        "cardBg": adjust_brightness(base, 0.85),
        "infoBg": f"rgb({min(255, r + 20)}, {min(255, g + 15)}, {b})",
    }


def theme_export_colors(theme):
    export = theme.get("export") or {}
    variables = theme.get("vars", {})

    def res(v):
        if v is None:
            return None
        r = resolve_var(v, variables)
        return None if r == "" else r

    return {"pageBg": res(export.get("pageBg")), "cardBg": res(export.get("cardBg")), "infoBg": res(export.get("infoBg"))}


def build_theme_vars(name):
    colors, theme = resolved_theme_colors(name)
    lines = [f"--{k}: {v};" for k, v in colors.items()]
    export = theme_export_colors(theme)
    derived = derive_export_colors(colors.get("userMessageBg", "#343541"))
    lines.append(f"--exportPageBg: {export['pageBg'] or derived['pageBg']};")
    lines.append(f"--exportCardBg: {export['cardBg'] or derived['cardBg']};")
    lines.append(f"--exportInfoBg: {export['infoBg'] or derived['infoBg']};")
    return "\n      ".join(lines), colors, export, derived


# --------------------------------------------------------------------------- #
# HTML generation (mirrors index.ts generateHtml).                             #
# --------------------------------------------------------------------------- #

def generate_html(session_data, theme_name="dark"):
    template = (EXPORT_DIR / "template.html").read_text(encoding="utf-8")
    template_css = (EXPORT_DIR / "template.css").read_text(encoding="utf-8")
    template_js = (EXPORT_DIR / "template.js").read_text(encoding="utf-8")
    marked_js = (EXPORT_DIR / "vendor" / "marked.min.js").read_text(encoding="utf-8")
    hljs_js = (EXPORT_DIR / "vendor" / "highlight.min.js").read_text(encoding="utf-8")

    theme_vars, _colors, export, derived = build_theme_vars(theme_name)
    body_bg = export["pageBg"] or derived["pageBg"]
    container_bg = export["cardBg"] or derived["cardBg"]
    info_bg = export["infoBg"] or derived["infoBg"]

    session_b64 = base64.b64encode(
        json.dumps(session_data, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")

    css = (
        template_css
        .replace("{{THEME_VARS}}", theme_vars)
        .replace("{{BODY_BG}}", body_bg)
        .replace("{{CONTAINER_BG}}", container_bg)
        .replace("{{INFO_BG}}", info_bg)
    )

    return (
        template
        .replace("{{CSS}}", css)
        .replace("{{JS}}", template_js)
        .replace("{{SESSION_DATA}}", session_b64)
        .replace("{{MARKED_JS}}", marked_js)
        .replace("{{HIGHLIGHT_JS}}", hljs_js)
    )


# --------------------------------------------------------------------------- #
# Claude Code JSONL -> pi SessionData conversion.                              #
# --------------------------------------------------------------------------- #

def iso_to_ms(ts):
    if not ts:
        return 0
    try:
        s = ts.replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except Exception:
        return 0


def map_stop_reason(sr):
    return {
        "end_turn": "stop",
        "max_tokens": "length",
        "tool_use": "toolUse",
        "stop_sequence": "stop",
        "refusal": "error",
        "pause_turn": "stop",
    }.get(sr, sr or "stop")


def price_for(model):
    m = (model or "").lower()
    if "opus" in m:
        return PRICING["opus"]
    if "haiku" in m:
        return PRICING["haiku"]
    return PRICING["sonnet"]  # default to sonnet for unknown/sonnet


def zero_usage():
    return {
        "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
    }


def map_usage(u, model):
    if not isinstance(u, dict):
        return zero_usage()
    inp = u.get("input_tokens", 0) or 0
    out = u.get("output_tokens", 0) or 0
    cr = u.get("cache_read_input_tokens", 0) or 0
    cw = u.get("cache_creation_input_tokens", 0) or 0
    p = price_for(model)
    cost = {
        "input": inp * p["input"],
        "output": out * p["output"],
        "cacheRead": cr * p["cacheRead"],
        "cacheWrite": cw * p["cacheWrite"],
    }
    cost["total"] = sum(cost.values())
    return {"input": inp, "output": out, "cacheRead": cr, "cacheWrite": cw,
            "totalTokens": inp + out + cr + cw, "cost": cost}


def map_tool_name(name):
    return KNOWN_TOOL_NAMES.get(name, name)


def normalize_tool_content(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        return [{"type": "text", "text": raw}]
    out = []
    for c in raw:
        if not isinstance(c, dict):
            out.append({"type": "text", "text": str(c)})
            continue
        ct = c.get("type")
        if ct == "text":
            out.append({"type": "text", "text": c.get("text", "")})
        elif ct == "image":
            src = c.get("source", {}) or {}
            out.append({"type": "image", "mimeType": src.get("media_type", "image/png"),
                        "data": src.get("data", "")})
        else:
            out.append({"type": "text", "text": json.dumps(c, ensure_ascii=False)})
    return out


def build_diff(structured_patch):
    parts = []
    for hunk in structured_patch or []:
        for line in hunk.get("lines", []) or []:
            parts.append(line)
    return "\n".join(parts)


def convert(lines, session_id, cwd):
    # Map every uuid -> parentUuid (across ALL line types) so we can re-link
    # across non-message entries that we skip (attachments, snapshots, etc.).
    parent_of = {}
    first_ts = None
    for ln in lines:
        if isinstance(ln, dict) and ln.get("uuid"):
            parent_of[ln["uuid"]] = ln.get("parentUuid")
        if first_ts is None and isinstance(ln, dict) and ln.get("timestamp"):
            first_ts = ln["timestamp"]

    rep = {}           # claude uuid -> pi entry id that its children attach to
    emitted = set()
    entries = []
    tool_name_by_id = {}   # tool_use id -> mapped pi tool name
    seen_msg_ids = set()   # dedupe usage across split assistant chunks

    def resolve_parent(p_uuid):
        u = p_uuid
        while u is not None:
            if u in rep:
                return rep[u]
            u = parent_of.get(u)
        return None

    def build_messages(ln):
        """Return list of pi message payload dicts for one Claude line, in order."""
        msg = ln.get("message") or {}
        role = msg.get("role")
        ts_ms = iso_to_ms(ln.get("timestamp"))

        if role == "assistant":
            content = []
            for b in msg.get("content", []) or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    content.append({"type": "text", "text": b.get("text", "")})
                elif bt == "thinking":
                    content.append({"type": "thinking", "thinking": b.get("thinking", "")})
                elif bt == "tool_use":
                    nm = map_tool_name(b.get("name", ""))
                    if b.get("id"):
                        tool_name_by_id[b["id"]] = nm
                    content.append({"type": "toolCall", "id": b.get("id", ""),
                                    "name": nm, "arguments": b.get("input", {}) or {}})
            if not content:
                return []
            model = msg.get("model")
            mid = msg.get("id")
            if mid and mid in seen_msg_ids:
                usage = zero_usage()  # same usage repeated across split chunks; count once
            else:
                usage = map_usage(msg.get("usage", {}), model)
                if mid:
                    seen_msg_ids.add(mid)
            payload = {
                "role": "assistant", "content": content,
                "model": model, "provider": "anthropic",
                "stopReason": map_stop_reason(msg.get("stop_reason")),
                "usage": usage, "timestamp": ts_ms,
            }
            return [payload]

        if role == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return [{"role": "user", "content": content, "timestamp": ts_ms}]
            results = []
            text_img = []
            tur = ln.get("toolUseResult")
            for b in content or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    text_img.append({"type": "text", "text": b.get("text", "")})
                elif bt == "image":
                    src = b.get("source", {}) or {}
                    text_img.append({"type": "image", "mimeType": src.get("media_type", "image/png"),
                                     "data": src.get("data", "")})
                elif bt == "tool_result":
                    tuid = b.get("tool_use_id", "")
                    nm = tool_name_by_id.get(tuid, "")
                    tr = {
                        "role": "toolResult", "toolCallId": tuid, "toolName": nm,
                        "content": normalize_tool_content(b.get("content")),
                        "isError": bool(b.get("is_error")), "timestamp": ts_ms,
                    }
                    if nm == "edit" and isinstance(tur, dict) and tur.get("structuredPatch"):
                        diff = build_diff(tur["structuredPatch"])
                        if diff:
                            tr["details"] = {"diff": diff}
                    results.append(tr)
            if text_img:
                results.append({"role": "user", "content": text_img, "timestamp": ts_ms})
            return results

        return []

    for ln in lines:
        if not isinstance(ln, dict):
            continue
        if ln.get("type") not in ("user", "assistant"):
            continue
        uuid = ln.get("uuid")
        if not uuid:
            continue
        parent_id = resolve_parent(ln.get("parentUuid"))
        payloads = build_messages(ln)
        ts_iso = ln.get("timestamp")
        if not payloads:
            rep[uuid] = parent_id  # passthrough so descendants stay connected
            continue
        prev = parent_id
        last = None
        for i, payload in enumerate(payloads):
            eid = uuid if i == 0 else f"{uuid}__{i}"
            entries.append({"type": "message", "id": eid, "parentId": prev,
                            "timestamp": ts_iso, "message": payload})
            emitted.add(eid)
            prev = eid
            last = eid
        rep[uuid] = last

    leaf_id = entries[-1]["id"] if entries else None
    header = {"type": "session", "version": 3, "id": session_id,
              "timestamp": first_ts or "", "cwd": cwd}
    return {"header": header, "entries": entries, "leafId": leaf_id}


# --------------------------------------------------------------------------- #
# Session file discovery.                                                      #
# --------------------------------------------------------------------------- #

def encode_cwd(cwd):
    # Claude Code encodes a project path by replacing non-alphanumerics with '-'.
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def projects_dir():
    return Path.home() / ".claude" / "projects"


def newest_jsonl(directory):
    files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def find_session_file(arg, cwd):
    # 1. Explicit path to a .jsonl file.
    if arg:
        p = Path(arg).expanduser()
        if p.suffix == ".jsonl" and p.exists():
            return p
        # 2. A session id (uuid) — search across all project dirs.
        sid = arg[:-6] if arg.endswith(".jsonl") else arg
        for proj in projects_dir().glob("*"):
            cand = proj / f"{sid}.jsonl"
            if cand.exists():
                return cand
        raise SystemExit(f"No session file found for '{arg}'.")
    # 3. Default: newest session in the current project directory.
    proj = projects_dir() / encode_cwd(cwd)
    if proj.is_dir():
        f = newest_jsonl(proj)
        if f:
            return f
    # 4. Fallback: newest session anywhere.
    candidates = []
    if projects_dir().is_dir():
        for proj in projects_dir().glob("*"):
            candidates.extend(proj.glob("*.jsonl"))
    if not candidates:
        raise SystemExit("No Claude Code sessions found under ~/.claude/projects.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def read_jsonl(path):
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return lines


def session_meta(lines, fallback_path):
    session_id = None
    cwd = None
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        if session_id is None and ln.get("sessionId"):
            session_id = ln["sessionId"]
        if cwd is None and ln.get("cwd"):
            cwd = ln["cwd"]
        if session_id and cwd:
            break
    if not session_id:
        session_id = Path(fallback_path).stem
    if not cwd:
        cwd = os.getcwd()
    return session_id, cwd


def main():
    ap = argparse.ArgumentParser(
        description="Export a Claude Code session to a pi-style standalone HTML file.")
    ap.add_argument("session", nargs="?",
                    help="Session id or path to a .jsonl file. Defaults to the current project's latest session.")
    ap.add_argument("-o", "--output", help="Output HTML path.")
    ap.add_argument("--theme", default="dark", choices=["dark", "light"], help="Theme (default: dark).")
    ap.add_argument("--cwd", default=os.getcwd(), help="Project working directory used to locate the session.")
    args = ap.parse_args()

    src = find_session_file(args.session, args.cwd)
    lines = read_jsonl(src)
    if not lines:
        raise SystemExit(f"Session file is empty: {src}")

    session_id, cwd = session_meta(lines, src)
    session_data = convert(lines, session_id, cwd)

    if not session_data["entries"]:
        raise SystemExit("Nothing to export - the session has no messages yet.")

    html = generate_html(session_data, args.theme)

    if args.output:
        out = Path(args.output).expanduser()
    else:
        out = Path.cwd() / f"{APP_NAME}-session-{src.stem}.html"
    out.write_text(html, encoding="utf-8")

    n = len(session_data["entries"])
    print(f"Exported session {session_id} ({n} entries)")
    print(f"  source: {src}")
    print(f"  output: {out.resolve()}")


if __name__ == "__main__":
    main()
