---
description: Export the current Claude Code session to a standalone HTML file (pi-style interactive viewer)
argument-hint: "[session-id|path/to.jsonl] [-o output.html] [--theme dark|light]"
allowed-tools: ["Bash(python3:*)"]
---

The exporter has been run. Its output:

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/export_session.py" $ARGUMENTS`

Report the path of the generated HTML file (the `output:` line above) to the user and tell them they can open it in any browser — it's fully self-contained (no internet needed). Do not take any other actions.
