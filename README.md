# prompt-optimizer

Turn a raw prompt into a production-ready prompt, right from your terminal.

`prompt-optimizer` installs one command, `optimize`. It sends your prompt through
a Principal-Prompt-Engineer instruction set and returns a rewritten prompt —
with a persona, explicit constraints, an output format, and `{{variable}}`
placeholders — plus an explanation of what changed and why.

The rewrite is tuned for **cache hits** and **speed** without dropping detail:
stable instructions are moved to the front and kept byte-identical between runs
so provider prompt caching applies, volatile content is pushed to the end, and
redundancy is removed without removing a single requirement.

## Install

```bash
pip install prompt-optimizer
```

## Setup

Save your provider API key once. The key is **verified with a live call before it
is saved**, so a typo or a bad key fails immediately instead of silently:

```bash
optimize <api_key> <provider>
```

For example:

```bash
optimize sk-xxxxxxxxxxxxxxxxxxxxxxxx deepseek
```

You can also use the unambiguous flag form:

```bash
optimize --set-key sk-xxxxxxxxxxxxxxxxxxxxxxxx --provider openai
```

## Use

```bash
optimize "write me a sql query"
```

The reply has two sections: an analysis of what was vague in your prompt, and the
rewritten prompt in a copy-pasteable block.

### Options

| Flag | Purpose |
| --- | --- |
| `--provider NAME` | Use this provider for one call instead of the saved one. |
| `--model MODEL` | Override the model. |
| `--base-url URL` | Override the API base URL (self-hosted or proxy endpoints). |
| `--system-file PATH` | Use your own optimizer system prompt from a file. |
| `--json` | Emit `{"analysis": ..., "optimized": ...}` for scripting. |
| `--quiet`, `-q` | Print only the optimized prompt. |
| `--version` | Print the version. |

Prompts can also come from stdin, which is handy in pipelines:

```bash
cat rough_prompt.txt | optimize --quiet > optimized_prompt.txt
```

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Runtime failure: rejected key, unknown model, rate limit, network error. |
| `2` | Usage error: no key configured, no prompt given, unknown provider. |

## Providers

| Provider | Default model | Env var |
| --- | --- | --- |
| `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` |
| `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| `google` | `gemini-2.0-flash` | `GEMINI_API_KEY` |
| `openrouter` | `openai/gpt-4o-mini` | `OPENROUTER_API_KEY` |
| `anthropic` | `claude-3-5-haiku-latest` | `ANTHROPIC_API_KEY` |

`gemini` and `claude` are accepted as aliases for `google` and `anthropic`, as is
`open-router` for `openrouter`. Every provider is reached over its
OpenAI-compatible chat-completions endpoint.

The defaults are deliberately cheap and fast models. Override per call with
`--model`, or save a different one alongside your key:

```bash
optimize <api_key> openai --model gpt-4o
```

## Where your key is stored

By default:

- Linux/macOS: `~/.config/prompt-optimizer/config.json`
- Windows: `%APPDATA%\prompt-optimizer\config.json`

Override the location with the `PROMPT_OPTIMIZER_CONFIG` environment variable.

**The key is stored in plaintext.** That is a deliberate tradeoff for a CLI. The
file is created with mode `0600` on Unix, but on Windows the permission bits are
best-effort. If you would rather not write it to disk at all, set the provider's
env var instead — it takes precedence over the saved key:

```bash
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
optimize "write me a sql query"
```

## Customizing the optimizer

`src/prompt_optimizer/system_prompt.py` holds the instruction set sent to the
provider. It is the one file to edit to change how prompts are rewritten. For a
per-call override without touching the source, use `--system-file`:

```bash
optimize --system-file my_system_prompt.md "write me a sql query"
```

## Development

```bash
git clone https://github.com/YESSS-STAR/prompt-optimizer
cd prompt-optimizer
python -m pip install -e .
python -m unittest discover -s tests
```

## License

MIT
