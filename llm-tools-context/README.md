# llm-tools-context

[![PyPI](https://img.shields.io/pypi/v/llm-tools-context.svg)](https://pypi.org/project/llm-tools-context/)
[![Changelog](https://img.shields.io/github/v/release/c0ffee0wl/llm-tools-context?include_prereleases&label=changelog)](https://github.com/c0ffee0wl/llm-tools-context/releases)
[![Tests](https://github.com/c0ffee0wl/llm-tools-context/actions/workflows/test.yml/badge.svg)](https://github.com/c0ffee0wl/llm-tools-context/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/c0ffee0wl/llm-tools-context/blob/main/LICENSE)

A tool that can query the logged shell context

## Installation

Install this plugin in the same environment as [LLM](https://llm.datasette.io/).

```bash
llm install llm-tools-context
```

Or install from the repository:

```bash
llm install /path/to/llm-linux-setup/llm-tools-context
```

## Usage

To use this with the [LLM command-line tool](https://llm.datasette.io/en/stable/usage.html):

```bash
llm --tool context "Example prompt goes here" --tools-debug
```

With the [LLM Python API](https://llm.datasette.io/en/stable/python-api.html):

```python
import llm
from llm_tools_context import context

model = llm.get_model("gpt-4.1-mini")

result = model.chain(
    "Example prompt goes here",
    tools=[context]
).text()
```

## Development

To set up this plugin locally, first checkout the code. Then create a new virtual environment:
```bash
cd llm-tools-context
python -m venv venv
source venv/bin/activate
```
Now install the dependencies and test dependencies:
```bash
llm install -e '.[test]'
```
To run the tests:
```bash
python -m pytest
```
