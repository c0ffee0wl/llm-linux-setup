# Shell Integration

This file provides guidance to Claude Code when working with shell integration files.

## Overview

Shell integration provides the `llm` wrapper function, keybindings, tab completion, and automatic session recording for Bash and Zsh.

## File Structure (Three-File Pattern)

| File | Purpose |
|------|---------|
| `llm-common.sh` | Shared: PATH, env vars, aliases, llm wrapper, auto-recording |
| `llm-integration.bash` | Bash-specific: sources common, defines Bash widgets |
| `llm-integration.zsh` | Zsh-specific: sources common, defines Zsh widgets |

## LLM Wrapper Function (`llm-common.sh`)

Automatically applies templates and tools to llm commands:

### Template Routing

| Command | Template Applied | Tools Added |
|---------|-----------------|-------------|
| `llm chat` | `-t llm` | context, sandboxed_shell |
| `llm code` | `-t llm-code` | (none) |
| `llm rag` | (none) | (llm-tools-rag plugin) |
| `llm "prompt"` | `-t llm` | context, sandboxed_shell |
| `llm chat -t custom` | custom | (none) |
| `llm chat -s "system"` | (none) | (none) |
| `llm -o google_search 1` | `-t llm` | (none - incompatible) |

### Key Functions

- **`should_skip_template()`**: Checks if user specified `-t`, `-s`, `-c`, `--cid`
- **`has_google_search()`**: Detects `-o google_search` option
- **`exclude_commands` array**: Subcommands that don't get templates (models, keys, plugins, etc.)

### Tool Skipping

Tools are skipped when:
1. User specifies custom template (`-t`), system prompt (`-s`), or continuation (`-c`)
2. `google_search` option detected (Vertex/Gemini incompatibility)

## Keybindings

### Ctrl+N - AI Command Completion

Uses `llm cmdcomp` from llm-cmd-comp plugin.

**Bash implementation** (`llm-integration.bash`):
```bash
bind -x '"\C-n": __llm_cmdcomp'
# Function manipulates READLINE_LINE and READLINE_POINT
```

**Zsh implementation** (`llm-integration.zsh`):
```zsh
zle -N __llm_cmdcomp
bindkey '^N' __llm_cmdcomp
# Function manipulates BUFFER and CURSOR
```

### Changing Keybindings

Update both files with new key code (e.g., `\C-k` for Ctrl+K).

## Tab Completion (Zsh)

Uses `llm-zsh-plugin/` (forked from eliyastein/llm-zsh-plugin):
- Comprehensive completion for all llm commands, options, models, templates
- Custom extensions for `llm code` and `llm rag` subcommands
- Dynamically fetches available models via `llm models list`

Location: `integration/llm-zsh-plugin/completions/_llm`

## VTE Unicode Marker Injection

For reliable prompt detection in Terminator/GNOME Terminal/Tilix:

- **VTE-only**: Injected when `$VTE_VERSION` is set
- **Markers**:
  - `PROMPT_START_MARKER` (`\u200B\u200D\u200B`): Before PS1
  - `INPUT_START_MARKER` (`\u200D\u200B\u200D`): After PS1, where user types
- **Mechanism**: Uses `PROMPT_COMMAND` (Bash) or `precmd_functions` (Zsh)
- **Framework compatible**: Appends to run LAST, after Starship/Powerlevel10k

## wut Function

Explains the output of the last command:
```bash
wut  # Uses `-t llm-wut` template
```

## Automatic Session Recording

See [`llm-tools-context/CLAUDE.md`](../llm-tools-context/CLAUDE.md) for session recording details.

## Testing

```bash
# Test files source correctly
bash -c "source integration/llm-integration.bash && type __llm_cmdcomp"
zsh -c "source integration/llm-integration.zsh && which __llm_cmdcomp"

# Test wrapper function
bash -c "source integration/llm-integration.bash && type llm"

# Test keybinding registration
bash -c "source integration/llm-integration.bash && bind -P | grep llm"
zsh -c "source integration/llm-integration.zsh && bindkey | grep llm"
```

## Troubleshooting

**Tab completion not working**: Clear cache (`rm -f ~/.zcompdump*`) and restart shell.

**Completions show old commands**: Verify `compinit` is loaded (`which compinit`).

**Code/rag subcommands not completing**: Re-run install script to apply modifications.

## Adding New Features

- **Shell-agnostic**: Add to `llm-common.sh`
- **Bash-specific** (readline bindings): Add to `llm-integration.bash`
- **Zsh-specific** (zle widgets): Add to `llm-integration.zsh`
- **New llm subcommands**: Update `llm-zsh-plugin/completions/_llm`
