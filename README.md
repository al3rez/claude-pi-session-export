# claude-pi-session-export

**Brings pi's (`earendil-works/pi`) session export to Claude Code.** It's a Claude
Code plugin that exports a Claude Code session to a **single self-contained HTML
file** — pixel-for-pixel and feature-for-feature identical to
[`earendil-works/pi`](https://github.com/earendil-works/pi)'s `/export`.

It bundles pi's exact export front-end (`template.html`, `template.css`,
`template.js`, plus the vendored `marked` and `highlight.js`) and converts Claude
Code's session JSONL into the `SessionData` shape pi's renderer consumes. Same
sidebar tree with branch navigation and filters (Default / No-tools / User /
Labeled / All), same message/thinking/tool rendering, collapsible tool output,
diff rendering for edits, syntax highlighting, the stats header (models, tokens,
cost), toggle-thinking / toggle-tools, copy-link, and the “↓ JSONL” download — all
working offline.

## Install

```
/plugin marketplace add ~/claude-pi-session-export
/plugin install claude-pi-session-export@claude-pi-session-export
```

(Restart or `/plugin` to confirm it's enabled.)

## Use

Inside any Claude Code session:

```
/export-session
```

Exports the **current project's most recent session** to
`claude-session-<id>.html` in the working directory.

Options:

```
/export-session <session-id>             # export a specific session by id
/export-session /path/to/session.jsonl   # export an explicit file
/export-session -o ~/Desktop/run.html    # custom output path
/export-session --theme light            # light theme (default: dark)
```

You can also run it directly, outside the slash command:

```
python3 ~/claude-pi-session-export/scripts/export_session.py [session] [-o out.html] [--theme dark|light]
```

## How it maps Claude Code → pi

| Claude Code | pi `SessionData` |
|---|---|
| `~/.claude/projects/<enc-cwd>/<id>.jsonl` | discovered automatically (newest in the current project) |
| `uuid` / `parentUuid` | entry `id` / `parentId` (tree + branches preserved, re-linked across skipped lines) |
| `assistant` `text` / `thinking` / `tool_use` | `assistant` `text` / `thinking` / `toolCall` |
| `user` `tool_result` blocks | `toolResult` entries (split out, matched by `tool_use_id`) |
| `Bash`/`Read`/`Write`/`Edit`/`LS` | mapped to pi's native `bash`/`read`/`write`/`edit`/`ls` renderers |
| other tools (`Grep`, `Glob`, `Task`, …) | pi's generic tool renderer (name + JSON args + output) |
| `Edit` `structuredPatch` | synthesized into `details.diff` for pi's diff view |
| `usage` tokens | mapped + priced (Opus $5/$25, Sonnet $3/$15, Haiku $1/$5 per 1M; cache-write 1.25×, cache-read 0.1×) for the cost header |
| `sessionId` / `cwd` / first timestamp | session header |
| `aiTitle` | page `<title>` (pi hardcodes "Session Export"; falls back to it when absent) |

Notes:
- Usage is de-duplicated across an assistant message's streamed chunks (same
  `message.id`) so token/cost totals aren't inflated.
- Claude Code doesn't store dollar cost in the transcript, so it's computed from
  token usage using the table above. Edit `PRICING` in `scripts/export_session.py`
  if rates change.
- The export is read-only and fully offline; nothing is uploaded anywhere.

## Layout

```
.claude-plugin/plugin.json          plugin manifest
.claude-plugin/marketplace.json     local marketplace listing
commands/export-session.md          the /export-session slash command
scripts/export_session.py           JSONL → pi SessionData → HTML (stdlib only)
assets/export-html/                  pi's exact template + vendored libs
assets/themes/{dark,light}.json      pi's exact themes
```

## Attribution & license

The files under `assets/export-html/` and `assets/themes/` are vendored verbatim
from [`earendil-works/pi`](https://github.com/earendil-works/pi) (MIT) so the
output matches pi's `/export` exactly. They include `marked` (MIT) and
`highlight.js` (BSD-3-Clause). All credit for the export front-end and its design
goes to the pi authors.

The plugin's own code (the converter, command, and manifests) is released under
the MIT License — see [LICENSE](LICENSE).
