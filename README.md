# browser-agent

A Pydantic-AI + Zendriver service that takes a free-form user task
describing a web workflow, inspects the live target site, and returns
a self-contained, executable Python script built on top of
[zendriver](https://github.com/cdpdriver/zendriver).

## Architecture

```
src/browser_agent/
├── configuration.py        # Env-driven config (model name, base URL, paths)
├── logging_config.py       # Loguru setup
├── agent_logging.py        # Shared agent logger
├── domain/                 # Pydantic models that own their behaviour
│   ├── generated_script.py     # Structured agent output (explanation, deps, code)
│   ├── html_snippet.py         # Cleaned, token-optimised page snapshot
│   └── code_generation_request.py
├── ports/                  # Abstract interfaces
│   ├── llm_port.py             # Provider-agnostic LLM
│   └── web_inspector_port.py   # Headless browser → HtmlSnippet
├── adapters/               # Concrete implementations
│   ├── llm/ollama_adapter.py
│   └── browser/zendriver_web_inspector_adapter.py
├── use_cases/              # Orchestration
│   ├── system_prompt.py        # The agent's hard rules
│   ├── inspect_html_tool.py    # Pydantic-AI tool, calls WebInspectorPort
│   ├── agent_deps.py
│   └── generate_zendriver_script_use_case.py
└── drivers/
    └── generate_script.py      # CLI entry point
```

The service is a **single Pydantic-AI agent** with exactly **one
tool**: `inspect_html(url)`. The agent:

1. Calls `inspect_html` on the target URL. The tool launches a
   headless zendriver session, navigates to the page, sleeps briefly
   for SPA rendering, reads the HTML and feeds it through
   BeautifulSoup to strip `<script>`, `<style>`, `<svg>`, `<path>`,
   `<noscript>`, `<iframe>`, `<template>`, `<link>` and HTML
   comments. The result is a token-optimised text snapshot.
2. Synthesises a structured :class:`GeneratedScript` containing a
   step-by-step explanation, the pip dependencies and a fully
   self-contained async Python script. The script **must**:
   - wrap all work in `async def main():` and run with `asyncio.run`;
   - use raw zendriver, **never** import anything from this project;
   - track `document.body.scrollHeight` for infinite scroll instead
     of guessing a number of iterations;
   - insert `await tab.sleep(...)` after every `fill` / `click` /
     `select` / scroll so the DOM has time to settle;
   - extract data defensively (try/except, defaults, `getattr` on
     element text).

## Run

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure
export OLLAMA_API_KEY=...

# 3. Generate a script for a task
python -m browser_agent.drivers.generate_script \
    "Visit https://quotes.toscrape.com and print every quote on the first three pages."

# Or pipe via stdin
echo "Visit example.com and click the More information link." \
    | python -m browser_agent.drivers.generate_script --stdin
```

The driver writes the executable source to
`data/scripts/<slug>.py` and prints the structured
:class:`GeneratedScript` (explanation, dependencies, python_code,
script_path) as JSON.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_API_KEY` | (required) | API key for the Ollama endpoint. |
| `OLLAMA_BASE_URL` | `https://ollama.com/v1` | Base URL of the OpenAI-compatible Ollama endpoint. |
| `ORCHESTRATOR_MODEL` | `deepseek-v4-flash:cloud` | The model the agent runs against. |
| `ZENDRIVER_HEADLESS` | `true` | Whether the inspection tool runs Chrome headless. |
| `SCRIPTS_PATH` | `data/scripts` | Where the driver persists generated scripts. |
| `ZENDRIVER_DOWNLOADS_DIR` | `data/downloads/zendriver` | Where zendriver writes any downloaded files. |
| `ZENDRIVER_PROBE_TIMEOUT_S` | `30` | Hard probe timeout for the inspection tool. |
| `BROWSER_AGENT_LOG_LEVEL` | `INFO` | Loguru level. |
| `BROWSER_AGENT_LOG_FILE` | (unset) | If set, also log to this file (10 MB rotation). |
| `BROWSER_AGENT_LOG_NO_COLOR` | (unset) | Set to `1` to disable ANSI colors. |
