"""FN-078 workflow_sim 離線測試。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from workflow_sim import SimStepError, run_workflow_locally  # noqa: E402


def _wf(tmp_path, text):
    p = tmp_path / "wf.yml"
    p.write_text(text)
    return p


def _run(tmp_path, text, **kw):
    wd = tmp_path / "wd"
    kw.setdefault("env_overrides", {})
    return run_workflow_locally(_wf(tmp_path, text), wd, **kw), wd


def test_output_and_env_reference(tmp_path):
    res, wd = _run(tmp_path, """
on: push
env: {TOP: t}
jobs:
  build:
    steps:
      - id: a
        run: echo "val=hello" >> "$GITHUB_OUTPUT"
      - id: b
        env:
          X: ${{ steps.a.outputs.val }}
        run: echo "$X-$TOP" > got.txt
""", jobs=("build",))
    assert res.outputs["a"] == {"val": "hello"}
    assert (wd / "got.txt").read_text().strip() == "hello-t"
    assert [s[2] for s in res.steps] == [0, 0]


def test_uses_skipped(tmp_path):
    res, _ = _run(tmp_path, """
jobs:
  build:
    steps:
      - uses: actions/checkout@v4
      - run: echo hi
""", jobs=("build",))
    assert res.steps[0][1] == "skipped-uses"
    assert res.steps[1][1] == "run"


def test_download_artifact_copies_and_cross_job(tmp_path):
    res, wd = _run(tmp_path, """
jobs:
  build:
    steps:
      - run: mkdir -p state-payload/site && echo x > state-payload/site/a.txt
      - uses: actions/upload-artifact@v4
        with: {name: state-payload, path: state-payload}
  save-state:
    needs: build
    steps:
      - uses: actions/download-artifact@v4
        with: {name: state-payload, path: state-out}
      - run: cat state-out/site/a.txt > seen.txt
""")
    assert (wd / "state-out/site/a.txt").read_text().strip() == "x"
    assert (wd / "seen.txt").read_text().strip() == "x"
    assert len(res.steps) == 4


def test_other_download_artifact_not_copied(tmp_path):
    _, wd = _run(tmp_path, """
jobs:
  build:
    steps:
      - run: mkdir state-payload
      - uses: actions/download-artifact@v4
        with: {name: other, path: elsewhere}
""", jobs=("build",))
    assert not (wd / "state-out").exists()


def test_failure_includes_output_tail(tmp_path):
    with pytest.raises(SimStepError) as ei:
        _run(tmp_path, """
jobs:
  build:
    steps:
      - name: boom
        run: |
          for i in $(seq 1 80); do echo line$i; done
          echo errmsg >&2
          exit 3
""", jobs=("build",))
    msg = str(ei.value)
    assert "boom" in msg and "rc=3" in msg and "line80" in msg and "errmsg" in msg
    assert "line1\n" not in msg


def test_unsupported_expression_raises_and_override_ok(tmp_path):
    text = """
jobs:
  build:
    steps:
      - run: echo "$GH_TOKEN" > tok.txt
        env:
          GH_TOKEN: ${{ github.token }}
"""
    with pytest.raises(SimStepError, match="unsupported expression"):
        _run(tmp_path, text, jobs=("build",))
    _, wd = _run(tmp_path, text, jobs=("build",), env_overrides={"GH_TOKEN": "abc"})
    assert (wd / "tok.txt").read_text().strip() == "abc"


def test_unsupported_if_raises(tmp_path):
    with pytest.raises(SimStepError, match="unsupported if"):
        _run(tmp_path, """
jobs:
  build:
    steps:
      - if: github.ref == 'x'
        run: echo hi
""", jobs=("build",))


def test_always_runs_after_failure_then_raises(tmp_path):
    wf = """
jobs:
  build:
    steps:
      - run: exit 1
      - name: skipped
        run: echo no > skipped.txt
      - name: cleanup
        if: always()
        run: echo yes > cleanup.txt
  save-state:
    steps:
      - run: echo no > job2.txt
"""
    wd = tmp_path / "wd"
    with pytest.raises(SimStepError):
        run_workflow_locally(_wf(tmp_path, wf), wd, env_overrides={},
                             jobs=("build", "save-state"))
    assert (wd / "cleanup.txt").exists()
    assert not (wd / "skipped.txt").exists()
    assert not (wd / "job2.txt").exists()


def test_timeout(tmp_path):
    with pytest.raises(SimStepError, match="timed out"):
        _run(tmp_path, """
jobs:
  build:
    steps:
      - timeout-minutes: 0.02
        run: sleep 20
""", jobs=("build",))


def test_missing_job(tmp_path):
    with pytest.raises(SimStepError, match="not found"):
        _run(tmp_path, "jobs:\n  build:\n    steps: []\n", jobs=("nope",))
