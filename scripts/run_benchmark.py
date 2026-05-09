from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from local_mcp_search.config import Settings
from local_mcp_search.launcher import (  # type: ignore
    DEFAULT_EMBED_GGUF,
    DEFAULT_EMBED_PORT,
    DEFAULT_LLAMA_SERVER,
    DEFAULT_RERANK_GGUF,
    DEFAULT_RERANK_PORT,
    _ensure_running,
    _load_private_launcher_config,
    _resolve_launcher_setting,
    _write_mcp_wrapper,
    Endpoint,
)
from local_mcp_search.retrieval import RetrievalService


DEFAULT_PAUSE_SECONDS = 12.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 30.0
RETRYABLE_FAILURE_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "rate-limit",
    "retry limit",
    "throttl",
    "try again later",
    "temporarily unavailable",
    "server overloaded",
)


def _find_command_path(name: str) -> str:
    for ext in (".cmd", ".exe", ".bat", ".ps1"):
        candidate = shutil.which(name + ext)
        if candidate:
            return candidate
    direct = shutil.which(name)
    if direct:
        return direct
    raise FileNotFoundError(f"Cannot find command in PATH: {name}")


def _build_windows_command(name: str, args: list[str]) -> list[str]:
    path = _find_command_path(name)
    suffix = Path(path).suffix.lower()
    if suffix == ".ps1":
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            path,
            *args,
        ]
    return [path, *args]


@dataclass
class ClientResult:
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    parsed: dict[str, Any] | None
    structured_output: dict[str, Any] | None
    session_id: str | None
    total_cost_usd: float | None
    raw_path: Path
    failure_reason: str | None


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_failure_text(result: ClientResult) -> str:
    parts = [part.strip() for part in (result.failure_reason, result.stderr, result.stdout) if part and part.strip()]
    return "\n".join(parts)[-4000:]


def _is_retryable_failure(result: ClientResult) -> bool:
    text = _extract_failure_text(result).lower()
    return any(marker in text for marker in RETRYABLE_FAILURE_MARKERS)


def _result_succeeded(result: ClientResult) -> bool:
    return result.exit_code == 0 and not result.failure_reason


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_text(text: str) -> dict[str, Any] | None:
    candidate = _strip_code_fence(text)
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_match_text(value: str) -> str:
    normalized = value.strip().lower().replace("\\", "/")
    normalized = re.sub(r"(\.[a-z0-9_]+):\d+\b", r"\1", normalized)
    return normalized


def _build_wrapper(workspace: str, disable_reranker: bool, embed_port: int, rerank_port: int) -> Path:
    class Args:
        pass

    args = Args()
    args.disable_reranker = disable_reranker
    args.embed_port = embed_port
    args.rerank_port = rerank_port
    _write_mcp_wrapper(workspace, args)
    return Path(workspace) / ".mcp-index" / "_mcp_server_wrapper.py"


def _prepare_local_search(workspace: str, mode: str, keep_running: bool) -> dict[str, Any]:
    private_config = _load_private_launcher_config(workspace)
    llama_server = _resolve_launcher_setting(
        None, "LOCAL_SEARCH_LLAMA_SERVER", private_config, DEFAULT_LLAMA_SERVER
    )
    embed_gguf = _resolve_launcher_setting(
        None, "LOCAL_SEARCH_EMBED_GGUF", private_config, DEFAULT_EMBED_GGUF
    )
    rerank_gguf = _resolve_launcher_setting(
        None, "LOCAL_SEARCH_RERANK_GGUF", private_config, DEFAULT_RERANK_GGUF
    )

    embed = Endpoint(
        label="embedding",
        port=DEFAULT_EMBED_PORT,
        extra_flag="--embedding",
        gguf=embed_gguf,
        probe_path="/v1/embeddings",
        probe_body={"model": "bge-base-zh", "input": ["test"]},
    )
    rerank = Endpoint(
        label="reranker",
        port=DEFAULT_RERANK_PORT,
        extra_flag="--reranking",
        gguf=rerank_gguf,
        probe_path="/rerank",
        probe_body={"query": "test", "texts": ["test"]},
    )

    spawned: list[tuple[subprocess.Popen[Any], str]] = []
    emb_proc = _ensure_running(embed, llama_server, Path(os.environ.get("TEMP", "/tmp")) / "llama-logs")
    if emb_proc is not None:
        spawned.append((emb_proc, embed.label))

    disable_reranker = mode == "local-search-no-reranker"
    if not disable_reranker:
        rr_proc = _ensure_running(rerank, llama_server, Path(os.environ.get("TEMP", "/tmp")) / "llama-logs")
        if rr_proc is not None:
            spawned.append((rr_proc, rerank.label))

    os.environ["MCP_SEARCH_WORKSPACE_ROOT"] = workspace
    os.environ["EMBEDDING_BASE_URL"] = f"http://127.0.0.1:{DEFAULT_EMBED_PORT}/v1"
    os.environ["EMBEDDING_MODEL"] = "bge-base-zh"
    os.environ["EMBEDDING_API_KEY"] = ""
    if disable_reranker:
        os.environ["MCP_SEARCH_RERANKER_ENABLED"] = "false"
        os.environ.pop("RERANKER_BASE_URL", None)
        os.environ.pop("RERANKER_MODEL", None)
        os.environ.pop("RERANKER_API_KEY", None)
    else:
        os.environ["MCP_SEARCH_RERANKER_ENABLED"] = "true"
        os.environ["RERANKER_BASE_URL"] = f"http://127.0.0.1:{DEFAULT_RERANK_PORT}"
        os.environ["RERANKER_MODEL"] = "bge-reranker-v2-m3"
        os.environ["RERANKER_API_KEY"] = ""

    service = RetrievalService(Settings.from_env())
    reindex_result = service.reindex(mode="auto")
    wrapper_path = _build_wrapper(
        workspace=workspace,
        disable_reranker=disable_reranker,
        embed_port=DEFAULT_EMBED_PORT,
        rerank_port=DEFAULT_RERANK_PORT,
    )
    if not keep_running:
        for proc, _label in spawned:
            if proc.poll() is None:
                proc.terminate()
    return {
        "wrapper_path": str(wrapper_path),
        "reindex_result": reindex_result,
        "disable_reranker": disable_reranker,
    }


def _make_codex_home(temp_home: Path) -> None:
    _ensure_dir(temp_home)
    auth_src = Path.home() / ".codex" / "auth.json"
    if auth_src.is_file():
        shutil.copy2(auth_src, temp_home / "auth.json")

    source_cfg = Path.home() / ".codex" / "config.toml"
    lines = source_cfg.read_text(encoding="utf-8").splitlines() if source_cfg.is_file() else []
    kept: list[str] = []
    skip_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[mcp_servers.") or stripped == "[projects]" or stripped.startswith("[projects."):
            skip_section = True
            continue
        if stripped.startswith("[") and not (
            stripped.startswith("[mcp_servers.") or stripped == "[projects]" or stripped.startswith("[projects.")
        ):
            skip_section = False
        if skip_section:
            continue
        kept.append(line)
    (temp_home / "config.toml").write_text("\n".join(kept) + "\n", encoding="utf-8")


def _run_codex(
    workspace: str,
    prompt: str,
    output_schema_path: Path,
    result_dir: Path,
    mode: str,
    wrapper_path: Path | None,
) -> ClientResult:
    temp_home = REPO_ROOT / "benchmark" / "tmp" / f"codex-home-{uuid.uuid4().hex[:8]}"
    if temp_home.exists():
        shutil.rmtree(temp_home)
    _make_codex_home(temp_home)
    config_append = []
    if wrapper_path is not None:
        config_append.extend(
            [
                '-c',
                f'mcp_servers.local-search.command={json.dumps(sys.executable)}',
                '-c',
                f'mcp_servers.local-search.args={json.dumps([str(wrapper_path)])}',
            ]
        )
    cmd = _build_windows_command(
        "codex",
        [
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ignore-rules",
            "-C",
            workspace,
            "--output-schema",
            str(output_schema_path),
            *config_append,
            "-",
        ],
    )
    env = os.environ.copy()
    env["CODEX_HOME"] = str(temp_home)
    started = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=workspace,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    duration = time.perf_counter() - started
    raw_path = result_dir / f"codex-{mode}.jsonl"
    raw_path.write_text(proc.stdout, encoding="utf-8")
    parsed = None
    structured = None
    session_id = None
    total_cost_usd = None
    failure_reason = None
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        parsed = payload
        if isinstance(payload, dict):
            structured = payload.get("structured_output")
            session_id = payload.get("session_id")
            total_cost_usd = payload.get("total_cost_usd")
            if payload.get("type") == "turn.failed":
                failure = payload.get("error") or {}
                if isinstance(failure, dict):
                    failure_reason = failure.get("message")
        break
    if structured is None:
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "item.completed":
                item = payload.get("item") or {}
                text = item.get("text")
                if text:
                    try:
                        structured = json.loads(text)
                    except json.JSONDecodeError:
                        structured = {"text": text}
                    break
            if payload.get("type") == "error" and not failure_reason:
                failure_reason = payload.get("message")
    shutil.rmtree(temp_home, ignore_errors=True)
    return ClientResult(
        exit_code=proc.returncode,
        duration_seconds=duration,
        stdout=proc.stdout,
        stderr=proc.stderr,
        parsed=parsed,
        structured_output=structured,
        session_id=session_id,
        total_cost_usd=total_cost_usd,
        raw_path=raw_path,
        failure_reason=failure_reason,
    )


def _run_claude(
    workspace: str,
    prompt: str,
    result_dir: Path,
    mode: str,
    wrapper_path: Path | None,
) -> ClientResult:
    mcp_config_path = result_dir / "claude-mcp.json"
    if wrapper_path is None:
        _write_json(mcp_config_path, {"mcpServers": {}})
    else:
        _write_json(
            mcp_config_path,
            {
                "mcpServers": {
                    "local-search": {
                        "command": sys.executable,
                        "args": [str(wrapper_path)],
                    }
                }
            },
        )

    cmd = _build_windows_command(
        "claude",
        [
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
            "--strict-mcp-config",
            "--mcp-config",
            str(mcp_config_path),
        ],
    )
    started = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=workspace,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = time.perf_counter() - started
    raw_path = result_dir / f"claude-{mode}.json"
    raw_path.write_text(proc.stdout, encoding="utf-8")
    parsed = None
    structured = None
    session_id = None
    total_cost_usd = None
    failure_reason = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
            structured = parsed.get("structured_output")
            if structured is None:
                result_text = parsed.get("result")
                if isinstance(result_text, str):
                    structured = _parse_json_text(result_text)
            session_id = parsed.get("session_id")
            total_cost_usd = parsed.get("total_cost_usd")
            if parsed.get("subtype") != "success":
                failure_reason = parsed.get("result")
        except json.JSONDecodeError:
            parsed = None
    return ClientResult(
        exit_code=proc.returncode,
        duration_seconds=duration,
        stdout=proc.stdout,
        stderr=proc.stderr,
        parsed=parsed,
        structured_output=structured,
        session_id=session_id,
        total_cost_usd=total_cost_usd,
        raw_path=raw_path,
        failure_reason=failure_reason,
    )


def _collect_text_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values.append(value)
    elif isinstance(value, list):
        for item in value:
            values.extend(_collect_text_values(item))
    elif isinstance(value, dict):
        for item in value.values():
            values.extend(_collect_text_values(item))
    return values


def _score_task(task: dict[str, Any], structured_output: dict[str, Any] | None) -> dict[str, Any]:
    expected = [_normalize_match_text(item) for item in task.get("expected_any", [])]
    min_matches = int(task.get("min_expected_matches", len(expected) or 1))
    values = [_normalize_match_text(item) for item in _collect_text_values(structured_output or {})]
    joined = "\n".join(values)
    matched = [item for item in expected if item in joined]
    return {
        "passed": len(matched) >= min_matches,
        "matched": matched,
        "expected": task.get("expected_any", []),
        "min_expected_matches": min_matches,
    }


def _run_case(
    client: str,
    mode: str,
    task: dict[str, Any],
    run_root: Path,
    output_schema_path: Path,
    keep_running: bool,
    max_retries: int,
    retry_backoff_seconds: float,
) -> dict[str, Any]:
    workspace = str(Path(task["workspace"]).resolve())
    case_id = f"{task['id']}--{client}--{mode}"
    case_dir = run_root / case_id
    if case_dir.exists():
        shutil.rmtree(case_dir)
    _ensure_dir(case_dir)

    wrapper_path: Path | None = None
    prep_result: dict[str, Any] | None = None
    if mode != "baseline":
        prep_result = _prepare_local_search(workspace=workspace, mode=mode, keep_running=keep_running)
        wrapper_path = Path(prep_result["wrapper_path"])

    prompt = task["prompt"]
    attempts = 0
    retryable_failure = False
    while True:
        attempts += 1
        if client == "codex":
            result = _run_codex(workspace, prompt, output_schema_path, case_dir, mode, wrapper_path)
        else:
            result = _run_claude(workspace, prompt, case_dir, mode, wrapper_path)
        retryable_failure = _is_retryable_failure(result)
        if attempts > max_retries or _result_succeeded(result) or not retryable_failure:
            break
        backoff = retry_backoff_seconds * (2 ** (attempts - 1))
        reason = result.failure_reason or "transient provider throttling"
        print(
            f"[benchmark] retry {attempts}/{max_retries + 1} for {case_id} "
            f"after {backoff:.1f}s: {reason}"
        )
        time.sleep(backoff)

    score = _score_task(task, result.structured_output)
    payload = {
        "case_id": case_id,
        "task_id": task["id"],
        "client": client,
        "mode": mode,
        "workspace": workspace,
        "exit_code": result.exit_code,
        "duration_seconds": round(result.duration_seconds, 3),
        "session_id": result.session_id,
        "total_cost_usd": result.total_cost_usd,
        "failure_reason": result.failure_reason,
        "attempts": attempts,
        "retryable_failure": retryable_failure,
        "passed": score["passed"] and _result_succeeded(result),
        "score": score,
        "structured_output": result.structured_output,
        "stderr": result.stderr,
        "raw_output_path": str(result.raw_path),
        "prep_result": prep_result,
    }
    _write_json(case_dir / "result.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run automated Codex/Claude benchmark cases.")
    parser.add_argument("--tasks", default=str(REPO_ROOT / "benchmark" / "tasks.json"))
    parser.add_argument(
        "--task-ids",
        nargs="+",
        help="Only run the selected task ids from benchmark/tasks.json.",
    )
    parser.add_argument("--clients", nargs="+", choices=["codex", "claude"], default=["codex", "claude"])
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["baseline", "local-search"],
        default=["baseline", "local-search"],
    )
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "benchmark" / "results"))
    parser.add_argument("--keep-running", action="store_true", help="Keep llama subprocesses alive after prep.")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=DEFAULT_PAUSE_SECONDS,
        help="Pause between cases to reduce provider throttling.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Retry count for transient provider failures such as HTTP 429.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help="Base backoff in seconds for retryable failures; doubled on each retry.",
    )
    args = parser.parse_args()

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    run_root = Path(args.output_dir) / run_id
    _ensure_dir(run_root)

    output_schema_path = run_root / "output-schema.json"
    _write_json(
        output_schema_path,
        {
            "type": "object",
            "additionalProperties": True,
        },
    )

    tasks = _load_tasks(Path(args.tasks))
    if args.task_ids:
        requested = {item.strip() for item in args.task_ids if item.strip()}
        tasks = [task for task in tasks if task["id"] in requested]
        found = {task["id"] for task in tasks}
        missing = sorted(requested - found)
        if missing:
            parser.error(f"Unknown task ids: {', '.join(missing)}")
    if not tasks:
        parser.error("No benchmark tasks selected.")

    summary: list[dict[str, Any]] = []
    total = len(tasks) * len(args.clients) * len(args.modes)
    index = 0
    for task in tasks:
        for client in args.clients:
            for mode in args.modes:
                index += 1
                print(f"[benchmark] {index}/{total} {task['id']} | {client} | {mode}")
                payload = _run_case(
                    client=client,
                    mode=mode,
                    task=task,
                    run_root=run_root,
                    output_schema_path=output_schema_path,
                    keep_running=args.keep_running,
                    max_retries=args.max_retries,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                )
                summary.append(payload)
                if args.pause_seconds > 0 and index < total:
                    print(f"[benchmark] pause {args.pause_seconds:.1f}s before next case")
                    time.sleep(args.pause_seconds)

    passed = sum(1 for item in summary if item["passed"])
    report = {
        "run_id": run_id,
        "total_cases": len(summary),
        "passed_cases": passed,
        "failed_cases": len(summary) - passed,
        "cases": summary,
    }
    _write_json(run_root / "summary.json", report)
    print(json.dumps({"run_id": run_id, "total_cases": len(summary), "passed_cases": passed}, ensure_ascii=False))
    return 0 if passed == len(summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
