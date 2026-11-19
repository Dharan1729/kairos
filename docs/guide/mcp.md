# MCP: Claude Code, Cursor and other clients

`kairos-mcp` is a stdio MCP server. It is a thin client of a running Kairos server: it forwards tool calls to the HTTP API with your credential.

| Tool | What it does |
|---|---|
| `get_context(task, repo?)` | Assembled context for a task: ranked, within `KAIROS_BUDGET_TOKENS` (default 3000). Call it at the start of a task. |
| `search(query, repo?)` | Ad-hoc search when the first bundle did not cover something. |
| `remember(content, layer, scope)` | Proposes a fact. It is **not** used until a human approves it (`kairos candidates approve <id> --by <name>`). |

The server sends no user id. Kairos takes the user from the key, so an agent can only ever get its own user's context. **Give agents a person-bound member key**, not the admin key:

```bash
kairos keys create --role member --person <your person id> --label "my laptop agent" --key <admin key>
```

For the local demo, `kairos seed` already wrote one (`demo_user` in `.kairos/dev_keys.json`), and `kairos-mcp` uses it when `KAIROS_API_KEY` is unset.

## Environment

| Variable | Default | |
|---|---|---|
| `KAIROS_URL` | `http://127.0.0.1:8000` | the Kairos server |
| `KAIROS_API_KEY` | `demo_user` from `$KAIROS_HOME/dev_keys.json` | person-bound key |
| `KAIROS_DEFAULT_REPO` | none | repo name sent when a call gives none, e.g. `acme/api`. It must be a project of your team to add project context. |
| `KAIROS_BUDGET_TOKENS` | 3000 | budget for `get_context` |

## Claude Code

```bash
claude mcp add kairos \
  -e KAIROS_URL=http://127.0.0.1:8000 \
  -e KAIROS_API_KEY=kairos_... \
  -e KAIROS_DEFAULT_REPO=nimbus/orchestrator-core \
  -- kairos-mcp
```

Use the full path to `kairos-mcp` (for example `.venv/bin/kairos-mcp`) if it is not on the `PATH` Claude Code sees. To share the setup with a project, put the same thing in `.mcp.json` at the repo root and keep the key out of it (reference an environment variable your shell sets):

```json
{
  "mcpServers": {
    "kairos": {
      "command": "kairos-mcp",
      "env": {"KAIROS_URL": "http://127.0.0.1:8000", "KAIROS_API_KEY": "${KAIROS_API_KEY}"}
    }
  }
}
```

Then `/mcp` in Claude Code should list `kairos` with three tools.

## Cursor

Add to `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "kairos": {
      "command": "/absolute/path/to/.venv/bin/kairos-mcp",
      "env": {"KAIROS_URL": "http://127.0.0.1:8000", "KAIROS_API_KEY": "kairos_..."}
    }
  }
}
```

## Other clients

Any MCP client that can launch a stdio server works: the command is `kairos-mcp`, configuration is the environment above.

## Troubleshooting

- `couldn't reach Kairos at ...`: the server is not running (`kairos serve`) or `KAIROS_URL` is wrong.
- `Kairos returned 401`: the key is wrong or revoked.
- `Kairos returned 403` from `remember`: you proposed into a scope you cannot write (another user's personal scope, another team, or org without admin).
- Only `[org]` items come back: the key is not bound to a person. Create a bound key.
