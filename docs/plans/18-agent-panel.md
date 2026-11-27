# 18 – Agent panel (branch `agent-pane`, 2026-11-26)

A coding agent in a panel beside the notebook. It reads, changes and runs the cells of the
open notebook, on the live kernel. gpython has one panel and no code for any one agent.

## Shape

    gpython (Textual app)
      AgentPanel (agent.py)  --ACP, JSON-RPC on stdio-->  agent process (claude / codex / opencode / gemini / pi / yours)
      AgentLink (agentlink.py) <--TCP 127.0.0.1 + token--  `gpython mcp` (mcpserver.py)  <--MCP on stdio--  the agent
      NotebookTools (notebooktools.py): the pane's own methods

- **ACP** (Agent Client Protocol, version 1) is the conversation: `initialize`, `session/new`,
  `session/prompt`, `session/update` (text, thoughts, tool calls, plans),
  `session/request_permission`, `session/cancel`. `acp.py`, no textual.
- **MCP** is the tools. `session/new` takes a list of MCP servers; gpython passes
  `python -c "from gpython.mcpserver import main; main()"` with `GPYTHON_AGENT_LINK=port:token`.
  The server is a thin pass-through to the running app.
- **Registry** (`agents.py`): name → command. Built in: claude and codex (npx adapters
  `@agentclientprotocol/claude-agent-acp`, `@agentclientprotocol/codex-acp`), opencode
  (`opencode acp`), gemini (`gemini --acp`), pi (`npx pi-acp`). Config: `[agent] default =
  "codex"` (`:agent default codex`), `[agents] mine = "cmd"`.
- **Steps**: `[agent] tools` and `thoughts` = keep | fold | hide (`:agent tools MODE`). fold,
  the default: shown while the turn runs, removed when it ends, one line "N tool calls" in
  their place. A tool title is one line, cut at the panel's edge.

## Tools

`notebooks`, `cells_read`, `cell_edit` (whole source, or old/new), `cell_insert`, `cell_delete`,
`cells_run` (numbers, all, stale; waits, default 120 s), `variables`, `variable_value`,
`python_eval`, `kernel` (interrupt, restart), `notebook_save`. Cells are named by header
number. Answers are text; plots come back as PNG blocks (4 per answer). Output is clipped to
6000 characters per cell, start and end kept.

## Decisions

- Edits apply at once. A cell on screen changes through its editor (one undo step on the
  notebook's timeline); a cell off screen takes the text as an ex command does (no text undo).
- The agent shares the user's kernel. That is the point: it sees the real values.
- The panel belongs to the app (right of the workspace), not to one window. Tools act on the
  active notebook, or on `notebook: path`.
- Each prompt carries one line of context (file, cell count, cursor cell); the first one also
  says to use the tools and not to edit the file on disk.
- gpython advertises no ACP file or terminal capability: the agent uses its own.
- Nothing is imported or started before the first `:agent` (`tests/test_agent_panel.py`
  checks). Streamed text is painted every 80 ms, one Static per transcript entry.
- The link listens on 127.0.0.1 only and needs a 128-bit token. The link file
  (`~/.gpython/links/<pid>.json`, mode 600) lets `gpython mcp` from another terminal find it.

## Commands

`:agent` show / hide · `:agent NAME` · `:agent new` · `:agent stop` · `:agent hide` ·
`:agent default NAME` · `:agent tools|thoughts keep|fold|hide` ·
`:agent link` (tools for an agent in another terminal: `claude mcp add gpython -- gpython mcp`).
Keys in the panel: enter, ctrl+c, 1-9 for a permission, pgup/pgdn, escape, ctrl+w h.

## Tested

- `tests/fake_agent.py` speaks ACP; `test_acp.py`, `test_agent_panel.py`, `test_notebooktools.py`
  (the last one drives the real MCP server process against a running app).
- By hand on 2026-11-26: `opencode acp` opens a session from the panel. Its turn ended with no
  text on the dev machine (no model set up for opencode); the panel now says so.
- 2026-11-27: the user ran a real agent from the panel (tool calls on the notebook worked).
- Not yet run from the panel with a real model by us: codex, gemini, pi.

## Not done

- A file the agent edits on disk does not reload in the open window (autoread is on branch
  `agent-experiments`).
- The debugger for the agent (stop-if, post-mortem as data) is on `agent-experiments`
  (`gpython debug`); not a tool here yet.
- Model and mode pickers (ACP `session/set_mode`, config options), slash commands the agent
  lists, `session/load` (resume), images in a prompt, multi-line input.
- A key for the panel (only `:agent` and ctrl+w l today).
- The agent's own file diffs are shown in the transcript only, a few lines.

## Unresolved questions

- Diff to accept per cell before an edit lands, or keep "apply at once + undo"?
- Own kernel for the agent as an option?
- Merge the debugger tools and autoread from `agent-experiments`?
- Which key opens the panel? (`space a`?)
- Should `:agent link` be on at start (a config key), so an outside agent always finds gpython?
