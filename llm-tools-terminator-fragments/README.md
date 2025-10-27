# llm-tools-terminator-fragments

LLM plugin that exposes Terminator terminal emulator content as context fragments, similar to `llm-tmux-fragments` for tmux.

## Features

- Capture visible scrollback content from Terminator terminals
- Access terminals by UUID, focused terminal, or all terminals
- Format content in XML tags for LLM comprehension
- Integrates with `llm-terminator-sidechat` for AI pair programming

## Installation

```bash
llm install -e /path/to/llm-tools-terminator-fragments
```

## Usage

### As LLM Tool

```bash
# Capture focused terminal
llm --tool terminator "what's in my terminal?" -a terminal_id focused

# Capture specific terminal by UUID
llm --tool terminator "analyze this terminal" -a terminal_id urn:uuid:abc123

# Capture all terminals
llm --tool terminator "what's happening?" -a terminal_id all
```

### Programmatic Usage

```python
import llm

model = llm.get_model("azure/gpt-4.1-mini")
response = model.prompt(
    "What's in my focused terminal?",
    tools=["terminator"]
)
```

## Requirements

- Terminator terminal emulator
- TerminatorSidechatPlugin installed in `~/.config/terminator/plugins/`
- Python 3.8+
- PyGObject (for GTK/VTE bindings)

## Fragment Syntax

- `focused` - Capture currently focused terminal
- `all` - Capture all terminals in Terminator instance
- `urn:uuid:...` - Capture specific terminal by UUID

## Output Format

Content is wrapped in XML tags for LLM understanding:

```xml
<terminal uuid="urn:uuid:abc123" title="Terminal 1" cwd="/home/user">
[terminal content here]
</terminal>
```

## License

GPL v2 only (to match Terminator license)
