# ADCode2API

OpenAI-compatible local proxy for **ADCode** (the ADCode desktop app, package
`bluethenics.ADCode`, upstream `https://adcode.bluethenics.com`).

Point any OpenAI/Anthropic client at `http://127.0.0.1:61024/v1` and it runs on
ADCode's included-AI models — no API key, no cost beyond the app's own quota.

## Models (upstream `/v1/ai/tagflow/config`)

| id | context | max output |
|---|---|---|
| `tagflow-code-27b` | 262144 | 50000 |
| `heabsy-qwen38` | 131072 | 50000 |
| `heabsy-glm53-flash-unc` | 131072 | 50000 |
| `heabsy-mimo-v26-flash` | 131072 | 50000 |

`GET /v1/models` is served live from upstream (5 min cache).

## Install

Pure Python 3 standard library — no pip packages needed.

Grab a binary from [Releases](https://github.com/ErfanBagheri404/ADCode2API/releases)
(`ADCode2API-windows-amd64.exe`, `-linux-amd64`, `-macos-amd64`, `-macos-arm64`)
or run from source:

```bash
cd E:/Dev/Projects/ADCode2API
python main.py            # menu
python main.py --no-menu  # server only
```

## Sign in

The proxy reuses ADCode's own Firebase identity.

```bash
python main.py --import-app   # adopt the signed-in desktop app's identity (recommended)
python main.py --login        # GitHub device flow
python main.py --login github # same, explicit
```

Google sign-in is not available: ADCode's desktop build ships no OAuth client
secret, so Google's token endpoint rejects the code exchange. Use the desktop
app import or GitHub.

Tokens are cached in `%APPDATA%/ADCode2API/auth.json` and refreshed automatically
60 s before expiry.

## Endpoints

| route | notes |
|---|---|
| `GET /v1/models` | OpenAI model list, live from upstream |
| `POST /v1/chat/completions` | OpenAI chat, streaming and non-streaming |
| `POST /v1/messages` | Anthropic Messages API (translated; streaming supported) |
| `GET /healthz` | `{"status":"ok"}` |
| `*` | anything else is passed through to upstream with your token |

Optional local key: `--api-key sk-local` then send `Authorization: Bearer sk-local`
(or `x-api-key`). Loopback bind only by default.

## Client config

OpenAI SDK / any OpenAI-compatible client:

```json
{
  "base_url": "http://127.0.0.1:61024/v1",
  "api_key": "not-needed",
  "model": "heabsy-qwen38"
}
```

Claude Code and other Anthropic clients:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:61024
export ANTHROPIC_AUTH_TOKEN=not-needed
export ANTHROPIC_MODEL=heabsy-qwen38
```

## Env overrides

| var | default |
|---|---|
| `ADCODE2API_PORT` | `61024` |
| `ADCODE2API_UPSTREAM` | `https://adcode.bluethenics.com` |
| `ADCODE_FIREBASE_API_KEY` | app's public key (embedded) |

## Notes

- Cloudflare sits in front of ADCode's API and blocks the stock Python user-agent;
  `client.py` sends the desktop app's UA (`ADCode/2.4.0`) on every request.
- Upstream ignores `stream: false` on some routes, so the proxy forces a
  non-streaming body for buffered calls.
- The gateway validates an allowlist and 400s the whole request
  (`Send a supported chat request.`) on any field it doesn't know, so the proxy
  drops `temperature`, `top_p`, `stop`, `n`, `user`, `metadata`,
  `response_format`, `tool_choice`, … and keeps only `model`, `messages`,
  `max_tokens`/`max_completion_tokens`, `stream`, `stream_options`, `tools`,
  `reasoning_effort`. Model ids with a router prefix (`adcode/heabsy-qwen38`)
  are stripped to the bare id, and content part arrays are flattened to text.
- Anthropic `/v1/messages` is served by translating to OpenAI format upstream —
  ADCode's own `/v1/messages` route is not exposed for this account type.

## Tests

```bash
python tests/selfcheck.py
```
