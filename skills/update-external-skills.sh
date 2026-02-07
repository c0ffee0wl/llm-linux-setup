#!/usr/bin/env bash
#
# Update external skills from GitHub repositories
# Clones skills directly into skills/<name>/ so the install script copies them
# to ~/.claude/skills/ alongside local skills.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$SCRIPT_DIR/external-skills.yaml"
GITIGNORE="$SCRIPT_DIR/.gitignore"
CACHE_DIR="$SCRIPT_DIR/.repo-cache"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

YAML_PARSER=""
CACHED_REPO_DIR=""

check_dependencies() {
    if ! command -v git >/dev/null 2>&1; then
        log_error "Missing required tool: git"
        exit 1
    fi

    if command -v yq >/dev/null 2>&1; then
        YAML_PARSER="yq"
    elif command -v python3 >/dev/null 2>&1; then
        YAML_PARSER="python"
    else
        log_error "Missing YAML parser: install yq or python3"
        exit 1
    fi
}

yaml_skill_count() {
    local file="$1"
    if [[ "$YAML_PARSER" == "yq" ]]; then
        yq -r '.skills | length' "$file" 2>/dev/null || echo "0"
    else
        python3 -c "
import yaml
with open('$file') as f:
    data = yaml.safe_load(f)
skills = data.get('skills', []) if data else []
print(len(skills))
" 2>/dev/null || echo "0"
    fi
}

yaml_skill_field() {
    local file="$1"
    local index="$2"
    local field="$3"
    local default="${4:-}"

    if [[ "$YAML_PARSER" == "yq" ]]; then
        local result
        result=$(yq -r ".skills[$index].$field // \"$default\"" "$file" 2>/dev/null)
        [[ "$result" == "null" ]] && result="$default"
        echo "$result"
    else
        python3 -c "
import yaml
with open('$file') as f:
    data = yaml.safe_load(f)
skills = data.get('skills', []) if data else []
if $index < len(skills):
    print(skills[$index].get('$field', '$default') or '$default')
else:
    print('$default')
" 2>/dev/null || echo "$default"
    fi
}

# Ensure skill is in .gitignore
add_to_gitignore() {
    local skill_name="$1"

    # Create .gitignore if it doesn't exist
    if [[ ! -f "$GITIGNORE" ]]; then
        cat > "$GITIGNORE" << 'EOF'
# External skills (cloned by update-external-skills.sh)
# Do not commit these - they are fetched from upstream repos
.repo-cache/
EOF
    fi

    # Ensure cache dir is ignored
    if ! grep -q "^\.repo-cache/$" "$GITIGNORE" 2>/dev/null; then
        echo ".repo-cache/" >> "$GITIGNORE"
    fi

    # Add skill if not already present
    if ! grep -q "^${skill_name}/$" "$GITIGNORE" 2>/dev/null; then
        echo "${skill_name}/" >> "$GITIGNORE"
        log_info "  Added $skill_name/ to .gitignore"
    fi
}

# Get cache key for a repo (used as directory name)
repo_cache_key() {
    local repo="$1"
    # Convert URL to safe directory name: github.com/owner/repo -> owner_repo
    echo "$repo" | sed -E 's|https://github.com/||; s|/|_|g; s|\.git$||'
}

# Clone or update a repo in cache
# Sets CACHED_REPO_DIR variable with the path
ensure_repo_cached() {
    local repo="$1"
    local ref="$2"
    local cache_key
    cache_key=$(repo_cache_key "$repo")
    CACHED_REPO_DIR="$CACHE_DIR/$cache_key"

    mkdir -p "$CACHE_DIR"

    if [[ -d "$CACHED_REPO_DIR/.git" ]]; then
        log_info "  Updating cached repo..."
        (
            cd "$CACHED_REPO_DIR"
            git fetch --depth 1 origin "$ref"
            git checkout "$ref" 2>/dev/null || git checkout "origin/$ref" 2>/dev/null || true
            git pull --depth 1 --ff-only origin "$ref" 2>/dev/null || true
        )
    else
        log_info "  Cloning to cache..."
        rm -rf "$CACHED_REPO_DIR"
        git clone --depth 1 --branch "$ref" "$repo" "$CACHED_REPO_DIR" 2>/dev/null || \
        git clone --depth 1 "$repo" "$CACHED_REPO_DIR"
    fi
}

update_skills() {
    if [[ ! -f "$MANIFEST" ]]; then
        log_error "Manifest not found: $MANIFEST"
        exit 1
    fi

    local skill_count
    skill_count=$(yaml_skill_count "$MANIFEST")

    if [[ "$skill_count" == "0" ]] || [[ -z "$skill_count" ]]; then
        log_warn "No skills defined in manifest"
        exit 0
    fi

    log_info "Found $skill_count external skill(s) in manifest"

    for ((i=0; i<skill_count; i++)); do
        local name repo ref path
        name=$(yaml_skill_field "$MANIFEST" "$i" "name" "")
        repo=$(yaml_skill_field "$MANIFEST" "$i" "repo" "")
        ref=$(yaml_skill_field "$MANIFEST" "$i" "ref" "main")
        path=$(yaml_skill_field "$MANIFEST" "$i" "path" "")

        if [[ -z "$name" ]] || [[ -z "$repo" ]]; then
            log_warn "Skipping skill $i: missing name or repo"
            continue
        fi

        # Expand short repo format to full URL
        if [[ ! "$repo" =~ ^https:// ]]; then
            repo="https://github.com/$repo"
        fi

        local target_dir="$SCRIPT_DIR/$name"

        log_info "Processing: $name ($repo @ $ref${path:+ path=$path})"

        # Ensure it's gitignored
        add_to_gitignore "$name"

        if [[ -n "$path" ]]; then
            # Subdirectory mode: clone to cache, copy subdirectory
            ensure_repo_cached "$repo" "$ref"
            local source_dir="$CACHED_REPO_DIR/$path"

            if [[ ! -d "$source_dir" ]]; then
                log_error "  Path not found in repo: $path"
                continue
            fi

            # Copy subdirectory contents to target
            log_info "  Copying $path/ to $name/"
            rm -rf "$target_dir"
            cp -r "$source_dir" "$target_dir"
        else
            # Root mode: clone directly (existing behavior)
            if [[ -d "$target_dir/.git" ]]; then
                # Check if remote URL has changed in manifest
                local current_remote
                current_remote=$(git -C "$target_dir" remote get-url origin 2>/dev/null || echo "")
                if [[ "$current_remote" != "$repo" ]]; then
                    log_warn "  Remote URL changed: $current_remote -> $repo"
                    log_info "  Re-cloning from new remote..."
                    rm -rf "$target_dir"
                    git clone --depth 1 --branch "$ref" "$repo" "$target_dir" 2>/dev/null || \
                    git clone --depth 1 "$repo" "$target_dir"
                else
                    log_info "  Updating existing clone..."
                    (
                        cd "$target_dir"
                        git fetch --depth 1 origin "$ref"
                        git checkout "$ref" 2>/dev/null || git checkout "origin/$ref"
                        git pull --depth 1 --ff-only origin "$ref" 2>/dev/null || true
                    )
                fi
            elif [[ -d "$target_dir" ]]; then
                log_warn "  Directory exists but is not a git repo: $target_dir"
                log_warn "  Remove it manually to re-clone"
                continue
            else
                log_info "  Cloning..."
                git clone --depth 1 --branch "$ref" "$repo" "$target_dir" 2>/dev/null || \
                git clone --depth 1 "$repo" "$target_dir"
            fi
        fi

        # Verify SKILL.md exists
        if [[ -f "$target_dir/SKILL.md" ]]; then
            log_info "  ✓ SKILL.md found"
        else
            log_warn "  ⚠ No SKILL.md found in $target_dir/"
        fi
    done

    log_info "Done. Run install script to copy skills to ~/.claude/skills/"
}

main() {
    log_info "External Skills Updater"
    check_dependencies
    update_skills
}

main "$@"
