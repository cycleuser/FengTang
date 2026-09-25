# FengTang(冯唐)

> English | [中文](README_CN.md)

**About the name:** FengTang (冯唐) is taken from a tale in the *Records of the
Grand Historian* (《史记·张释之冯唐列传》). Under Emperor Wen of Han, the governor
of Yunzhong, Wei Shang, was stripped of rank over a minor miscount in reporting
enemy heads. Feng Tang spoke up — *“your rewards are too light and your
punishments too heavy”* — and the emperor, moved, that very day sent Feng Tang
**bearing the imperial tally (持节)** to Yunzhong to **deliver the edict of pardon
(赦书)**, restoring Wei Shang to office. Su Shi's celebrated line — *“bearing the
tally to Yunzhong; when will Feng Tang be sent?”* (持节云中,何日遣冯唐) — alludes to
exactly this. Hence the name: may every message be carried as faithfully as that
pardon, and arrive exactly as it must.

**Pure-Python command-line mail client — send, fetch, read, search, mark — with a built-in mail server and a full agent (function-calling) API.**

GPL-3.0-or-later licensed. Zero runtime dependencies: everything (SMTP, IMAP, POP3 clients, an asyncio SMTP+POP3 server, SQLite storage, MIME parsing, SASL auth incl. DES/MD4 for NTLM) is built on the Python standard library.

## Features

- **All mainstream protocols**: SMTP (send), IMAP4 (fetch/search/flags/folders), POP3 (fetch) — SSL and STARTTLS everywhere.
- **All mainstream auth methods**: SASL `PLAIN`, `LOGIN`, `CRAM-MD5`, `XOAUTH2`/`OAUTHBEARER`, `NTLM` (with a pure-Python DES + MD4), and POP3 `APOP`. `auto` mode tries what the server advertises.
- **Provider presets**: gmail, outlook, qq, 163, 126, yahoo, icloud, zoho, aliyun, sina — one flag fills all host/port settings.
- **Built-in mail server / MTA**: a self-contained SMTP + POP3 server (asyncio, zero system dependencies) that **sends** (direct-to-MX with its own pure-Python DNS resolver, or smarthost relay) and **receives** (local delivery plus external-mailbox pull) real mail — no external tools.
- **Local SQLite store**: every fetched/sent message is searchable, flaggable, movable, deletable — offline.
- **MIME done right**: multipart/alternative, RFC 2047 CJK headers, attachments (list + save), HTML→text fallback.
- **Agent API**: an OpenAI function-calling `TOOLS` schema + `dispatch()` for every operation, so an LLM agent can read/write/search/mark mail exactly like a human user.

## Installation

**Requires Python ≥ 3.10. Zero runtime dependencies — pure standard library.**
There is **no venv requirement and no system dependency**: no OpenSSL bindings,
no external mail binaries, no Node/Go/Rust toolchain.

Use whatever Python environment you already have; pick one:

```bash
# 1. PyPI, into the current environment
pip install fengtang

# 2. Isolated CLI install, no environment juggling (recommended for CLI use)
pipx install fengtang

# 3. conda — create your own env; the name is entirely up to you
conda create -n mymail python=3.12
conda activate mymail
pip install fengtang

# 4. From source (development; [dev] only adds pytest / ruff / mypy)
git clone https://github.com/cycleuser/FengTang && cd FengTang
pip install -e ".[dev]"
```

## Data & file locations

All runtime data lives in **one directory**: `~/.fengtang/` by default.

| File | Purpose | Permissions |
|---|---|---|
| `~/.fengtang/config.json` | Account credentials (email, auth code/token, server hosts, ports) | `0600` (owner-only, enforced on save) |
| `~/.fengtang/fengtang.db` | SQLite store: every fetched/sent message — raw RFC822 source, parsed headers/body, flags, attachments | `0600` (tightened after first run) |

Location resolution order (highest first):

1. `data_dir` field inside `config.json`
2. Environment variable `FENGTANG_DATA_DIR`
3. Default `~/.fengtang/`

The mail database stores the **full raw message** plus parsed fields
(subject, from/to, date, flags, text/html bodies), so searches work offline
and attachments can be re-extracted any time. No message content is kept
anywhere else — no code, no tests, no README ever contain real credentials
or message data.

To move everything (config + mail) elsewhere:

```bash
export FENGTANG_DATA_DIR=/Volumes/SecureUSB/fengtang
```

Or set `"data_dir": "/path"` in the config file.

### Credentials & security (important)

**Passwords live only in the local config — never in code, docs, tests, or git.**

Credentials are stored exclusively in `~/.fengtang/config.json` (mode `0600`). Three
out-of-band channels are supported, in priority order:

1. **Environment variable** (highest priority, nothing written to disk)
   ```bash
   export FENGTANG_PASSWORD_QQ=<your-auth-code>   # name = FENGTANG_PASSWORD_ + ACCOUNT (upper)
   fengtang fetch -a qq
   ```
2. **Password file** (secret kept in its own file, e.g. an encrypted volume)
   ```bash
   fengtang config add qq you@qq.com --password-file ~/.secrets/qq.txt
   ```
3. **Secure set** (no echo, no shell history, no chat transcript)
   ```bash
   fengtang config set-password qq            # interactive, confirmed twice
   echo "$PW" | fengtang config set-password qq --stdin   # pipe from a password manager
   fengtang config set-password qq --password-file ~/.secrets/qq.txt
   ```

`fengtang config add` without `--password` prompts with no echo. Prefer these
interactive/file/env forms so a secret never lands in a plaintext command line
or a pasted conversation.

### Configure an account (provider preset)

### Configure an account (provider preset)

```bash
fengtang config add qq you@qq.com --password <SMTP-auth-code> --auth login --provider qq
fengtang config test -a qq        # probes IMAP + POP3 + SMTP auth
```

### Interactive OAuth login (Gmail / Outlook)

XOAUTH2 accounts (Gmail, Outlook) need no manual token hunting — one command
runs the browser consent flow:

```bash
# Recommended: guided setup (opens the console, then continues into login)
fengtang setup-gmail you@gmail.com

# Or, with an existing client_id:
fengtang config login you@gmail.com --client-id <your-client-id>
fengtang config login -a <existing-account>   # re-authorize an account
```

Google no longer permits shared public client_ids for the Gmail scope, so you
create your own (Desktop app type — no verification review needed for your
own account).

The provider's consent page opens in your default browser; a temporary local
loopback port catches the redirect, then access/refresh tokens are persisted
to `~/.fengtang/config.json` (0600). An expired access token is refreshed
automatically with the stored refresh token on next use.

### Fetch, list, read, search, mark

```bash
fengtang fetch -a qq -n 20   # same as before        # pull new mail into local store
fengtang list --unread            # newest first
fengtang search "Foxmail"
fengtang read 42                  # headers + body
fengtang read 42 --save-attachments ./att
fengtang mark 42 --flags seen,flagged
fengtang folders
```

### Send

```bash
fengtang send -t bob@example.com -s "Hello" -m "Body text" \
    --attach ./report.pdf:report-2026.pdf
```

### Run the built-in mail server (a self-contained MTA)

The built-in server both **receives** and **sends** real mail with no external
tools — its own DNS resolver, its own SMTP client, its own SMTP/POP3 servers.

```bash
# Local-only server: receives into ~/.fengtang/fengtang.db
fengtang serve --smtp-port 2525 --pop-port 1110 --domain localhost

# Auth-protected local mailboxes (repeat --user)
fengtang serve --user alice@localhost:secret1 --user bob@localhost:secret2

# Full send+receive: outbound via a configured account (smarthost) and
# inbound aggregation from real external mailboxes
fengtang serve --domain fengtang.local \
  --user "me@fengtang.local:localpw" \
  --relay-account qq --outbound auto \
  --pull foxmail --pull-interval 60
```

**Outbound (send):** for any non-local recipient, the server delivers the
message itself:
- `--outbound direct` — resolve the recipient's MX with the built-in pure-Python
  DNS client and speak SMTP on port 25 (no auth, no relay);
- `--outbound smarthost` — hand it to an authenticated upstream account named by
  `--relay-account` (like Postfix's `relayhost`); the `From` is rewritten to that
  account and the original is kept in `Reply-To` / `X-Original-From`;
- `--outbound auto` (default) — try direct-to-MX, fall back to the smarthost.

**Inbound (receive):** the SMTP listener accepts mail for local domains and
stores it; `--pull ACCOUNT[:FOLDER]` additionally polls real external mailboxes
(IMAP or POP3) and aggregates them into the local store — so a server without a
public IP or its own MX can still receive. `--pull-interval` controls the poll
period (0 runs one pull at startup).

Relaying to non-local domains always requires authentication — the server is
never an open relay. Point any SMTP/POP3 client at `127.0.0.1:2525` /
`127.0.0.1:1110`.

### JSON output for scripting

Every command accepts `--json` and returns `{"success", "data", "error", "metadata"}`:

```bash
fengtang list --json --unread | jq '.data[0].subject'
```

## Agent Integration (OpenAI Function Calling)

```python
from fengtang.agent.tools import TOOLS, dispatch

# 1. Pass TOOLS to your model's tool list.
# 2. When the model calls a tool, route it:
result = dispatch("fengtang_send", {
    "to": ["bob@example.com"],
    "subject": "Hi from the agent",
    "body": "Sent via fengtang tool call.",
})
print(result)  # {"success": True, "data": {...}, "error": None, "metadata": {...}}
```

Available tools: `fengtang_send`, `fengtang_list`, `fengtang_read`, `fengtang_search`, `fengtang_mark`, `fengtang_delete`, `fengtang_move`, `fengtang_fetch`, `fengtang_folders`, `fengtang_account_add`, `fengtang_account_list`, `fengtang_account_test`.

Inspect the schema yourself: `fengtang api --schema`.

## Python API

```python
from fengtang import ToolResult, send_mail, list_messages, read_message, search_messages, mark_messages

result = send_mail(to=["bob@example.com"], subject="Hi", body="Hello")
if result.success:
    print(result.data["message_id"])
```

All API functions return a `ToolResult` dataclass (`success`, `data`, `error`, `metadata`, `.to_dict()`, truthiness via `__bool__`).

## Configuration

Accounts live in `~/.fengtang/config.json` (0600). See **Data & file locations** above for the full layout and how to relocate it. Per-run overrides:

- `FENGTANG_DATA_DIR` — data directory
- `fengtang serve --db <path>` — custom database path for the built-in server

## Project structure

```
fengtang/
├── core/            # config (accounts/presets), errors (ToolResult),
│                    # auth (SASL PLAIN/LOGIN/CRAM-MD5/XOAUTH2/NTLM/APOP),
│                    # _des + _md4 (pure-Python crypto primitives for NTLM)
├── mail/            # smtp_client, imap_client, pop_client,
│                    # parser (MIME build/parse/render), store (SQLite)
├── serve/           # built-in MTA: asyncio SMTP + POP3 server, pure-Python
│                    # DNS resolver (dns.py), outbound delivery (outbound.py),
│                    # external-mailbox pull (pull.py)
├── agent/           # OpenAI function-calling TOOLS + dispatch
└── cli/             # argparse CLI (config/send/fetch/list/read/search/…)
tests/               # pytest suite incl. end-to-end client↔built-in-server loops
```

## Development

Any Python ≥ 3.10 environment. From a source checkout:

```bash
pip install -e ".[dev]"   # adds pytest / ruff / mypy
pytest                    # 59 tests
ruff check . && ruff format .
mypy fengtang
```

## Acknowledgments & prior art

FengTang builds on decades of protocol work, and for the trickiest part — modern
OAuth2 against Gmail and Outlook — it deliberately follows **Mozilla Thunderbird's**
implementation rather than guessing at provider rules:

- **OAuth2 provider data** (`fengtang/mail/oauth.py`, `BUILTIN_PROVIDERS`) mirrors
  Thunderbird's `mailnews/base/src/OAuth2Providers.sys.mjs`: the built-in public
  client IDs (Google `406964657835-…`, Microsoft `9e5f94bc-…`), the authorization
  and token endpoints, the exact mail scopes (Google `https://mail.google.com/`;
  Microsoft `outlook.office.com/IMAP.AccessAsUser.All`,
  `POP.AccessAsUser.All`, `SMTP.Send`, `offline_access`), and which providers
  require PKCE.
- **The browser OAuth flow** (`interactive_login`) mirrors Thunderbird's
  `mailnews/base/src/OAuth2.sys.mjs`: a loopback listener on `127.0.0.1` /
  `localhost` bound to a **randomly chosen port** (the providers' registered
  loopback redirects permit any port), a local `state` check, and the
  authorization-code exchange including `code_verifier` and `client_secret`
  where the provider requires them.
- Thunderbird is MPL-2.0. The values reused here are factual
  protocol/registration data; all code in FengTang is an independent Python
  implementation.

Other references: RFC 5321 (SMTP), RFC 3501 (IMAP4rev1), RFC 1939 (POP3),
RFC 4616 (SASL PLAIN), RFC 2195 (CRAM-MD5), RFC 7628 (OAUTHBEARER), RFC 7636
(PKCE), RFC 2595 (STARTTLS), and Microsoft's MS-NLMP.

## Notes

- QQ/163/126 mailboxes use "authorization codes" (授权码) instead of the account password — pass it via `--password`. Credentials are stored **only** in `~/.fengtang/config.json` (0600); they never appear in code, tests, or docs.
- XOAUTH2 tokens can be supplied with `fengtang config test -a acct --oauth2-token <token>` or `Account.oauth2_token` for Gmail/Outlook OAuth flows.
- The built-in server stores delivered mail in the same SQLite store, so `fengtang fetch --protocol pop3` round-trips against it — useful for testing agents without touching a real mailbox.

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)).