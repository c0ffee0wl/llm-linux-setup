# Troubleshooting

### Update fails

**"fatal: Not possible to fast-forward, aborting"**

This error occurs when your local git branch has diverged from the remote (both have conflicting changes). This typically happens if you made local edits to files that were also updated remotely.

**Solutions:**

```bash
# Option 1: Discard local changes and match remote (recommended)
cd /opt/llm-linux-setup
git reset --hard origin/main
./install-llm-tools.sh

# Option 2: Try to reapply your local commits on top of remote changes
git pull --rebase

# Option 3: Nuclear option - delete and re-clone
sudo rm -rf /opt/llm-linux-setup
sudo git clone https://github.com/c0ffee0wl/llm-linux-setup.git /opt/llm-linux-setup
sudo chown -R $(whoami):$(whoami) /opt/llm-linux-setup
cd /opt/llm-linux-setup
./install-llm-tools.sh
```

### Command completion not working

**For Ctrl+N (AI completion)**:

1. Restart your shell or source your profile:

   ```bash
   source ~/.bashrc  # or ~/.zshrc
   ```

2. Verify llm is in PATH:

   ```bash
   which llm
   ```

3. Test llm command completion:

   ```bash
   llm cmdcomp "list files"
   ```

**For Tab completion (Zsh only)**:

1. Verify you're using Zsh: `echo $SHELL` (should show `/bin/zsh` or similar)

2. Clear completion cache and restart shell:

   ```bash
   rm -f ~/.zcompdump*
   exec zsh
   ```

3. Verify the plugin is in fpath:

   ```bash
   echo $fpath | grep llm-zsh-plugin
   ```

4. Test tab completion:

   ```bash
   llm <TAB>  # Should show: chat, code, rag, models, etc.
   ```

### Azure API errors

1. Verify API key is set:

   ```bash
   llm keys get azure
   ```

2. Check model configuration:

   ```bash
   cat ~/.config/io.datasette.llm/extra-openai-models.yaml
   ```

3. Update the API base URL in the YAML file if needed

### Rust version issues

**Problem**: `cargo install` fails with errors about minimum Rust version

**Solution**: The script automatically detects and offers to upgrade Rust to 1.85+ via rustup. If you declined during installation:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
```

**Check Rust version**:

```bash
rustc --version
```

### Node.js version issues

**Problem**: npm or node commands fail, or Claude Code won't install

**Solution**: The script requires Node.js 20+. If you have an older version:

```bash
# Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc  # or ~/.zshrc

# Install Node 22
nvm install 22
nvm use 22
nvm alias default 22
```

### Session recording not working

**Problem**: `context` command shows "No asciinema session recording found"

**Solutions**:

1. Verify asciinema is installed and in PATH:

   ```bash
   which asciinema
   ```

2. Check shell integration is loaded:

   ```bash
   grep -r "llm-integration" ~/.bashrc ~/.zshrc
   ```

3. Restart your shell or re-source your RC file:

   ```bash
   source ~/.bashrc  # or ~/.zshrc
   ```

4. Check if recording is active (should see asciinema process):

   ```bash
   ps aux | grep asciinema
   ```

### Context shows wrong session

**Problem**: `context` command shows old or wrong session history

**Solutions**:

1. Check current session file:

   ```bash
   echo $SESSION_LOG_FILE
   ```

2. Manually set session file if needed:

   ```bash
   export SESSION_LOG_FILE="/path/to/your/session.cast"
   ```

3. Get correct export command:

   ```bash
   context -e
   ```

### tmux panes not recording independently

**Problem**: New tmux panes don't get their own recordings

**Solution**: Check for pane-specific environment markers:

```bash
env | grep IN_ASCIINEMA_SESSION
```

You should see markers like `IN_ASCIINEMA_SESSION_tmux_0=1` for each pane. If not, re-source your shell RC file in the new pane:

```bash
source ~/.bashrc  # or ~/.zshrc
```
