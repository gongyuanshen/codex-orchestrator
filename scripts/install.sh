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
  -h, --help                Show this help message
EOF
}

REPO_ROOT=""
PLUGIN_DIR=""
CODEX_HOME_ARG=""
SKIP_UV_SYNC=0
SKIP_PLUGIN_INSTALL=0

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
if [ ! -f "$PLUGIN_DIR/plugin.json" ]; then
  echo "Missing plugin.json at $PLUGIN_DIR/plugin.json" >&2
  exit 1
fi

echo "[install] PluginDir : $PLUGIN_DIR"
if [ -n "$REPO_ROOT" ]; then
  echo "[install] RepoRoot  : $REPO_ROOT"
fi
echo "[install] CodexHome : $CODEX_HOME_DIR"
echo "[install] Using uv  : $UV_BIN"

PLUGIN_VERSION="$(run_py - "$PLUGIN_DIR/plugin.json" <<'PY'
import json, pathlib, sys
data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
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

# Safely merge [agents] in config.toml and validate with tomllib
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

# Create CODEX_HOME, agents/, and backup directories
mkdir -p "$CODEX_HOME_DIR" "$CODEX_HOME_DIR/agents"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$CODEX_HOME_DIR/backups/tri-orchestrator-$TIMESTAMP"
BASELINE_DIR="$CODEX_HOME_DIR/backups/tri-orchestrator-baseline"
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

CACHED_PLUGIN_DIR=""

if [ "$SKIP_PLUGIN_INSTALL" -eq 0 ]; then
  if [ "$HAS_MARKETPLACE_LAYOUT" -eq 1 ]; then
    mp_list_json="$(CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin marketplace list --json)"
    mp_action="$(printf '%s' "$mp_list_json" | run_py - "$REPO_ROOT" <<'PY'
import json, pathlib, sys
data = json.load(sys.stdin)
repo = str(pathlib.Path(sys.argv[1]).resolve())
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
      CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin marketplace remove tri-orchestrator --json >/dev/null || true
      CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin marketplace add "$REPO_ROOT" --json >/dev/null
    elif [ "$mp_action" = "add" ]; then
      CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin marketplace add "$REPO_ROOT" --json >/dev/null
      echo "[install] Registered marketplace 'tri-orchestrator'"
    else
      echo "[install] Marketplace 'tri-orchestrator' already points to $REPO_ROOT"
    fi

    pl_list_json="$(CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin list --marketplace tri-orchestrator --json || true)"
    is_installed="$(printf '%s' "$pl_list_json" | run_py - <<'PY'
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    print("no")
    sys.exit(0)
data = json.loads(raw)
for item in data.get("installed", []):
    if item.get("pluginId") == "tri-orchestrator@tri-orchestrator":
        print("yes")
        sys.exit(0)
print("no")
PY
)"
    if [ "$is_installed" = "yes" ]; then
      echo "[install] Removing existing plugin cache before fresh install..."
      CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin remove tri-orchestrator@tri-orchestrator --json >/dev/null
    fi
    CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin add tri-orchestrator@tri-orchestrator --json >/dev/null
  else
    pl_list_json="$(CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin list --marketplace tri-orchestrator --json || true)"
    is_installed="$(printf '%s' "$pl_list_json" | run_py - <<'PY'
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    print("no")
    sys.exit(0)
data = json.loads(raw)
for item in data.get("installed", []):
    if item.get("pluginId") == "tri-orchestrator@tri-orchestrator" and item.get("installed"):
        print("yes")
        sys.exit(0)
print("no")
PY
)"
    if [ "$is_installed" != "yes" ]; then
      CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin add tri-orchestrator@tri-orchestrator --json >/dev/null
    fi
  fi

  verify_pl_json="$(CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" plugin list --marketplace tri-orchestrator --json)"
  PLUGIN_VERSION="$(printf '%s' "$verify_pl_json" | run_py - <<'PY'
import json, sys
data = json.load(sys.stdin)
for item in data.get("installed", []):
    if item.get("pluginId") == "tri-orchestrator@tri-orchestrator" and item.get("installed") and item.get("enabled"):
        print(item.get("version") or "0.1.0")
        sys.exit(0)
raise SystemExit("tri-orchestrator@tri-orchestrator not reported installed+enabled")
PY
)"

  verify_mcp_json="$(CODEX_HOME="$CODEX_HOME_DIR" "$CODEX_BIN" mcp list --json)"
  CACHED_PLUGIN_DIR="$(printf '%s' "$verify_mcp_json" | run_py - <<'PY'
import json, sys
items = json.load(sys.stdin)
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

echo "[install] Completed successfully."
