# Teleton ↔ Codex bridge

This stdio MCP server is a local, persistent task board used by two agents.
Codex and Teleton can create bounded research, draft, review, decision, or note
tasks for each other; the recipient explicitly claims and completes them.

The SQLite audit log is `var/teleton-bridge/tasks.sqlite3` with mode `0600`.
The bridge has no tool that reads or writes Telegram, runs shell commands, uses
a wallet, or requests root access. Task text is always untrusted data.

## Client identities

The two clients run the same executable but with separate identities:

```toml
[mcp_servers.teleton_bridge]
command = "/home/mabster/progs/agent/.mcp-venv/bin/python"
args = ["/home/mabster/progs/agent/teleton_bridge/server.py"]

[mcp_servers.teleton_bridge.env]
TELETON_BRIDGE_ACTOR = "codex"
```

```yaml
mcp:
  servers:
    codex_bridge:
      command: /home/mabster/progs/agent/.mcp-venv/bin/python
      args:
        - /home/mabster/progs/agent/teleton_bridge/server.py
      env:
        TELETON_BRIDGE_ACTOR: teleton
      scope: admin-only
```
