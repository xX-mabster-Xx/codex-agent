## Dedicated Teleton account setup

Teleton is installed from source in `third_party/teleton-agent`. Its data must
stay separate from the existing Telegram MCP sessions:

- Teleton home: `/home/mabster/.teleton-agent`
- Telegram identity: a new QR/phone login for the `agent` account
- bridge: local stdio MCP only; no HTTP port

The first login is intentionally manual. It proves control of the account and
can require a Telegram 2FA password, which must never be placed in a project
file or sent to an agent.

1. Start the setup wizard with a new session, not a copied session string:

   `zsh /home/mabster/progs/agent/scripts/setup-teleton-agent.sh`

2. In the wizard choose **user** mode, sign in as `agent` by QR/phone, choose
   the `codex` provider, and set your own Telegram numeric ID as the only admin.
3. Before starting, ensure `telegram.proxy` is SOCKS5 at `127.0.0.1:2060`,
   `embedding.provider` is `none`, `group_policy` is `disabled`,
   `capabilities.exec.mode` is `off`, `heartbeat.enabled` is `false`, and the
   `codex_bridge` block from `teleton-agent.config.example.yaml` is present in
   `/home/mabster/.teleton-agent/config.yaml`.
4. Run it locally:

   `zsh /home/mabster/progs/agent/scripts/run-teleton-agent.sh`

Do not enable a wallet, TON Proxy, automatic posts, or scheduled heartbeats
until you decide their exact policy. The bridge itself can only create, claim,
complete, and cancel bounded task records.
