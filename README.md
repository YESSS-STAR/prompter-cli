# prompter-cli

Turn a raw prompt into a production-ready prompt, right from your terminal.

`prompter-cli` installs one command, `optimize`. It sends your prompt through
a Principal-Prompt-Engineer instruction set and returns a rewritten prompt —
with a persona, explicit constraints, an output format, and `{{variable}}`
placeholders — plus an explanation of what changed and why.

The rewrite is tuned for **cache hits** and **speed** without dropping detail:
stable instructions are moved to the front and kept byte-identical between runs
so provider prompt caching applies, volatile content is pushed to the end, and
redundancy is removed without removing a single requirement.


## Install

```bash
pip install prompter-cli
```

## Caching: the honest answer

Read this before relying on the caching claim, because the honest answer is
"not by default".

Prompt caching is prefix-based: a provider caches the longest **unchanged
prefix** of a request. `optimize` sends one fixed system prompt with a different
user prompt on every call, so the cacheable prefix is **the system prompt
alone** — roughly 450 tokens. And most providers refuse to cache a prefix that
short:

| Provider | Minimum cacheable prefix | How caching turns on |
| --- | --- | --- |
| OpenAI | ~1,024 tokens | automatic |
| Google (Gemini 2.5 Flash) | ~1,024 tokens | automatic |
| Google (Gemini 2.5 Pro) | ~4,096 tokens | automatic |
| Anthropic (Sonnet 4.x, Opus 4/4.1) | ~1,024 tokens | explicit `cache_control` block |
| Anthropic (Opus 4.5+, Haiku 4.5) | ~4,096 tokens | explicit `cache_control` block |
| Anthropic (Haiku 3.5) | ~2,048 tokens | explicit `cache_control` block |
| DeepSeek | none | automatic |

So on every provider except DeepSeek, **the default prefix is too short to ever
be cached**, no matter how the prompt is ordered.

`--big` is the fix. It prepends a stable reference block (a prompt-engineering
rubric, a judging checklist, and a worked before/after example) that pushes the
prefix past ~1,200 tokens, so it clears the common minimum. Once that prefix is
cached, later runs reuse it at the provider's cached-input rate — up to a 90%
discount on those tokens. The block is fixed text, so it costs nothing extra
after the first call.

Measured on DeepSeek with the same prompt twice, before and after `--big`:

| Run | Prefix | Cached input tokens | Reuse |
| --- | --- | --- | --- |
| default, run 1 | ~456 | 128 / 360 | 36% |
| default, run 2 | ~456 | 128 / 360 | 36% — never grows |
| `--big`, run 1 | ~1,203 | 256 / 996 | 26% — writing the prefix |
| `--big`, run 2 | ~1,203 | **768 / 996** | **77%** |

`--big` tripled the reused prefix. The default figure is the interesting one: it
stays flat at 128 tokens however many times you run, because the only reusable
part is a small fixed prefix and everything else changes per call.

`--usage` reports the real numbers for your account and model instead of asking
you to trust this table:

```console
$ optimize --big --usage "write me a sql query"
       usage: input=996 output=1078
       cache: 768/996 input tokens reused (77%)
       system prompt ~1203 tokens; prefix written to the cache on first use
```

If the prefix is still too short, `--usage` says so and names the threshold
rather than reporting a silent miss. Note that Anthropic needs an explicit cache
breakpoint, which this tool does not send yet, so treat Anthropic as uncached.


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
| `--target TARGET` | `auto` (default), `system`, `user`, or `tool` — specialises the rewrite. |
| `--big` | Prepend a stable reference block so the prefix clears provider cache minimums. |
| `--usage` | Print token and cache-hit telemetry to stderr. |
| `--check` | Second pass that reports any requirement the rewrite dropped. |
| `--system-file PATH` | Use your own optimizer system prompt from a file. |
| `--json` | Emit `analysis`, `optimized`, `fidelity` and `usage` as JSON. |
| `--quiet`, `-q` | Print only the optimized prompt. |
| `--version` | Print the version. |

### Targets

`--target system` rewrites for a system message (durable rules first, no
conversational framing, reference material ordered ahead of per-request detail).
`--target tool` documents a tool definition: when to call it, every parameter
with type and units, what it returns, and how failures surface.

### Verifying a rewrite

The real risk in an LLM rewriter is silent detail loss: a requirement that
disappears while the prompt still reads well. `--check` runs a second pass that
compares the rewrite against your original and reports what it dropped:

```console
$ optimize --check "must be read-only and return JSON only"
fidelity: 1 requirement(s) may have been dropped:
  - must be read-only
```

The verdict goes to stderr, so `--quiet > file` stays pipeable. It costs a
second API call, which is why it is opt-in.

## Stdin

Prompts can also come from stdin, which is handy in pipelines:

```bash
cat rough_prompt.txt | optimize --quiet > optimized_prompt.txt
```

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Runtime failure: rejected key, unknown model, rate limit, network error. |
| `2` | Usage error: no key configured, no prompt given, unknown provider, unreadable `--system-file`. |

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

- Linux/macOS: `~/.config/prompter-cli/config.json`
- Windows: `%APPDATA%\prompter-cli\config.json`

Override the location with the `PROMPTER_CONFIG` environment variable.

**The key is stored in plaintext.** That is a deliberate tradeoff for a CLI.

On Unix the file is chmod'd to `0600`. **On Windows it is not protected at all:**
the mode argument to `os.open` is ignored and the file inherits the directory's
ACLs, so it is typically readable by any process running as your user. Treat the
Windows copy as plaintext in a shared location.

If you would rather not write it to disk, set the provider's env var instead — it
takes precedence over the saved key and nothing is written:

```bash
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
optimize "write me a sql query"
```

### The command name is a heuristic

`optimize <token> <provider>` is recognised as setup only when the first token
looks like a key (a known provider prefix such as `sk-`, `AIza`, `gsk_`, or a
random-looking mixed-case-and-digit string). Otherwise the whole line is treated
as the prompt, so `optimize README_markdown_v2 openai` optimizes a prompt rather
than storing a filename as your key.

If you are ever unsure, `--set-key` is unambiguous:

```bash
optimize --set-key <key> --provider openai
```

## Customizing the optimizer

`src/prompt_optimizer/prompts.py` holds the instruction sets sent to the
provider: the base instruction set, the per-target variants, and the `--big`
reference block. It is the one file to edit to change how prompts are rewritten.
For a per-call override without touching the source, use `--system-file`:

```bash
optimize --system-file my_system_prompt.md "write me a sql query"
```

## Development

```bash
git clone https://github.com/YESSS-STAR/prompter-cli
cd prompter-cli
python -m pip install -e .
python -m unittest discover -s tests
```

### Releasing

Publishing uses PyPI trusted publishing, so no API token is stored anywhere.
Pushing a `v*` tag builds, tests and publishes via `.github/workflows/publish.yml`:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Bump `version` in `pyproject.toml` before tagging.

## License

MIT
