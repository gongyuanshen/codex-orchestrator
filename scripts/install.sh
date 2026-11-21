#!/usr/bin/env bash
set -euo pipefail

# Idempotent macOS/Linux installer for the tri-orchestrator Codex plugin,
# agent role definitions, [agents] config settings, and cached plugin runtime.

usage() {
  cat <<'EOF'
Usage: install.sh [OPTIONS]

Options:
  --repo-root PATH          Path to cloned codex-orchestrator repo (or plugin dir)
  --plugin-dir PATH         Direct path to plugin/ or cached plugin directory
  --codex-home PATH         Codex home directory (default: $CODEX_HOME or ~/.codex)
  --skip-uv-sync            Skip pre-warming uv sync on the cached plugin runtime
  --skip-plugin-install     Skip codex plugin marketplace/plugin registration
  --trust-plugin-hooks      Trust all 4 plugin hooks in [hooks.state] config.toml
  --keep-backups N          Retain latest N timestamped backups (default: 3; baseline always kept)
  -h, --help                Show this help message
EOF
}

REPO_ROOT=""
PLUGIN_DIR=""
CODEX_HOME_ARG=""
SKIP_UV_SYNC=0
SKIP_PLUGIN_INSTALL=0
TRUST_PLUGIN_HOOKS=0
KEEP_BACKUPS=3

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo-root)
      [ "$#" -ge 2 ] || { echo "Missing value for --repo-root" >&2; exit 2; }
      REPO_ROOT="$2"
      shift 2
      ;;
    --plugin-dir)
      [ "$#" -ge 2 ] || { echo "Missing value for --plugin-dir" >&2; exit 2; }
      PLUGIN_DIR="$2"
      shift 2
      ;;
    --codex-home)
      [ "$#" -ge 2 ] || { echo "Missing value for --codex-home" >&2; exit 2; }
      CODEX_HOME_ARG="$2"
      shift 2
      ;;
    --skip-uv-sync)
      SKIP_UV_SYNC=1
      shift
      ;;
    --skip-plugin-install)
      SKIP_PLUGIN_INSTALL=1
      shift
      ;;
    --trust-plugin-hooks)
      TRUST_PLUGIN_HOOKS=1
      shift
      ;;
    --keep-backups)
      [ "$#" -ge 2 ] || { echo "Missing value for --keep-backups" >&2; exit 2; }
      case "$2" in
        ''|*[!0-9]*)
          echo "Invalid --keep-backups value (expected non-negative integer): $2" >&2
          exit 2
          ;;
      esac
      KEEP_BACKUPS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

abs_dir() {
  (cd "$1" && pwd -P)
}

SCRIPT_DIR="$(abs_dir "$(dirname "$0")")"

resolve_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  for cand in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" "/opt/homebrew/bin/uv" "/usr/local/bin/uv"; do
    if [ -x "$cand" ]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  echo "Could not locate uv. Install uv (https://docs.astral.sh/uv/) and re-run." >&2
  exit 1
}

resolve_codex() {
  local active_codex_home="$1"
  if [ -n "${CODEX_CLI_PATH:-}" ] && [ -x "${CODEX_CLI_PATH}" ]; then
    printf '%s\n' "${CODEX_CLI_PATH}"
    return 0
  fi
  if command -v codex >/dev/null 2>&1; then
    command -v codex
    return 0
  fi
  for cand in "$HOME/.local/bin/codex" "/opt/homebrew/bin/codex" "/usr/local/bin/codex" "$active_codex_home/plugins/.plugin-appserver/codex"; do
    if [ -x "$cand" ]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  echo "Could not locate codex CLI binary. Ensure codex is installed and on PATH." >&2
  exit 1
}

UV_BIN="$(resolve_uv)"

# Resolve Python >= 3.11 (for tomllib/json) without creating any .venv inside repo
PY_BIN=""
if found_py="$("$UV_BIN" python find ">=3.11" 2>/dev/null || true)"; then
  found_py="$(printf '%s' "$found_py" | head -n 1 | tr -d '\r')"
  if [ -n "$found_py" ] && [ -x "$found_py" ]; then
    PY_BIN="$found_py"
  fi
fi
if [ -z "$PY_BIN" ] && command -v python3 >/dev/null 2>&1; then
  if python3 -c "import sys, tomllib; assert sys.version_info >= (3, 11)" >/dev/null 2>&1; then
    PY_BIN="$(command -v python3)"
  fi
fi

run_py() {
  if [ -n "$PY_BIN" ]; then
    env -u VIRTUAL_ENV -u PYTHONPATH PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$PY_BIN" "$@"
  else
    env -u VIRTUAL_ENV -u PYTHONPATH PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$UV_BIN" run --quiet --no-project --python ">=3.11" python "$@"
  fi
}

# Resolve repo vs direct/cached plugin layout
HAS_MARKETPLACE_LAYOUT=0
if [ -n "$PLUGIN_DIR" ]; then
  PLUGIN_DIR="$(abs_dir "$PLUGIN_DIR")"
  if [ -n "$REPO_ROOT" ]; then
    REPO_ROOT="$(abs_dir "$REPO_ROOT")"
    if [ -f "$REPO_ROOT/.agents/plugins/marketplace.json" ]; then
      HAS_MARKETPLACE_LAYOUT=1
    fi
  else
    parent_dir="$(dirname "$PLUGIN_DIR")"
    if [ -f "$parent_dir/.agents/plugins/marketplace.json" ]; then
      REPO_ROOT="$(abs_dir "$parent_dir")"
      HAS_MARKETPLACE_LAYOUT=1
    fi
  fi
else
  if [ -z "$REPO_ROOT" ]; then
    REPO_ROOT="$(abs_dir "$SCRIPT_DIR/..")"
  else
    REPO_ROOT="$(abs_dir "$REPO_ROOT")"
  fi
  if [ -d "$REPO_ROOT/plugin/agents" ] && [ -f "$REPO_ROOT/plugin/pyproject.toml" ]; then
    PLUGIN_DIR="$REPO_ROOT/plugin"
    if [ -f "$REPO_ROOT/.agents/plugins/marketplace.json" ]; then
      HAS_MARKETPLACE_LAYOUT=1
    fi
  elif [ -d "$REPO_ROOT/agents" ] && [ -f "$REPO_ROOT/pyproject.toml" ]; then
    PLUGIN_DIR="$REPO_ROOT"
    HAS_MARKETPLACE_LAYOUT=0
  else
    echo "Could not find self-contained plugin layout (expected plugin/agents/*.toml + plugin/pyproject.toml under $REPO_ROOT)." >&2
    exit 1
  fi
fi

if [ -n "$CODEX_HOME_ARG" ]; then
  CODEX_HOME_DIR="$CODEX_HOME_ARG"
elif [ -n "${CODEX_HOME:-}" ]; then
  CODEX_HOME_DIR="$CODEX_HOME"
else
  CODEX_HOME_DIR="$HOME/.codex"
fi

if [ ! -f "$PLUGIN_DIR/pyproject.toml" ]; then
  echo "Missing plugin pyproject.toml at $PLUGIN_DIR/pyproject.toml" >&2
  exit 1
fi
if [ -f "$PLUGIN_DIR/plugin.json" ]; then
  echo "Legacy root plugin.json must not exist at $PLUGIN_DIR/plugin.json (use .codex-plugin/plugin.json)" >&2
  exit 1
fi
if [ ! -f "$PLUGIN_DIR/.codex-plugin/plugin.json" ]; then
  echo "Missing plugin manifest at $PLUGIN_DIR/.codex-plugin/plugin.json" >&2
  exit 1
fi
if [ ! -f "$PLUGIN_DIR/hooks/hooks.json" ]; then
  echo "Missing plugin hooks config at $PLUGIN_DIR/hooks/hooks.json" >&2
  exit 1
fi

echo "[install] PluginDir : $PLUGIN_DIR"
if [ -n "$REPO_ROOT" ]; then
  echo "[install] RepoRoot  : $REPO_ROOT"
fi
echo "[install] CodexHome : $CODEX_HOME_DIR"
echo "[install] Using uv  : $UV_BIN"

PLUGIN_VERSION="$(run_py - "$PLUGIN_DIR/.codex-plugin/plugin.json" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data.get("hooks") == "./hooks/hooks.json", "Expected .codex-plugin/plugin.json hooks == './hooks/hooks.json'"
print(data.get("version") or "0.1.0")
PY
)"

ROLE_FILES="interviewer.toml planner.toml investigator.toml coder.toml verifier.toml swarm_worker.toml"
AGENTS_SRC_DIR="$PLUGIN_DIR/agents"
if [ ! -d "$AGENTS_SRC_DIR" ]; then
  echo "Missing plugin agents directory: $AGENTS_SRC_DIR" >&2
  exit 1
fi

# Validate all 6 role TOMLs before touching CODEX_HOME
for rf in $ROLE_FILES; do
  src_file="$AGENTS_SRC_DIR/$rf"
  if [ ! -f "$src_file" ]; then
    echo "Missing required agent role file: $src_file" >&2
    exit 1
  fi
  run_py - "$src_file" <<'PY'
import pathlib, sys, tomllib
p = pathlib.Path(sys.argv[1])
d = tomllib.loads(p.read_text(encoding="utf-8"))
assert "model" not in d, f"Hardcoded model forbidden in {p}"
assert "model_reasoning_effort" not in d, f"Hardcoded model_reasoning_effort forbidden in {p}"
assert d.get("name") and d.get("description") and d.get("developer_instructions"), f"Missing required fields in {p}"
PY
done

CODEX_BIN="$(resolve_codex "$CODEX_HOME_DIR")"
echo "[install] Using codex: $CODEX_BIN"

CONFIG_PATH="$CODEX_HOME_DIR/config.toml"
CONFIG_EXISTED=0
if [ -f "$CONFIG_PATH" ]; then
  CONFIG_EXISTED=1
fi

STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tri-orch-cfg.XXXXXX")"
cleanup_stage() {
  rm -rf "$STAGE_DIR"
}
trap cleanup_stage EXIT

CANDIDATE_CONFIG="$STAGE_DIR/config.toml"
AGENTS_META_JSON="$STAGE_DIR/agents_meta.json"

# Safely merge [agents] and [features] in config.toml and validate with tomllib
run_py - "$CONFIG_PATH" "$CANDIDATE_CONFIG" "$AGENTS_META_JSON" <<'PY'
import json, pathlib, re, sys, tomllib

cfg_path = pathlib.Path(sys.argv[1])
out_path = pathlib.Path(sys.argv[2])
meta_path = pathlib.Path(sys.argv[3])

text = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else ""
nl = "\r\n" if "\r\n" in text else "\n"
lines = text.splitlines() if text else []

agents_start = -1
agents_end = len(lines)
for i, line in enumerate(lines):
    trim = line.strip()
    if re.match(r"^\[agents\]\s*(#.*)?$", trim):
        agents_start = i
        continue
    if agents_start >= 0 and trim.startswith("["):
        agents_end = i
        break

meta = {
    "had_agents_section": agents_start >= 0,
    "added_threads_key": False,
    "added_max_depth": False,
}

if agents_start < 0:
    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.extend(["[agents]", "max_threads = 6", "max_depth = 2"])
    meta["added_threads_key"] = True
    meta["added_max_depth"] = True
else:
    has_threads = False
    has_depth = False
    for i in range(agents_start + 1, agents_end):
        m_threads = re.match(r"^(\s*(?:max_threads|max_concurrent_threads_per_session)\s*=\s*)(\d+)(.*)$", lines[i])
        if m_threads:
            has_threads = True
            if int(m_threads.group(2)) < 6:
                lines[i] = f"{m_threads.group(1)}6{m_threads.group(3)}"
            continue
        m_depth = re.match(r"^(\s*max_depth\s*=\s*)(\d+)(.*)$", lines[i])
        if m_depth:
            has_depth = True
            if int(m_depth.group(2)) < 2:
                lines[i] = f"{m_depth.group(1)}2{m_depth.group(3)}"

    insert_at = agents_start + 1
    if not has_threads:
        lines.insert(insert_at, "max_threads = 6")
        insert_at += 1
        meta["added_threads_key"] = True
    if not has_depth:
        lines.insert(insert_at, "max_depth = 2")
        meta["added_max_depth"] = True

# Merge [features] section: idempotently ensure multi_agent = true and multi_agent_v2 = true
feat_start = -1
feat_end = len(lines)
for i, line in enumerate(lines):
    trim = line.strip()
    if re.match(r"^\[features\]\s*(#.*)?$", trim):
        feat_start = i
        continue
    if feat_start >= 0 and trim.startswith("["):
        feat_end = i
        break

if feat_start < 0:
    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.extend(["[features]", "multi_agent = true", "multi_agent_v2 = true"])
else:
    has_ma = False
    has_ma_v2 = False
    for i in range(feat_start + 1, feat_end):
        m_v2 = re.match(r"^(\s*multi_agent_v2\s*=\s*)([^\s#]+)(.*)$", lines[i])
        if m_v2:
            has_ma_v2 = True
            if m_v2.group(2) != "true":
                lines[i] = f"{m_v2.group(1)}true{m_v2.group(3)}"
            continue
        m_ma = re.match(r"^(\s*multi_agent\s*=\s*)([^\s#]+)(.*)$", lines[i])
        if m_ma:
            has_ma = True
            if m_ma.group(2) != "true":
                lines[i] = f"{m_ma.group(1)}true{m_ma.group(3)}"
    ins_at = feat_start + 1
    if not has_ma:
        lines.insert(ins_at, "multi_agent = true")
        ins_at += 1
    if not has_ma_v2:
        lines.insert(ins_at, "multi_agent_v2 = true")

candidate = (nl.join(lines) + nl) if lines else ""
tomllib.loads(candidate)
out_path.write_text(candidate, encoding="utf-8")
meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
PY

# Validate candidate config with codex --strict-config in isolated stage directory
strict_err="$(CODEX_HOME="$STAGE_DIR" "$CODEX_BIN" --strict-config exec --skip-git-repo-check </dev/null 2>&1 || true)"
if printf '%s' "$strict_err" | grep -q "Error loading config\.toml"; then
  echo "codex --strict-config rejected updated config.toml:" >&2
  echo "$strict_err" >&2
  exit 1
fi

# Create CODEX_HOME, agents/, and backup directories (back up only config.toml + role TOMLs; never copy .venv into backups)
mkdir -p "$CODEX_HOME_DIR" "$CODEX_HOME_DIR/agents"
BACKUPS_ROOT="$CODEX_HOME_DIR/backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUPS_ROOT/tri-orchestrator-$TIMESTAMP"
suffix_idx=1
while [ -e "$BACKUP_DIR" ]; do
  BACKUP_DIR="$(printf '%s/tri-orchestrator-%s-%02d' "$BACKUPS_ROOT" "$TIMESTAMP" "$suffix_idx")"
  suffix_idx=$((suffix_idx + 1))
done
BASELINE_DIR="$BACKUPS_ROOT/tri-orchestrator-baseline"
BASELINE_EXISTS=0
if [ -d "$BASELINE_DIR" ]; then
  BASELINE_EXISTS=1
fi
mkdir -p "$BACKUP_DIR" "$BASELINE_DIR"

if [ "$CONFIG_EXISTED" -eq 1 ]; then
  cp -f "$CONFIG_PATH" "$BACKUP_DIR/config.toml"
  if [ "$BASELINE_EXISTS" -eq 0 ]; then
    cp -f "$CONFIG_PATH" "$BASELINE_DIR/config.toml"
  fi
  echo "[install] Backed up config.toml -> $BACKUP_DIR/config.toml"
fi

BACKED_UP_AGENTS_LIST=""
BASELINE_PREEXISTING_LIST=""
INSTALLED_AGENTS_LIST=""
AGENTS_DST_DIR="$CODEX_HOME_DIR/agents"

for rf in $ROLE_FILES; do
  src_file="$AGENTS_SRC_DIR/$rf"
  dst_file="$AGENTS_DST_DIR/$rf"
  if [ -f "$dst_file" ]; then
    mkdir -p "$BACKUP_DIR/agents"
    cp -f "$dst_file" "$BACKUP_DIR/agents/$rf"
    BACKED_UP_AGENTS_LIST="${BACKED_UP_AGENTS_LIST}${BACKED_UP_AGENTS_LIST:+,}$rf"
    if [ "$BASELINE_EXISTS" -eq 0 ]; then
      mkdir -p "$BASELINE_DIR/agents"
      cp -f "$dst_file" "$BASELINE_DIR/agents/$rf"
      BASELINE_PREEXISTING_LIST="${BASELINE_PREEXISTING_LIST}${BASELINE_PREEXISTING_LIST:+,}$rf"
    fi
  fi
  cp -f "$src_file" "$dst_file"
  INSTALLED_AGENTS_LIST="${INSTALLED_AGENTS_LIST}${INSTALLED_AGENTS_LIST:+,}$rf"
done

if [ "$BASELINE_EXISTS" -eq 0 ]; then
  run_py - "$BASELINE_DIR/baseline.json" "$TIMESTAMP" "$CONFIG_EXISTED" "$BASELINE_PREEXISTING_LIST" <<'PY'
import json, pathlib, sys
obj = {
    "created_at": sys.argv[2],
    "config_existed": sys.argv[3] == "1",
    "preexisting_agents": [x for x in sys.argv[4].split(",") if x],
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
PY
fi
echo "[install] Installed 6 agent role TOMLs into $AGENTS_DST_DIR"

# Atomically install merged config.toml
tmp_cfg="$CONFIG_PATH.tmp.$$"
cp -f "$CANDIDATE_CONFIG" "$tmp_cfg"
mv -f "$tmp_cfg" "$CONFIG_PATH"
echo "[install] Updated [agents] in $CONFIG_PATH (strict-config verified)"

run_py - "$BACKUP_DIR/manifest.json" "$TIMESTAMP" "$REPO_ROOT" "$PLUGIN_DIR" "$CODEX_HOME_DIR" "$CONFIG_EXISTED" "$BACKED_UP_AGENTS_LIST" "$INSTALLED_AGENTS_LIST" "$AGENTS_META_JSON" <<'PY'
import json, pathlib, sys
obj = {
    "installed_at": sys.argv[2],
    "repo_root": sys.argv[3],
    "plugin_dir": sys.argv[4],
    "codex_home": sys.argv[5],
    "config_existed": sys.argv[6] == "1",
    "backed_up_agents": [x for x in sys.argv[7].split(",") if x],
    "installed_agents": [x for x in sys.argv[8].split(",") if x],
    "agents_meta": json.loads(pathlib.Path(sys.argv[9]).read_text(encoding="utf-8")),
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
PY

run_py - "$BACKUPS_ROOT" "$KEEP_BACKUPS" <<'PY'
import os, pathlib, stat, sys

backups_root = pathlib.Path(sys.argv[1])
keep_count = max(0, int(sys.argv[2]))

def is_reparse_or_symlink(p: pathlib.Path) -> bool:
    try:
        if p.is_symlink():
            return True
        if hasattr(p, "is_junction") and p.is_junction():
            return True
        st = os.lstat(p)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return False

def rmtree_safe(root: pathlib.Path) -> None:
    if is_reparse_or_symlink(root):
        try:
            os.rmdir(root)
        except OSError:
            root.unlink(missing_ok=True)
        return
    with os.scandir(root) as it:
        for entry in it:
            ep = pathlib.Path(entry.path)
            if is_reparse_or_symlink(ep):
                try:
                    os.rmdir(ep)
                except OSError:
                    ep.unlink(missing_ok=True)
            elif entry.is_dir(follow_symlinks=False):
                rmtree_safe(ep)
            else:
                try:
                    ep.unlink()
                except PermissionError:
                    os.chmod(ep, stat.S_IWRITE | stat.S_IREAD)
                    ep.unlink()
    try:
        os.rmdir(root)
    except PermissionError:
        os.chmod(root, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        os.rmdir(root)

if not backups_root.is_dir():
    sys.exit(0)

candidates = []
for entry in backups_root.iterdir():
    name = entry.name
    if name == "tri-orchestrator-baseline":
        continue
    if not name.startswith("tri-orchestrator-2"):
        continue
    if is_reparse_or_symlink(entry):
        continue
    if entry.is_dir():
        candidates.append(entry)

candidates.sort(key=lambda p: p.name, reverse=True)
pruned = []
for old_dir in candidates[keep_count:]:
    rmtree_safe(old_dir)
    pruned.append(old_dir.name)

if pruned:
    print(f"[install] Pruned {len(pruned)} old backup(s) under {backups_root} (kept baseline + latest {keep_count}): {', '.join(pruned)}")
PY

CACHED_PLUGIN_DIR=""

if [ "$SKIP_PLUGIN_INSTALL" -eq 0 ]; then
  codex_json="$STAGE_DIR/codex.json"
  if [ "$HAS_MARKETPLACE_LAYOUT" -eq 1 ]; then
    CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin marketplace list --json > "$codex_json"
    mp_action="$(run_py - "$codex_json" "$REPO_ROOT" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
repo = str(pathlib.Path(sys.argv[2]).resolve())
for mp in data.get("marketplaces", []):
    if mp.get("name") == "tri-orchestrator":
        root = mp.get("root") or ""
        norm = str(pathlib.Path(root).resolve()) if root else ""
        print("keep" if norm == repo else "repoint")
        sys.exit(0)
print("add")
PY
)"
    if [ "$mp_action" = "repoint" ]; then
      echo "[install] Re-pointing marketplace 'tri-orchestrator' -> $REPO_ROOT"
      CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin marketplace remove tri-orchestrator --json >/dev/null
      CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin marketplace add "$REPO_ROOT" --json >/dev/null
    elif [ "$mp_action" = "add" ]; then
      CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin marketplace add "$REPO_ROOT" --json >/dev/null
      echo "[install] Registered marketplace 'tri-orchestrator'"
    else
      echo "[install] Marketplace 'tri-orchestrator' already points to $REPO_ROOT"
    fi

  fi

  CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin list --marketplace tri-orchestrator --json > "$codex_json"
  is_installed="$(run_py - "$codex_json" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for item in data.get("installed", []):
    if item.get("pluginId") == "tri-orchestrator@tri-orchestrator" and item.get("installed"):
        print("yes")
        sys.exit(0)
print("no")
PY
)"
  if [ "$HAS_MARKETPLACE_LAYOUT" -eq 1 ] && [ "$is_installed" = "yes" ]; then
    echo "[install] Removing existing plugin cache before fresh install..."
    CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin remove tri-orchestrator@tri-orchestrator --json >/dev/null
  fi
  if [ "$HAS_MARKETPLACE_LAYOUT" -eq 1 ] || [ "$is_installed" != "yes" ]; then
    CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin add tri-orchestrator@tri-orchestrator --json >/dev/null
  fi

  CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin list --marketplace tri-orchestrator --json > "$codex_json"
  PLUGIN_VERSION="$(run_py - "$codex_json" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for item in data.get("installed", []):
    if item.get("pluginId") == "tri-orchestrator@tri-orchestrator" and item.get("installed") and item.get("enabled"):
        print(item["version"])
        sys.exit(0)
raise SystemExit("tri-orchestrator@tri-orchestrator not reported installed+enabled")
PY
)"

  CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" mcp list --json > "$codex_json"
  CACHED_PLUGIN_DIR="$(run_py - "$codex_json" <<'PY'
import json, pathlib, sys
items = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for item in items:
    if item.get("name") == "tri_orchestrator" and item.get("enabled"):
        tr = item.get("transport") or {}
        env = tr.get("env") or {}
        print(env.get("PLUGIN_ROOT") or tr.get("cwd") or "")
        sys.exit(0)
raise SystemExit("MCP server 'tri_orchestrator' not reported enabled in codex mcp list")
PY
)"
  echo "[install] Verified plugin: tri-orchestrator@tri-orchestrator (v$PLUGIN_VERSION, installed=True, enabled=True)"
  echo "[install] Verified MCP   : tri_orchestrator (enabled=True, cached=$CACHED_PLUGIN_DIR)"
fi

if [ -z "$CACHED_PLUGIN_DIR" ]; then
  expected_cache="$CODEX_HOME_DIR/plugins/cache/tri-orchestrator/tri-orchestrator/$PLUGIN_VERSION"
  if [ -d "$expected_cache" ]; then
    CACHED_PLUGIN_DIR="$expected_cache"
  elif [ "$HAS_MARKETPLACE_LAYOUT" -eq 0 ]; then
    CACHED_PLUGIN_DIR="$PLUGIN_DIR"
  fi
fi

if [ "$SKIP_UV_SYNC" -eq 0 ]; then
  if [ -z "$CACHED_PLUGIN_DIR" ] || [ ! -f "$CACHED_PLUGIN_DIR/pyproject.toml" ]; then
    if [ "$SKIP_PLUGIN_INSTALL" -eq 1 ]; then
      echo "[install] Cached plugin directory not present (--skip-plugin-install); skipping cache uv sync."
    else
      echo "Cached plugin directory missing pyproject.toml: '$CACHED_PLUGIN_DIR'" >&2
      exit 1
    fi
  else
    CACHED_PLUGIN_DIR="$(abs_dir "$CACHED_PLUGIN_DIR")"
    # Remove stale .venv if copied from source repo with .pth pointing outside cache
    stale_venv="$(run_py - "$CACHED_PLUGIN_DIR" <<'PY'
import pathlib, sys
plugin_dir = pathlib.Path(sys.argv[1]).resolve()
venv_dir = plugin_dir / ".venv"
if not venv_dir.is_dir():
    print("no")
    sys.exit(0)
for pth in venv_dir.rglob("*.pth"):
    for line in pth.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("import "):
            continue
        try:
            target = pathlib.Path(s).resolve()
            target.relative_to(plugin_dir)
        except Exception:
            print("yes")
            sys.exit(0)
print("no")
PY
)"
    if [ -d "$CACHED_PLUGIN_DIR/.venv" ] && { [ "$HAS_MARKETPLACE_LAYOUT" -eq 1 ] || [ "$stale_venv" = "yes" ]; }; then
      echo "[install] Removing stale copied .venv inside $CACHED_PLUGIN_DIR..."
      rm -rf "$CACHED_PLUGIN_DIR/.venv"
    fi

    echo "[install] Pre-warming cached plugin runtime: uv sync --project $CACHED_PLUGIN_DIR --no-dev"
    (cd "${TMPDIR:-/tmp}" && env -u VIRTUAL_ENV -u PYTHONPATH "$UV_BIN" sync --project "$CACHED_PLUGIN_DIR" --no-dev)
    if [ ! -x "$CACHED_PLUGIN_DIR/.venv/bin/python" ]; then
      echo "Expected cached plugin python missing at $CACHED_PLUGIN_DIR/.venv/bin/python" >&2
      exit 1
    fi
    echo "[install] Pre-warmed cached runtime -> $CACHED_PLUGIN_DIR/.venv"
  fi
fi

# hooks/list self-check and optional --trust-plugin-hooks
probe_hooks_list_json() {
  local probe_cwd="$1"
  run_py - "$CODEX_BIN" "$CODEX_HOME_DIR" "$probe_cwd" "$PLUGIN_VERSION" <<'PY'
import json, os, queue, subprocess, sys, threading, time

codex_exe, codex_home, probe_cwd, plugin_version = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
env = os.environ.copy()
env["CODEX_HOME"] = codex_home
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.Popen(
    [codex_exe, "app-server", "--stdio"],
    cwd=probe_cwd,
    env=env,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    bufsize=1,
)

stdout_q = queue.Queue()
stderr_chunks = []

def _read_out():
    try:
        for line in proc.stdout:
            stdout_q.put(line)
    finally:
        stdout_q.put(None)

def _read_err():
    try:
        for line in proc.stderr:
            stderr_chunks.append(line)
    except Exception:
        pass

t_out = threading.Thread(target=_read_out, daemon=True)
t_err = threading.Thread(target=_read_err, daemon=True)
t_out.start()
t_err.start()

def _send(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()

hooks_resp = None
try:
    _send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "tri-orchestrator-installer-sh", "version": plugin_version},
            "capabilities": {"experimentalApi": True},
        },
    })
    sent_list = False
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            line = stdout_q.get(timeout=0.4)
        except queue.Empty:
            if proc.poll() is not None and stdout_q.empty():
                break
            continue
        if line is None:
            break
        raw = line.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            continue
        msg_id = msg.get("id")
        if msg_id == 1 and not sent_list:
            if "error" in msg:
                raise RuntimeError(f"app-server initialize error: {msg['error']}")
            _send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            _send({"jsonrpc": "2.0", "id": 2, "method": "hooks/list", "params": {}})
            sent_list = True
        elif msg_id == 2:
            if "error" in msg:
                raise RuntimeError(f"app-server hooks/list error: {msg['error']}")
            hooks_resp = msg.get("result") or {}
            break
finally:
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
    t_out.join(timeout=1.0)
    t_err.join(timeout=1.0)

if hooks_resp is None:
    err_tail = "".join(stderr_chunks[-20:]).strip()
    raise RuntimeError(f"Timed out or exited before receiving hooks/list response. stderr={err_tail}")

all_hooks = []
if isinstance(hooks_resp.get("data"), list):
    for bucket in hooks_resp["data"]:
        if isinstance(bucket, dict) and isinstance(bucket.get("hooks"), list):
            all_hooks.extend(bucket["hooks"])
elif isinstance(hooks_resp.get("hooks"), list):
    all_hooks.extend(hooks_resp["hooks"])

plugin_hooks = {}
for h in all_hooks:
    if isinstance(h, dict) and h.get("pluginId") == "tri-orchestrator@tri-orchestrator":
        k = h.get("key") or f"{h.get('eventName')}:{len(plugin_hooks)}"
        plugin_hooks[k] = h

print(json.dumps({"hooks": list(plugin_hooks.values())}, ensure_ascii=False))
PY
}

if [ "$SKIP_PLUGIN_INSTALL" -eq 0 ] || [ "$TRUST_PLUGIN_HOOKS" -eq 1 ]; then
  if [ -n "$CODEX_BIN" ] && [ -x "$CODEX_BIN" ]; then
    probe_cwd="${REPO_ROOT:-${TMPDIR:-/tmp}}"
    hooks_json="$STAGE_DIR/hooks.json"
    probe_hooks_list_json "$probe_cwd" > "$hooks_json"
    hooks_status="$(run_py - "$hooks_json" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
hooks = data.get("hooks", [])
events = {h.get("eventName") for h in hooks if isinstance(h, dict)}
required = {"sessionStart", "postCompact", "userPromptSubmit", "stop"}
missing = required - events
if missing:
    print(f"MISSING:{','.join(sorted(missing))}")
    sys.exit(0)
untrusted = [h for h in hooks if isinstance(h, dict) and h.get("trustStatus") != "trusted"]
print(f"OK:{len(hooks)}:{len(untrusted)}")
PY
)"
    if printf '%s' "$hooks_status" | grep -q '^MISSING:'; then
      echo "Plugin hooks/list verification failed ($hooks_status) for 'tri-orchestrator@tri-orchestrator'." >&2
      exit 1
    fi

    if [ "$TRUST_PLUGIN_HOOKS" -eq 1 ]; then
      tmp_trusted="$STAGE_DIR/trusted_config.toml"
      run_py - "$hooks_json" "$CONFIG_PATH" "$tmp_trusted" <<'PY'
import json, pathlib, re, sys, tomllib

hooks_data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
hooks = hooks_data.get("hooks", [])
config_path = pathlib.Path(sys.argv[2])
out_path = pathlib.Path(sys.argv[3])
toml_text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""

entries = []
for h in sorted(hooks, key=lambda item: (item.get("displayOrder", 999), item.get("key", ""))):
    key = h.get("key")
    cur_hash = h.get("currentHash") or ""
    if not key or not isinstance(key, str):
        raise ValueError(f"Hook missing valid key: {h}")
    parts = cur_hash.split("/", 1)
    if len(parts) != 2 or not parts[0].startswith("sha256:") or not parts[1].startswith("sha256:"):
        raise ValueError(f"Unexpected composite currentHash for {key}: {cur_hash!r}")
    cmd_hash, content_hash = parts[0], parts[1]
    line = f'{json.dumps(key)} = {{ enabled = true, trusted_hash = {json.dumps(cmd_hash)}, trusted_content_hash = {json.dumps(content_hash)} }}'
    entries.append((key, cmd_hash, content_hash, line))

nl = "\r\n" if "\r\n" in toml_text else "\n"
lines = toml_text.splitlines() if toml_text else []

cleaned = []
skip_sub = False
for ln in lines:
    if re.match(r'^\s*\[\s*hooks\.state\s*\.\s*(["\'])tri-orchestrator@tri-orchestrator:[^"\']*\1\s*\]\s*(?:#.*)?$', ln):
        skip_sub = True
        continue
    if skip_sub and re.match(r'^\s*\[', ln):
        skip_sub = False
    if not skip_sub:
        cleaned.append(ln)
lines = cleaned

sec_start = -1
sec_end = len(lines)
for idx, ln in enumerate(lines):
    if re.match(r'^\s*\[\s*hooks\.state\s*\]\s*(?:#.*)?$', ln):
        sec_start = idx
        continue
    if sec_start >= 0 and re.match(r'^\s*\[', ln):
        sec_end = idx
        break

tri_key_re = re.compile(r'^\s*(["\'])tri-orchestrator@tri-orchestrator:[^"\']*\1\s*=')
if sec_start < 0:
    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.append("[hooks.state]")
    for _, _, _, entry_line in entries:
        lines.append(entry_line)
else:
    kept = []
    i = sec_start + 1
    while i < sec_end:
        ln = lines[i]
        if tri_key_re.match(ln):
            rhs = ln.split("=", 1)[1]
            depth = rhs.count("{") - rhs.count("}")
            i += 1
            while depth > 0 and i < sec_end:
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
            continue
        kept.append(ln)
        i += 1
    had_trailing_blank = bool(kept and kept[-1].strip() == "")
    while kept and kept[-1].strip() == "":
        kept.pop()
    for _, _, _, entry_line in entries:
        kept.append(entry_line)
    if sec_end < len(lines) and (had_trailing_blank or lines[sec_end].strip() != ""):
        kept.append("")
    lines = lines[:sec_start + 1] + kept + lines[sec_end:]

updated = (nl.join(lines) + nl) if lines else ""
parsed = tomllib.loads(updated)
state_tbl = parsed.get("hooks", {}).get("state", {})
for key, cmd_hash, content_hash, _ in entries:
    actual = state_tbl.get(key)
    expected = {"enabled": True, "trusted_hash": cmd_hash, "trusted_content_hash": content_hash}
    if actual != expected:
        raise AssertionError(f"TOML roundtrip mismatch for {key}: {actual} != {expected}")
out_path.write_text(updated, encoding="utf-8")
PY
      hooks_stage_dir="$STAGE_DIR/hooks-strict"
      mkdir -p "$hooks_stage_dir"
      cp -f "$tmp_trusted" "$hooks_stage_dir/config.toml"
      strict_hooks_out="$(CODEX_HOME="$hooks_stage_dir" "$CODEX_BIN" app-server --strict-config --print-startup-config 2>&1)" || {
        echo "codex --strict-config rejected [hooks.state] in config.toml:" >&2
        echo "$strict_hooks_out" >&2
        exit 1
      }
      if printf '%s' "$strict_hooks_out" | grep -q "Error loading config\.toml"; then
        echo "codex --strict-config rejected [hooks.state] in config.toml:" >&2
        echo "$strict_hooks_out" >&2
        exit 1
      fi
      tmp_dest="$CONFIG_PATH.tmp.$$"
      cp -f "$tmp_trusted" "$tmp_dest"
      mv -f "$tmp_dest" "$CONFIG_PATH"

      probe_hooks_list_json "$probe_cwd" > "$hooks_json"
      run_py - "$hooks_json" <<'PY'
import json, pathlib, sys
hooks = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")).get("hooks", [])
still_untrusted = [h for h in hooks if isinstance(h, dict) and h.get("trustStatus") != "trusted"]
if len(hooks) < 4 or still_untrusted:
    raise SystemExit(f"--trust-plugin-hooks failed to transition all 4 hooks to trustStatus='trusted': {hooks}")
PY
      echo "[install] Trusted all 4 plugin hooks in [hooks.state] ($CONFIG_PATH) and verified trustStatus=trusted via hooks/list."
    else
      untrusted_n="${hooks_status##*:}"
      if [ "$untrusted_n" != "0" ]; then
        echo "[install] Verified 4 plugin hooks via hooks/list (trustStatus=untrusted; [hooks.state] left untouched by default)."
        echo "[提示] 已发现 tri-orchestrator 的全部 4 个生命周期钩子（sessionStart / postCompact / userPromptSubmit / stop），当前默认处于未信任（untrusted）状态。"
        echo "[提示] 打开 Codex Desktop 后，请在弹出的插件钩子信任提示框中点击「信任 / Trust」（或使用 scripts/install.sh --trust-plugin-hooks 重新安装），即可激活 Stop 防提前退出门禁与 PostCompact 上下文自动恢复。"
      else
        echo "[install] Verified 4 plugin hooks via hooks/list (all trustStatus=trusted)."
      fi
    fi
  else
    echo "[install] codex CLI not found; skipping hooks/list self-check."
  fi
fi

echo "[install] Completed successfully."
