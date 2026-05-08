"""Launcher: ensure llama-server is running, reindex, register MCP, launch client.

Fail-first. No JSON config fallback. No silent retries.
Pure Python — no PowerShell dependency.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_LLAMA_SERVER = "llama-server"
DEFAULT_EMBED_GGUF = ""
DEFAULT_RERANK_GGUF = ""
DEFAULT_EMBED_PORT = 8887
DEFAULT_RERANK_PORT = 8888
EMBED_MODEL_NAME = "bge-base-zh"
STARTUP_TIMEOUT_SECONDS = 60
PROBE_INTERVAL_SECONDS = 1.0
PROBE_REQUEST_TIMEOUT_SECONDS = 5.0
PRIVATE_CONFIG_BASENAME = ".local-search.env"
PRIVATE_CONFIG_HOME = Path.home() / ".local-mcp-search.env"


# ── Endpoint probe ────────────────────────────────────────────────────────────

@dataclass
class Endpoint:
    label: str
    port: int
    extra_flag: str
    gguf: str
    probe_path: str
    probe_body: dict


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _load_private_launcher_config(workspace: str) -> dict[str, str]:
    config: dict[str, str] = {}
    for path in (PRIVATE_CONFIG_HOME, Path(workspace) / PRIVATE_CONFIG_BASENAME):
        config.update(_parse_env_file(path))
    return config


def _resolve_launcher_setting(
    cli_value: str | None,
    env_key: str,
    config: dict[str, str],
    default: str,
) -> str:
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get(env_key)
    if env_value:
        return env_value
    config_value = config.get(env_key)
    if config_value:
        return config_value
    return default


def _probe(endpoint: Endpoint) -> bool:
    """Send a real functional POST. Return True iff HTTP 200."""
    url = f"http://127.0.0.1:{endpoint.port}{endpoint.probe_path}"
    data = json.dumps(endpoint.probe_body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=PROBE_REQUEST_TIMEOUT_SECONDS) as resp:
            return resp.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError):
        return False


def _port_in_use(port: int) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


# ── llama-server lifecycle ────────────────────────────────────────────────────

def _spawn_llama(binary: str, endpoint: Endpoint, log_dir: Path) -> subprocess.Popen:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"llama-{endpoint.label}-{os.getpid()}.log"
    args = [binary, "-m", endpoint.gguf, endpoint.extra_flag,
            "--port", str(endpoint.port), "--host", "127.0.0.1"]
    print(f"[llama] starting {endpoint.label} on port {endpoint.port}")
    print(f"[llama] log: {log_path}")
    log_fp = open(log_path, "w", encoding="utf-8", errors="replace")
    return subprocess.Popen(
        args, stdout=log_fp, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def _wait_ready(endpoint: Endpoint, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        elapsed = time.monotonic() - (deadline - STARTUP_TIMEOUT_SECONDS)
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server ({endpoint.label}) exited prematurely (code {proc.returncode})")
        if _probe(endpoint):
            print(f"[llama] {endpoint.label} ready on port {endpoint.port} (PID {proc.pid}, attempt {attempt}, {elapsed:.0f}s)")
            return
        print(f"[llama] waiting for {endpoint.label} on port {endpoint.port} (attempt {attempt}, {elapsed:.0f}s)")
        time.sleep(PROBE_INTERVAL_SECONDS)
    raise RuntimeError(f"llama-server ({endpoint.label}) did not become ready within {STARTUP_TIMEOUT_SECONDS}s")


def _port_owner_pids(port: int) -> list[int]:
    """Return PIDs listening on the given port. Report only — NEVER kill."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "TCP"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[3] != "LISTENING":
            continue
        if parts[1].endswith(f":{port}"):
            try:
                pids.append(int(parts[4]))
            except ValueError:
                pass
    return pids


def _ensure_running(endpoint: Endpoint, binary: str, log_dir: Path) -> subprocess.Popen | None:
    """Return spawned process, or None if we reused an existing healthy service.

    NEVER kills foreign processes — only processes started by this launcher.
    """
    if _probe(endpoint):
        print(f"[llama] {endpoint.label} already healthy on port {endpoint.port}, reusing")
        return None
    if _port_in_use(endpoint.port):
        owners = _port_owner_pids(endpoint.port)
        raise RuntimeError(
            f"Port {endpoint.port} ({endpoint.label}) is in use by PID(s) {owners} "
            f"but the endpoint is not responding correctly. "
            f"Please stop the conflicting process manually, then retry."
        )
    if not Path(binary).is_file():
        raise FileNotFoundError(f"llama-server binary not found: {binary}")
    if not Path(endpoint.gguf).is_file():
        raise FileNotFoundError(f"{endpoint.label} GGUF not found: {endpoint.gguf}")
    proc = _spawn_llama(binary, endpoint, log_dir)
    try:
        _wait_ready(endpoint, proc)
    except Exception:
        if proc.poll() is None:
            proc.terminate()
        raise
    return proc


def _terminate(proc: subprocess.Popen, label: str) -> None:
    if proc.poll() is not None:
        return
    print(f"[llama] stopping {label} (PID {proc.pid})")
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── MCP registration ─────────────────────────────────────────────────────────

def _find_command(name: str) -> str | None:
    return shutil.which(name)


def register_codex_mcp(server_name: str, server_args: list[str], workspace: str) -> None:
    codex = _find_command("codex")
    if not codex:
        print("[mcp] codex not found in PATH, skipping Codex MCP registration")
        return
    # Remove old config (ignore errors)
    subprocess.run([codex, "mcp", "remove", server_name],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd = [codex, "mcp", "add", server_name, "--"] + server_args
    print(f"[mcp] registering Codex MCP server: {server_name}")
    result = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[mcp] codex mcp add failed: {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"[mcp] Codex MCP registered")
        _verify_codex_mcp_target(codex, server_name, server_args)


def register_claude_mcp(server_name: str, server_args: list[str], workspace: str) -> None:
    claude = _find_command("claude")
    if not claude:
        print("[mcp] claude not found in PATH, skipping Claude MCP registration")
        return
    subprocess.run([claude, "mcp", "remove", server_name],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd = [claude, "mcp", "add", server_name] + server_args
    print(f"[mcp] registering Claude MCP server: {server_name}")
    result = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[mcp] claude mcp add failed: {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"[mcp] Claude MCP registered")


def write_claude_project_mcp_config(workspace: str, server_name: str, server_args: list[str]) -> None:
    """Write .mcp.json for Claude Code project-level MCP config."""
    config = {
        "mcpServers": {
            server_name: {
                "command": server_args[0] if server_args else "python",
                "args": server_args[1:] if len(server_args) > 1 else ["-m", "local_mcp_search"],
            }
        }
    }
    path = Path(workspace) / ".mcp.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"[mcp] wrote Claude project MCP config: {path}")


def _verify_codex_mcp_target(codex: str, server_name: str, server_args: list[str]) -> None:
    expected = [str(Path(arg).resolve()) if Path(arg).exists() else arg for arg in server_args]
    result = subprocess.run(
        [codex, "mcp", "get", server_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"[mcp] warning: failed to verify Codex MCP target for {server_name}", file=sys.stderr)
        return

    stdout = result.stdout.lower()
    mismatched = [
        arg for arg in expected
        if arg.lower() not in stdout
    ]
    if mismatched:
        print(
            "[mcp] warning: Codex MCP target does not match current workspace wrapper. "
            f"Expected args to include: {expected}. "
            "This usually means another project-level MCP config is overriding the global registration.",
            file=sys.stderr,
        )


# ── Session lookup ────────────────────────────────────────────────────────────

def _normalize_workspace_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


def _resolve_workspace_root(path: str) -> str:
    p = Path(path).resolve()
    try:
        git_root = subprocess.check_output(
            ["git", "-C", str(p), "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return _normalize_workspace_path(git_root)
    except Exception:
        return _normalize_workspace_path(str(p))


def _get_latest_codex_session(workspace: str) -> dict | None:
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.is_dir():
        return None
    normalized_workspace = _normalize_workspace_path(workspace)
    candidates = sorted(sessions_dir.rglob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in candidates:
        try:
            first_line = f.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
            entry = json.loads(first_line)
        except Exception:
            continue
        if entry.get("type") != "session_meta":
            continue
        session_workspace = entry.get("payload", {}).get("cwd")
        if not session_workspace or _normalize_workspace_path(session_workspace) != normalized_workspace:
            continue
        return {"id": entry["payload"]["id"], "path": str(f)}
    return None


def _get_latest_claude_session(workspace: str) -> dict | None:
    history_path = Path.home() / ".claude" / "history.jsonl"
    if not history_path.is_file():
        return None
    normalized_workspace = _normalize_workspace_path(workspace)
    best = None
    best_ts = -1
    for line in history_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        project = entry.get("project")
        if not project or _normalize_workspace_path(project) != normalized_workspace or not entry.get("sessionId"):
            continue
        ts = int(entry.get("timestamp", 0))
        if ts > best_ts:
            best_ts = ts
            best = {"id": entry["sessionId"], "display": entry.get("display", "")}
    return best


def _get_recent_codex_sessions(workspace: str, max_count: int = 10) -> list[dict]:
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.is_dir():
        return []
    normalized_workspace = _normalize_workspace_path(workspace)
    results = []
    for f in sorted(sessions_dir.rglob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            first_line = f.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
            entry = json.loads(first_line)
        except Exception:
            continue
        session_workspace = entry.get("payload", {}).get("cwd")
        if (
            entry.get("type") != "session_meta"
            or not session_workspace
            or _normalize_workspace_path(session_workspace) != normalized_workspace
        ):
            continue
        results.append({"id": entry["payload"]["id"], "path": str(f),
                        "mtime": f.stat().st_mtime})
        if len(results) >= max_count:
            break
    return results


def _get_recent_claude_sessions(workspace: str, max_count: int = 10) -> list[dict]:
    history_path = Path.home() / ".claude" / "history.jsonl"
    if not history_path.is_file():
        return []
    normalized_workspace = _normalize_workspace_path(workspace)
    by_id: dict[str, dict] = {}
    for line in history_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        project = entry.get("project")
        if not project or _normalize_workspace_path(project) != normalized_workspace or not entry.get("sessionId"):
            continue
        sid = entry["sessionId"]
        ts = int(entry.get("timestamp", 0))
        existing = by_id.get(sid)
        if not existing or ts > existing["timestamp"]:
            by_id[sid] = {"id": sid, "timestamp": ts, "display": entry.get("display", "")}
    return sorted(by_id.values(), key=lambda s: s["timestamp"], reverse=True)[:max_count]


def _select_session_interactive(sessions: list[dict], client_name: str) -> dict | None:
    if not sessions:
        return None
    print(f"\n{client_name} sessions:")
    for i, s in enumerate(sessions):
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.get("timestamp", s.get("mtime", 0))))
        display = s.get("display", "")
        suffix = f" | {display}" if display else ""
        print(f"  [{i+1}] {s['id']}  {ts}{suffix}")
    try:
        raw = input(f"Select {client_name} session number (Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    try:
        idx = int(raw)
        if 1 <= idx <= len(sessions):
            return sessions[idx - 1]
    except ValueError:
        pass
    print(f"Invalid selection: {raw}")
    return None


# ── Client launch ─────────────────────────────────────────────────────────────

def _launch_codex(workspace: str, fresh: bool, pick: bool, fork: bool) -> int:
    codex = _find_command("codex")
    if not codex:
        print("[client] codex not found in PATH", file=sys.stderr)
        return 1
    session = None
    if not fresh:
        if pick:
            session = _select_session_interactive(
                _get_recent_codex_sessions(workspace), "Codex")
        else:
            session = _get_latest_codex_session(workspace)
    if session:
        if fork:
            print(f"[client] forking Codex session: {session['id']}")
            return subprocess.call([codex, "fork", session["id"]])
        print(f"[client] resuming Codex session: {session['id']}")
        return subprocess.call([codex, "resume", session["id"]])
    print(f"[client] launching fresh Codex session at: {workspace}")
    return subprocess.call([codex, "-C", workspace])


def _launch_claude(workspace: str, fresh: bool, pick: bool, fork: bool) -> int:
    claude = _find_command("claude")
    if not claude:
        print("[client] claude not found in PATH", file=sys.stderr)
        return 1
    session = None
    if not fresh:
        if pick:
            session = _select_session_interactive(
                _get_recent_claude_sessions(workspace), "Claude")
        else:
            session = _get_latest_claude_session(workspace)
    if session:
        cmd = [claude, "--resume", session["id"]]
        if fork:
            cmd.append("--fork-session")
        print(f"[client] resuming Claude session: {session['id']}")
        return subprocess.call(cmd, cwd=workspace)
    print(f"[client] launching fresh Claude session at: {workspace}")
    return subprocess.call([claude], cwd=workspace)


# ── Main ─────────────────────────────────────────────────────────────────────

def _normalize_argv(argv: list[str]) -> list[str]:
    """Convert old cpx.ps1 style single-dash flags to argparse long options."""

    # Explicit mapping: old flag → canonical form
    MAP: dict[str, str | None] = {
        "-claude": "--client,claude",
        "-Claude": "--client,claude",
        "-codex": "--client,codex",
        "-Codex": "--client,codex",
        "-launchCodex": "--client,codex",
        "-LaunchCodex": "--client,codex",
        "-fresh": "--fresh",
        "-Fresh": "--fresh",
        "-pick": "--pick",
        "-Pick": "--pick",
        "-fork": "--fork",
        "-Fork": "--fork",
        "-launch": None,           # legacy no-op
        "-Launch": None,
        "-disableReranker": "--disable-reranker",
        "-DisableReranker": "--disable-reranker",
        "-skipReindex": "--skip-reindex",
        "-SkipReindex": "--skip-reindex",
        "-registerClaude": "--register-claude",
        "-RegisterClaude": "--register-claude",
        "-writeClaudeProjectConfig": "--write-claude-project-config",
        "-WriteClaudeProjectConfig": "--write-claude-project-config",
        "-noAutoReindex": "--skip-reindex",
        "-NoAutoReindex": "--skip-reindex",
        "-modelConfigPath": None,   # legacy: skip value
        "-ModelConfigPath": None,
        "-rerankerConfigPath": None,
        "-RerankerConfigPath": None,
    }

    # Flags that take a following value (old name → canonical name)
    VALUE_FLAGS: dict[str, str] = {
        "-reindexMode": "--reindex-mode",
        "-ReindexMode": "--reindex-mode",
        "-projectRoot": "--workspace",
        "-ProjectRoot": "--workspace",
        "-serverName": "--server-name",
        "-ServerName": "--server-name",
        "-indexDir": "--index-dir",
        "-IndexDir": "--index-dir",
        "-embedPort": "--embed-port",
        "-EmbedPort": "--embed-port",
        "-rerankerPort": "--rerank-port",
        "-RerankerPort": "--rerank-port",
        "-embedModelGGUF": "--embed-gguf",
        "-EmbedModelGGUF": "--embed-gguf",
        "-rerankerModelGGUF": "--rerank-gguf",
        "-RerankerModelGGUF": "--rerank-gguf",
        "-llamaServerPath": "--llama-server",
        "-LlamaServerPath": "--llama-server",
    }

    VALUE_OPTIONS = {
        "--workspace",
        "--llama-server",
        "--embed-gguf",
        "--rerank-gguf",
        "--embed-port",
        "--rerank-port",
        "--reindex-mode",
        "--server-name",
        "--client",
    }

    out = []
    i = 0
    expect_value_for: str | None = None
    while i < len(argv):
        a = argv[i]
        a_lower = a.lower()

        if expect_value_for is not None:
            out.append(a)
            expect_value_for = None
            i += 1
            continue

        if a in MAP:
            mapped = MAP[a]
            if mapped is None:
                i += 1
                continue
            if "," in mapped:
                out.extend(mapped.split(","))
            else:
                out.append(mapped)
        elif a in VALUE_FLAGS:
            out.append(VALUE_FLAGS[a])
            expect_value_for = VALUE_FLAGS[a]
        elif a_lower in MAP:
            mapped = MAP[a_lower]
            if mapped is not None:
                out.extend(mapped.split(","))
        elif a_lower in VALUE_FLAGS:
            out.append(VALUE_FLAGS[a_lower])
            expect_value_for = VALUE_FLAGS[a_lower]
        elif a in VALUE_OPTIONS:
            out.append(a)
            expect_value_for = a
        elif a.startswith("-") and len(a) > 2 and not a.startswith("--"):
            # Generic: try converting single-dash multi-char → double-dash
            # Strip leading dash and check if it matches a known option
            # If ambiguous, pass through and let argparse fail with a clear message
            out.append("--" + a[1:])
        elif not a.startswith("-"):
            # positional → workspace
            out.append("--workspace")
            out.append(a)
        else:
            out.append(a)
        i += 1
    return out


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    argv = _normalize_argv(argv)

    parser = argparse.ArgumentParser(description="Local MCP search launcher (Python-only, no PowerShell)")
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--llama-server", default=None)
    parser.add_argument("--embed-gguf", default=None)
    parser.add_argument("--rerank-gguf", default=None)
    parser.add_argument("--embed-port", type=int, default=DEFAULT_EMBED_PORT)
    parser.add_argument("--rerank-port", type=int, default=DEFAULT_RERANK_PORT)
    parser.add_argument("--disable-reranker", action="store_true")
    parser.add_argument("--reindex-mode", choices=["auto", "full", "incremental"], default="auto")
    parser.add_argument("--server-name", default="local-search")

    # Client selection
    parser.add_argument("--client", choices=["codex", "claude", "none"], default="codex",
                        help="Which client to launch after setup (default: codex)")

    # Client options
    parser.add_argument("--fresh", action="store_true", help="Start a fresh session")
    parser.add_argument("--pick", action="store_true", help="Interactively pick a session to resume")
    parser.add_argument("--fork", action="store_true", help="Fork the resumed session")

    # MCP registration
    parser.add_argument("--register-claude", action="store_true",
                        help="Also register MCP server for Claude Code")
    parser.add_argument("--write-claude-project-config", action="store_true",
                        help="Write .mcp.json for Claude Code project-level config")

    # Dev / maintenance
    parser.add_argument("--keep-running", action="store_true",
                        help="After reindex, keep llama-servers alive (for MCP server use)")
    parser.add_argument("--skip-reindex", action="store_true",
                        help="Skip reindex step (e.g. when services already indexed)")

    args = parser.parse_args(argv)

    workspace = _resolve_workspace_root(args.workspace)
    private_config = _load_private_launcher_config(workspace)
    args.llama_server = _resolve_launcher_setting(
        args.llama_server, "LOCAL_SEARCH_LLAMA_SERVER", private_config, DEFAULT_LLAMA_SERVER
    )
    args.embed_gguf = _resolve_launcher_setting(
        args.embed_gguf, "LOCAL_SEARCH_EMBED_GGUF", private_config, DEFAULT_EMBED_GGUF
    )
    args.rerank_gguf = _resolve_launcher_setting(
        args.rerank_gguf, "LOCAL_SEARCH_RERANK_GGUF", private_config, DEFAULT_RERANK_GGUF
    )
    log_dir = Path(os.environ.get("TEMP", "/tmp")) / "llama-logs"

    # ── 1. Ensure llama-server endpoints ───────────────────────────────────
    embed = Endpoint(
        label="embedding", port=args.embed_port, extra_flag="--embedding",
        gguf=args.embed_gguf, probe_path="/v1/embeddings",
        probe_body={"model": EMBED_MODEL_NAME, "input": ["test"]},
    )
    rerank = Endpoint(
        label="reranker", port=args.rerank_port, extra_flag="--reranking",
        gguf=args.rerank_gguf, probe_path="/rerank",
        probe_body={"query": "test", "texts": ["test"]},
    )

    spawned: list[tuple[subprocess.Popen, str]] = []
    try:
        emb_proc = _ensure_running(embed, args.llama_server, log_dir)
        if emb_proc is not None:
            spawned.append((emb_proc, embed.label))

        rr_disabled = args.disable_reranker
        if not rr_disabled:
            rr_proc = _ensure_running(rerank, args.llama_server, log_dir)
            if rr_proc is not None:
                spawned.append((rr_proc, rerank.label))

        # ── 2. Set environment ─────────────────────────────────────────────
        os.environ["MCP_SEARCH_WORKSPACE_ROOT"] = workspace
        os.environ["EMBEDDING_BASE_URL"] = f"http://127.0.0.1:{args.embed_port}/v1"
        os.environ["EMBEDDING_MODEL"] = EMBED_MODEL_NAME
        os.environ["EMBEDDING_API_KEY"] = ""
        if rr_disabled:
            os.environ["MCP_SEARCH_RERANKER_ENABLED"] = "false"
            for k in ("RERANKER_BASE_URL", "RERANKER_MODEL", "RERANKER_API_KEY"):
                os.environ.pop(k, None)
        else:
            os.environ["MCP_SEARCH_RERANKER_ENABLED"] = "true"
            os.environ["RERANKER_BASE_URL"] = f"http://127.0.0.1:{args.rerank_port}"
            os.environ["RERANKER_MODEL"] = "bge-reranker-v2-m3"
            os.environ["RERANKER_API_KEY"] = ""

        # ── 3. Reindex ────────────────────────────────────────────────────
        if not args.skip_reindex:
            print(f"[mcp] reindexing workspace: {workspace} (mode={args.reindex_mode})")
            from .config import Settings
            from .retrieval import RetrievalService
            service = RetrievalService(Settings.from_env())
            result = service.reindex(mode=args.reindex_mode)
            print(json.dumps(result, ensure_ascii=False, indent=2))

        # ── 4. Register MCP ────────────────────────────────────────────────
        # MCP server command: always `python -m local_mcp_search`
        mcp_server_cmd = [sys.executable, "-m", "local_mcp_search"]

        # Set env vars in MCP server subprocess via a wrapper that exports them
        # We write a tiny helper script that sets env then calls python -m local_mcp_search
        # Actually, codex/claude will inherit our env, but since the MCP server
        # runs in a separate process started by codex/claude, we need to pass
        # env through the command line wrapper.
        # Simplest: write a one-shot .py wrapper that sets env then runs the server.
        _write_mcp_wrapper(workspace, args)

        wrapper_path = Path(workspace) / ".mcp-index" / "_mcp_server_wrapper.py"
        wrapper_cmd = [sys.executable, str(wrapper_path)]

        register_codex_mcp(args.server_name, wrapper_cmd, workspace)
        if args.register_claude:
            register_claude_mcp(args.server_name, wrapper_cmd, workspace)
        if args.write_claude_project_config:
            write_claude_project_mcp_config(workspace, args.server_name, wrapper_cmd)

        # ── 5. Launch client ───────────────────────────────────────────────
        exit_code = 0
        if args.client == "codex":
            exit_code = _launch_codex(workspace, args.fresh, args.pick, args.fork)
        elif args.client == "claude":
            exit_code = _launch_claude(workspace, args.fresh, args.pick, args.fork)
        # args.client == "none" → skip

        if args.keep_running:
            spawned.clear()
        return exit_code

    except Exception as exc:
        print(f"[launcher] error: {exc}", file=sys.stderr)
        return 1
    finally:
        for proc, label in spawned:
            _terminate(proc, label)


def _write_mcp_wrapper(workspace: str, args: argparse.Namespace) -> None:
    """Write a tiny Python script that sets env vars then runs the MCP server.

    This is needed because codex/claude start the MCP server in a fresh
    subprocess — our os.environ changes don't propagate. The wrapper script
    reproduces the same env, so the MCP server picks up the right endpoints.
    """
    idx_dir = Path(workspace) / ".mcp-index"
    idx_dir.mkdir(parents=True, exist_ok=True)
    wrapper = idx_dir / "_mcp_server_wrapper.py"

    rr_enabled = not args.disable_reranker
    code = f'''\
"""Auto-generated by cpx launcher. Do not edit."""
import os
import sys
from pathlib import Path

workspace = Path({workspace!r})
src_dir = workspace / "src"
if src_dir.is_dir():
    sys.path.insert(0, str(src_dir))

os.environ["MCP_SEARCH_WORKSPACE_ROOT"] = str(workspace)
os.environ["EMBEDDING_BASE_URL"] = "http://127.0.0.1:{args.embed_port}/v1"
os.environ["EMBEDDING_MODEL"] = "bge-base-zh"
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["MCP_SEARCH_RERANKER_ENABLED"] = {"true" if rr_enabled else "false"!r}
'''
    if rr_enabled:
        code += f'''\
os.environ["RERANKER_BASE_URL"] = "http://127.0.0.1:{args.rerank_port}"
os.environ["RERANKER_MODEL"] = "bge-reranker-v2-m3"
os.environ["RERANKER_API_KEY"] = ""
'''
    code += '''
from local_mcp_search.server import main
main()
'''
    wrapper.write_text(code, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
