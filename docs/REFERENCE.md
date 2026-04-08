# Reference

## What Gets Installed

### Core Tools

- **[llm](https://github.com/c0ffee0wl/llm)** - LLM CLI tool (fork with markdown markup enhancements, originally by Simon Willison - [Documentation](https://llm.datasette.io/))
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** - Anthropic's official agentic coding CLI
- **[Claude Code Router](https://github.com/musistudio/claude-code-router)** - Multi-provider routing proxy for Claude Code (Azure + Gemini dual-provider or Gemini-only)

### Prerequisites

- **[Python 3](https://python.org/)** - Required for llm
- **[uv](https://docs.astral.sh/uv/)** - Modern Python package installer
- **[Node.js](https://nodejs.org/)** - JavaScript runtime (v20+, from repositories or nvm)
- **[Rust/Cargo](https://www.rust-lang.org/)** - Rust toolchain (v1.85+, from repositories or rustup)
- **[Go](https://go.dev/)** - Go toolchain (v1.22+, optional, for imagemage)
- **[argc](https://github.com/sigoden/argc)** - Bash CLI framework and command runner (enables optional llm-functions integration)
- **[bubblewrap](https://github.com/containers/bubblewrap)** - Sandboxing tool for llm-tools-sandboxed-shell
- **[poppler-utils](https://poppler.freedesktop.org/)** - PDF utilities (pdftotext for RAG)
- **[pandoc](https://pandoc.org/)** - Document converter (DOCX support for RAG)
- **[xsel](https://github.com/kfish/xsel)** - X11 clipboard tool (enables pbcopy/pbpaste on Linux)
- **[jq](https://stedolan.github.io/jq/)** - Command-line JSON processor

### LLM Plugins

- **[llm-cmd](https://github.com/c0ffee0wl/llm-cmd)** - Command execution and management
- **[llm-cmd-comp](https://github.com/c0ffee0wl/llm-cmd-comp)** - AI-powered command completion (powers Ctrl+N)
- **[llm-tools-quickjs](https://github.com/simonw/llm-tools-quickjs)** - JavaScript execution tool
- **[llm-tools-sqlite](https://github.com/simonw/llm-tools-sqlite)** - SQLite database tool
- **[llm-jq](https://github.com/simonw/llm-jq)** - JSON processing tool
- **[llm-tools-sandboxed-shell](https://github.com/c0ffee0wl/llm-tools-sandboxed-shell)** - Sandboxed shell command execution
- **[llm-tools-patch](https://github.com/c0ffee0wl/llm-tools-patch)** - File manipulation tools (read, write, edit, multi_edit, info)
- **[llm-tools-context](../llm-tools-context/)** - Terminal history integration (exposes `context` tool to AI)
- **[llm-fragments-site-text](https://github.com/daturkel/llm-fragments-site-text)** - Web page content extraction
- **[llm-fragments-pdf](https://github.com/daturkel/llm-fragments-pdf)** - PDF content extraction
- **[llm-fragments-github](https://github.com/simonw/llm-fragments-github)** - GitHub repository integration
- **[llm-fragments-youtube-transcript](https://github.com/c0ffee0wl/llm-fragments-youtube-transcript)** - YouTube video transcript extraction with metadata
- **[llm-arxiv](https://github.com/c0ffee0wl/llm-arxiv)** - arXiv paper search, fetch, and image extraction
- **[llm-fragments-dir](https://github.com/RKeelan/llm-fragments-dir)** - Load all text files from a local directory recursively
- **[llm-templates-fabric](https://github.com/c0ffee0wl/llm-templates-fabric)** - Fabric prompt templates
- **[llm-tools-llm-functions](https://github.com/c0ffee0wl/llm-tools-llm-functions)** - Bridge for optional [llm-functions](https://github.com/sigoden/llm-functions) integration (enables custom tools in Bash/JS/Python)
- **[llm-gemini](https://github.com/simonw/llm-gemini)** - Google Gemini models integration
- **[llm-vertex](https://github.com/c0ffee0wl/llm-vertex)** - Google Vertex AI Gemini models integration
- **[llm-openrouter](https://github.com/simonw/llm-openrouter)** - OpenRouter API integration
- **[llm-anthropic](https://github.com/simonw/llm-anthropic)** - Anthropic Claude models integration
- **[llm-git-commit](https://github.com/ShamanicArts/llm-git-commit)** - AI-powered Git commit message generation with interactive refinement
- **[llm-sort](https://github.com/vagos/llm-sort)** - Semantic sorting using LLM-based pairwise comparisons
- **[llm-classify](https://github.com/irthomasthomas/llm-classify)** - Text classification with confidence scoring using logprobs

### LLM Templates

- **[llm.yaml](../llm-templates/llm.yaml)** - Custom assistant template with security/IT expertise configuration (Optimized for cybersecurity and Linux tasks, includes `context` and `sandboxed_shell` tools by default)
- **[llm-code.yaml](../llm-templates/llm-code.yaml)** - Code-only generation template (outputs clean, executable code without markdown)
- **[llm-wut.yaml](../llm-templates/llm-wut.yaml)** - Command-line assistant for explaining terminal output and troubleshooting (concise 5-sentence responses, uses `context` tool automatically)
- **[llm-assistant.yaml](../llm-templates/llm-assistant.yaml)** - AI pair programming template for Terminator terminal (provides intelligent debugging, command suggestions, and automatic execution in split panes)

### Additional Tools

- **[gitingest](https://github.com/coderamp-labs/gitingest)** - Convert Git repositories to LLM-friendly text
- **[yek](https://github.com/bodo-run/yek)** - Fast repository to LLM-friendly text converter (230x faster than alternatives, written in Rust)
- **[files-to-prompt](https://github.com/c0ffee0wl/files-to-prompt)** - File content formatter for LLM prompts
- **[asciinema](https://asciinema.org/)** - Terminal session recorder (built from source for latest features)
- **[context](../llm-tools-context/)** - Terminal history extraction from asciinema recordings (CLI + LLM tool)
- **[Micro](https://github.com/zyedidia/micro)** - Modern terminal text editor with [llm-micro](https://github.com/ShamanicArts/llm-micro) plugin for in-editor AI assistance
- **[imagemage](https://github.com/c0ffee0wl/imagemage)** - Gemini image generation CLI (requires Go 1.22+, only installed when Gemini is configured)
- **[onnx-asr](https://github.com/istupakov/onnx-asr)** - Speech-to-text transcription using NVIDIA Parakeet TDT (25 European languages, auto-punctuation)
- **[llm-assistant](../llm-assistant/llm-assistant)** - TmuxAI-inspired AI assistant for Terminator terminal (automatic command execution, watch mode)

### Shell Integration

- AI-powered command completion (Ctrl+N) - see [`llm-integration.bash`](../integration/llm-integration.bash) / [`.zsh`](../integration/llm-integration.zsh)
- Tab completion for llm commands (Zsh only) - see [`llm-zsh-plugin`](../integration/llm-zsh-plugin/)
- Custom llm wrapper with automatic template application - see [`llm-common.sh`](../integration/llm-common.sh)
- Automatic session recording with asciinema - see [`llm-common.sh`](../integration/llm-common.sh)
- macOS-style clipboard aliases (`pbcopy`/`pbpaste` via `xsel` on Linux)
- Common aliases and PATH configuration

## Configuration Files

- `~/.config/io.datasette.llm/` - LLM configuration directory
  - `extra-openai-models.yaml` - Azure OpenAI model definitions
  - `templates/llm.yaml` - Custom [assistant template](../llm-templates/llm.yaml) with security/IT expertise (cybersecurity focus)
  - `templates/llm-code.yaml` - [Code-only generation template](../llm-templates/llm-code.yaml) (no markdown, no explanations)
  - `default_model.txt` - Currently selected default model
  - API keys stored securely via llm's key management

- `~/.config/llm-tools/` - Additional tool configuration
  - `asciinema-commit` - Tracks asciinema version for update detection

- `$SESSION_LOG_DIR/` - Session recording storage
  - Default: `/tmp/session_logs/asciinema/` (temporary) or `~/session_logs/asciinema/` (permanent)
  - Contains `.cast` files with terminal session recordings
  - Configured via `SESSION_LOG_DIR` environment variable in your shell RC file

- `~/.config/io.datasette.llm/rag/` - RAG collections (llm-tools-rag)
  - Contains ChromaDB vector databases and BM25 indices

## Shell Integration Files

Located in the `integration/` subdirectory:

- [`integration/llm-integration.bash`](../integration/llm-integration.bash) - Bash integration (Ctrl+N keybinding)
- [`integration/llm-integration.zsh`](../integration/llm-integration.zsh) - Zsh integration (Ctrl+N keybinding)
- [`integration/llm-common.sh`](../integration/llm-common.sh) - Shared configuration (llm wrapper function, auto-recording)

These are automatically sourced from your `.bashrc` or `.zshrc`.

## Credits

### Core Tools & Frameworks

- [Simon Willison](https://github.com/simonw) - Original llm CLI tool and plugins (llm-gemini, llm-anthropic, llm-openrouter, llm-jq, llm-tools-sqlite, llm-tools-quickjs, llm-fragments-github, llm-cmd)
- [c0ffee0wl](https://github.com/c0ffee0wl) - llm fork with markdown markup enhancements
- [sigoden](https://github.com/sigoden) - argc Bash CLI framework and llm-functions framework
- [Anthropic](https://www.anthropic.com/) - Claude Code agentic coding CLI
- [Astral](https://astral.sh/) - uv Python package manager
- [Rust Foundation](https://foundation.rust-lang.org/) - Rust programming language and Cargo
- [Node.js Foundation](https://nodejs.org/) - Node.js JavaScript runtime

### LLM Plugins & Extensions

- [Daniel Turkel](https://github.com/daturkel) - llm-fragments-pdf, llm-fragments-site-text
- [Ryan Patterson ](https://github.com/CGamesPlay) - llm-cmd-comp plugin
- [Dan Mackinlay](https://github.com/danmackinlay) - files-to-prompt (fork)
- [Damon McMinn](https://github.com/damonmcminn) - llm-templates-fabric (fork)
- [Daniel Miessler](https://github.com/danielmiessler) - Original Fabric prompt patterns
- [ShamanicArts](https://github.com/ShamanicArts) - llm-git-commit AI-powered commit messages
- [vagos](https://github.com/vagos) - llm-sort semantic sorting
- [irthomasthomas](https://github.com/irthomasthomas) - llm-classify text classification
- [RKeelan](https://github.com/RKeelan) - llm-fragments-dir directory fragment loader

### Additional Tools

- [Bubblewrap Project](https://github.com/containers/bubblewrap) - Sandboxing tool for unprivileged containers
- [stedolan/jq](https://github.com/stedolan/jq) - Command-line JSON processor
- [Asciinema](https://github.com/asciinema/asciinema) - Terminal session recorder
- [Coderamp Labs](https://github.com/coderamp-labs/gitingest) - gitingest repository analyzer
- [Zachary Yedidia](https://github.com/zyedidia/micro) - Micro modern terminal text editor
- [ShamanicArts](https://github.com/ShamanicArts/llm-micro) - llm-micro plugin for in-editor AI assistance
- [bodo-run](https://github.com/bodo-run/yek) - yek fast repository converter
- [istupakov](https://github.com/istupakov/onnx-asr) - onnx-asr speech recognition
- [NVIDIA](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) - Parakeet TDT ASR model

## License

This installation script is provided as-is.
Individual tools have their own licenses:

- llm: Apache 2.0
- See individual tool repositories for details

## Contributing

To modify or extend this installation, see [CLAUDE.md](../CLAUDE.md) for detailed architecture documentation.

**Key files to understand:**

- [`install-llm-tools.sh`](../install-llm-tools.sh) - Main installation script (7 phases, self-updating)
- [`integration/llm-common.sh`](../integration/llm-common.sh) - Shell wrapper function, auto-recording
- [`llm-tools-context/`](../llm-tools-context/) - Terminal history extraction (CLI + LLM tool)
- [`llm-templates/`](../llm-templates/) - Custom template sources

**Development workflow:**

1. Read [CLAUDE.md](../CLAUDE.md) to understand architecture
2. Edit the scripts in the repository
3. Test your changes
4. Commit and push to git
5. Changes pulled automatically on next run
