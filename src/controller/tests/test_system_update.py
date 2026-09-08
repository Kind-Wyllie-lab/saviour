"""Tests for src/controller/system_update.py.

git_checkout_info / pull_and_reset / notify_modules exercised with
subprocess mocked; stage_zip runs for real against a temp tree.
"""

import json
import os
import subprocess
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from src.controller import system_update as su

# --------------------------------------------------------------------------- #
# git_checkout_info
# --------------------------------------------------------------------------- #

class TestGitCheckoutInfo:
    def test_no_git_dir(self, tmp_path):
        info = su.git_checkout_info(str(tmp_path))
        assert info == {"available": False,
                        "reason": "No git checkout on this device"}

    def test_named_branch_with_origin(self, tmp_path):
        (tmp_path / ".git").mkdir()
        outs = {"rev-parse": "staging\n", "get-url": "git@github.com:x/y.git\n"}

        def fake_run(cmd, **kw):
            key = "rev-parse" if "rev-parse" in cmd else "get-url"
            return MagicMock(stdout=outs[key])

        with patch.object(su.subprocess, "run", side_effect=fake_run):
            info = su.git_checkout_info(str(tmp_path))
        assert info == {"available": True, "branch": "staging",
                        "remote": "git@github.com:x/y.git"}

    def test_detached_head(self, tmp_path):
        (tmp_path / ".git").mkdir()
        with patch.object(su.subprocess, "run",
                          return_value=MagicMock(stdout="HEAD\n")):
            info = su.git_checkout_info(str(tmp_path))
        assert info["available"] is False
        assert "Detached HEAD" in info["reason"]

    def test_no_origin_remote(self, tmp_path):
        (tmp_path / ".git").mkdir()

        def fake_run(cmd, **kw):
            return MagicMock(stdout="main\n" if "rev-parse" in cmd else "\n")

        with patch.object(su.subprocess, "run", side_effect=fake_run):
            info = su.git_checkout_info(str(tmp_path))
        assert info["available"] is False
        assert "origin" in info["reason"]


# --------------------------------------------------------------------------- #
# pull_and_reset
# --------------------------------------------------------------------------- #

class TestPullAndReset:
    def test_runs_fetch_then_reset_and_reports_commits(self):
        calls = []
        heads = iter(["aaaaaaa", "bbbbbbb"])

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "rev-parse" in cmd:
                return MagicMock(stdout=next(heads) + "\n")
            return MagicMock(stdout="", stderr="")

        with patch.object(su.subprocess, "run", side_effect=fake_run):
            res = su.pull_and_reset("staging", src_root="/x")

        assert res == {"branch": "staging",
                       "old_commit": "aaaaaaa", "new_commit": "bbbbbbb"}
        assert ["git", "-C", "/x", "fetch", "--prune", "origin", "staging"] in calls
        assert ["git", "-C", "/x", "reset", "--hard", "origin/staging"] in calls

    def test_git_failure_propagates(self):
        def fake_run(cmd, **kw):
            if "rev-parse" in cmd:
                return MagicMock(stdout="aaa\n")
            raise subprocess.CalledProcessError(1, cmd, stderr="boom")

        with patch.object(su.subprocess, "run", side_effect=fake_run):
            with pytest.raises(subprocess.CalledProcessError):
                su.pull_and_reset("staging", src_root="/x")


# --------------------------------------------------------------------------- #
# stage_zip  (real)
# --------------------------------------------------------------------------- #

class TestStageZip:
    def test_zips_tree_skipping_junk_and_writes_meta(self, tmp_path):
        src = tmp_path / "src"
        (src / "src" / "controller").mkdir(parents=True)
        (src / "src" / "controller" / "web.py").write_text("x = 1\n")
        (src / "README.md").write_text("hi\n")
        (src / ".git").mkdir()
        (src / ".git" / "HEAD").write_text("ref: x\n")
        (src / "env").mkdir()
        (src / "env" / "big").write_text("y" * 100)
        (src / "src" / "controller" / "cache.pyc").write_text("nope")

        zp = tmp_path / "store" / "pkg.zip"
        mp = tmp_path / "store" / "meta.json"
        meta = su.stage_zip(version="v9.9-1-gdead", src_root=str(src),
                            zip_path=str(zp), meta_path=str(mp))

        assert zp.exists() and not (tmp_path / "store" / "pkg.zip.tmp").exists()
        names = set(zipfile.ZipFile(zp).namelist())
        assert "src/controller/web.py" in names
        assert "README.md" in names
        assert not any(n.startswith(".git/") for n in names)
        assert not any(n.startswith("env/") for n in names)
        assert not any(n.endswith(".pyc") for n in names)
        assert meta["version"] == "v9.9-1-gdead"
        assert json.loads(mp.read_text())["size_bytes"] == os.path.getsize(zp)


# --------------------------------------------------------------------------- #
# notify_modules
# --------------------------------------------------------------------------- #

class TestNotifyModules:
    def test_sends_update_saviour_to_each(self):
        send = MagicMock()
        n = su.notify_modules(send, ["cam_a", "cam_b"], "http://10.0.0.1:5000")
        assert n == 2
        send.assert_any_call("cam_a", "update_saviour",
                             {"controller_url": "http://10.0.0.1:5000"})
        send.assert_any_call("cam_b", "update_saviour",
                             {"controller_url": "http://10.0.0.1:5000"})

    def test_one_failure_does_not_stop_the_rest(self):
        send = MagicMock(side_effect=[RuntimeError("x"), None])
        n = su.notify_modules(send, ["a", "b"], "http://c:5000")
        assert n == 2
        assert send.call_count == 2


# --------------------------------------------------------------------------- #
# snapshot
# --------------------------------------------------------------------------- #

class TestSnapshot:
    def test_success_writes_meta_and_prunes(self, tmp_path):
        bdir = tmp_path / "backups"
        bdir.mkdir()
        for old in ("20200101T000000Z_v1", "20200102T000000Z_v2",
                    "20200103T000000Z_v3"):
            (bdir / old).mkdir()

        def fake_run(cmd, **kw):
            # emulate rsync creating dest
            dest = cmd[-1].rstrip("/")
            os.makedirs(dest, exist_ok=True)
            return MagicMock(returncode=0)

        with patch.object(su.subprocess, "run", side_effect=fake_run):
            res = su.snapshot("pre-x", "v4", src_root=str(tmp_path),
                              backup_dir=str(bdir))
        assert res["ok"] is True
        snap = bdir / res["name"]
        assert json.loads((snap / ".backup_meta.json").read_text())["reason"] == "pre-x"
        # kept CTRL_BACKUP_KEEP-1 of the pre-existing + the new one
        remaining = sorted(p.name for p in bdir.iterdir())
        assert res["name"] in remaining
        assert len(remaining) <= su.CTRL_BACKUP_KEEP

    def test_rsync_failure_is_best_effort(self, tmp_path):
        with patch.object(su.subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, "rsync")):
            res = su.snapshot("pre-x", "v4", src_root=str(tmp_path),
                              backup_dir=str(tmp_path / "b"))
        assert res["ok"] is False
        assert "error" in res
