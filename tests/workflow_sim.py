"""FN-078：本機模擬 GitHub Actions workflow（測試支援，離線）。

設計：
- 依序執行 ``jobs``，共用同一個 workdir。
- ``uses:`` 一律略過；唯 ``actions/download-artifact``（with.name=state-payload、
  with.path=state-out）會把 workdir/state-payload 複製到 workdir/state-out。
- env 只支援 ``${{ steps.<id>.outputs.<k> }}``；其他 ``${{ }}`` 必須由
  env_overrides 以同名環境變數提供，否則 SimStepError。
- ``if:`` 只支援 ``always()``。前一 step 失敗後，只有 always() step 仍會執行；
  其餘略過。所有可執行 step 跑完後才丟出第一個失敗的 SimStepError。
  前一 job 失敗則後續 job 不執行（等同 needs）。
- 每個 step 建立新的 GITHUB_OUTPUT 檔。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class SimStepError(Exception):
    """模擬 step 失敗、逾時或使用了不支援的語法。"""


@dataclass
class SimResult:
    # (name, "run"|"skipped-uses", rc, seconds)
    steps: list[tuple[str, str, int, float]] = field(default_factory=list)
    outputs: dict[str, dict[str, str]] = field(default_factory=dict)


_EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
_STEP_OUT = re.compile(r"^steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)$")


def _resolve(value, outputs: dict[str, dict[str, str]], where: str) -> str:
    text = str(value)

    def sub(m: re.Match) -> str:
        expr = m.group(1)
        mm = _STEP_OUT.match(expr)
        if not mm:
            raise SimStepError(f"unsupported expression '${{{{ {expr} }}}}' in {where}")
        sid, key = mm.groups()
        try:
            return outputs[sid][key]
        except KeyError:
            raise SimStepError(f"unknown step output steps.{sid}.outputs.{key} in {where}")

    return _EXPR.sub(sub, text)


def _merge_env(layers, overrides, outputs, where):
    merged: dict[str, str] = {}
    for layer in layers:
        for k, v in (layer or {}).items():
            if k in overrides:
                continue
            merged[str(k)] = _resolve(v, outputs, f"{where} env {k}")
    merged.update({str(k): str(v) for k, v in overrides.items()})
    return merged


def _tail(text: str, n: int = 50) -> str:
    return "\n".join(text.splitlines()[-n:])


def _should_run(step: dict, failed: bool, name: str) -> bool:
    cond = step.get("if")
    if cond is None:
        return not failed
    c = str(cond).strip()
    m = _EXPR.fullmatch(c)
    if m:
        c = m.group(1).strip()
    if c != "always()":
        raise SimStepError(f"unsupported if '{cond}' in step '{name}' (only always())")
    return True


def _parse_outputs(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


def run_workflow_locally(workflow_path: Path, workdir: Path, *,
                         jobs: tuple[str, ...] = ("build", "save-state"),
                         env_overrides: dict[str, str],
                         bash: str = "/bin/bash") -> SimResult:
    wf = yaml.safe_load(Path(workflow_path).read_text(encoding="utf-8")) or {}
    all_jobs = wf.get("jobs") or {}
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    overrides = dict(env_overrides or {})
    result = SimResult()
    first_error: SimStepError | None = None

    with tempfile.TemporaryDirectory(prefix="wfsim-") as tmp:
        tmpdir = Path(tmp)
        counter = 0
        for job_id in jobs:
            if job_id not in all_jobs:
                raise SimStepError(f"job '{job_id}' not found in workflow")
            if first_error is not None:
                break
            job = all_jobs[job_id] or {}
            for idx, step in enumerate(job.get("steps") or []):
                name = str(step.get("name") or step.get("id") or f"step-{idx}")
                failed = first_error is not None
                try:
                    if not _should_run(step, failed, name):
                        continue
                    if "uses" in step:
                        _do_uses(step, workdir)
                        result.steps.append((name, "skipped-uses", 0, 0.0))
                        continue
                    if "run" not in step:
                        continue
                    counter += 1
                    _do_run(step, name, wf, job, overrides, result, workdir,
                            tmpdir, counter, bash)
                except SimStepError as e:
                    if first_error is None:
                        first_error = e
    if first_error is not None:
        raise first_error
    return result


def _do_uses(step: dict, workdir: Path) -> None:
    uses = str(step["uses"])
    if not uses.startswith("actions/download-artifact"):
        return
    w = step.get("with") or {}
    if w.get("name") == "state-payload" and w.get("path") == "state-out":
        src = workdir / "state-payload"
        dst = workdir / "state-out"
        if not src.is_dir():
            raise SimStepError("download-artifact: state-payload/ not found in workdir")
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _do_run(step, name, wf, job, overrides, result, workdir, tmpdir, counter, bash):
    env = os.environ.copy()
    env.update(_merge_env([wf.get("env"), job.get("env"), step.get("env")],
                          overrides, result.outputs, f"step '{name}'"))
    out_file = tmpdir / f"output-{counter}"
    summary = tmpdir / f"summary-{counter}"
    out_file.write_text("")
    summary.write_text("")
    env["GITHUB_OUTPUT"] = str(out_file)
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    script = tmpdir / f"step-{counter}.sh"
    script.write_text(str(step["run"]), encoding="utf-8")

    cwd = workdir
    if step.get("working-directory"):
        cwd = workdir / str(step["working-directory"])
    minutes = float(step.get("timeout-minutes", 10))
    sid = step.get("id")
    start = time.monotonic()
    try:
        proc = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", str(script)],
                              cwd=str(cwd), env=env, capture_output=True, text=True,
                              timeout=minutes * 60)
    except subprocess.TimeoutExpired as e:
        secs = time.monotonic() - start
        result.steps.append((name, "run", 124, secs))
        partial = e.stdout or b""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        raise SimStepError(f"step '{name}' timed out after {minutes} minutes\n{_tail(partial)}")
    secs = time.monotonic() - start
    result.steps.append((name, "run", proc.returncode, secs))
    if sid:
        result.outputs[str(sid)] = _parse_outputs(out_file)
    if proc.returncode != 0:
        raise SimStepError(
            f"step '{name}' failed rc={proc.returncode}\n--- stdout (tail) ---\n"
            f"{_tail(proc.stdout)}\n--- stderr (tail) ---\n{_tail(proc.stderr)}")
