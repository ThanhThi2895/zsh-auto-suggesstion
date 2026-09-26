"""End-to-end tests for the GitHub install path (install.sh remote mode,
`zss update`, uninstall.sh's clone removal). A local bare-bones git repo
built from this working tree stands in for GitHub via ZSS_REPO_URL, and HOME
points at a temp dir, so the real ~/.zshrc and ~/.zsh_history are never
touched. Each subprocess runs in a new session so /dev/tty is unavailable and
prompts are answered from stdin.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from zss import installer

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ["install.sh", "uninstall.sh", "zsh-smart-suggest.plugin.zsh", "bin", "lib", "zss"]
ZSHRC = "alias ll='ls -la'\n"


@unittest.skipUnless(shutil.which("git"), "git is required")
class RemoteInstallTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.home / ".zshrc").write_text(ZSHRC)
        self.origin = self.tmp / "origin"
        self.install_dir = self.home / ".zsh-smart-suggest"
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "ZSS_REPO_URL": str(self.origin),
            "ZSS_HISTFILE": str(self.tmp / "histfile"),
            "XDG_DATA_HOME": str(self.tmp / "data"),
        }
        self._make_origin()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True)

    def _make_origin(self):
        self.origin.mkdir()
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
        for name in SHIPPED:
            src = REPO_ROOT / name
            if src.is_dir():
                shutil.copytree(src, self.origin / name, ignore=ignore)
            else:
                shutil.copy2(src, self.origin / name)
        self.git("init", "-q", "-b", "main", cwd=self.origin)
        self.git("add", "-A", cwd=self.origin)
        self.git("commit", "-q", "-m", "init", cwd=self.origin)

    def commit_to(self, repo, name, content="new\n"):
        (repo / name).write_text(content)
        self.git("add", name, cwd=repo)
        self.git("commit", "-q", "-m", f"add {name}", cwd=repo)

    def commit_to_origin(self, name, content="new\n"):
        self.commit_to(self.origin, name, content)

    def git_out(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.install_dir), *args],
            env=self.env, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def exec(self, argv, input_text=""):
        return subprocess.run(
            argv, env=self.env, input=input_text, capture_output=True, text=True,
            cwd=self.tmp, start_new_session=True,
        )

    def standalone_install_sh(self):
        """install.sh on its own, as `curl -o install.sh` would leave it."""
        dl = self.tmp / "download"
        dl.mkdir(exist_ok=True)
        shutil.copy2(REPO_ROOT / "install.sh", dl / "install.sh")
        return dl / "install.sh"

    def zshrc(self):
        return (self.home / ".zshrc").read_text()


class TestRemoteInstall(RemoteInstallTestCase):
    def test_clones_and_installs_from_clone(self):
        result = self.exec(["bash", str(self.standalone_install_sh())], "y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.install_dir / ".git").is_dir())
        self.assertIn(f'source "{self.install_dir}/zsh-smart-suggest.plugin.zsh"', self.zshrc())

    def test_curl_pipe_bash_clones_and_prompt_reads_eof(self):
        # Piped script: no tty, so the [y/N] prompt sees EOF and aborts
        # without writing; the clone itself still happens.
        script = (REPO_ROOT / "install.sh").read_text()
        result = self.exec(["bash"], script)
        self.assertEqual(result.returncode, 1)
        self.assertTrue((self.install_dir / "zss" / "_installer_main.py").is_file())
        self.assertEqual(self.zshrc(), ZSHRC)

    def test_rerun_pulls_updates_and_stays_idempotent(self):
        sh = str(self.standalone_install_sh())
        self.assertEqual(self.exec(["bash", sh], "y\n").returncode, 0)
        first = self.zshrc()
        self.commit_to_origin("NEW_FILE")
        result = self.exec(["bash", sh], "y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.install_dir / "NEW_FILE").exists())
        self.assertEqual(self.zshrc(), first)
        self.assertEqual(first.count(installer.BLOCK_START), 1)

    def test_install_dir_override(self):
        self.env["ZSS_INSTALL_DIR"] = str(self.tmp / "custom dir")
        result = self.exec(["bash", str(self.standalone_install_sh())], "y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.install_dir.exists())
        self.assertIn(f'source "{self.tmp / "custom dir"}/zsh-smart-suggest.plugin.zsh"', self.zshrc())

    def test_refuses_non_git_install_dir(self):
        self.install_dir.mkdir()
        (self.install_dir / "keep").write_text("x")
        result = self.exec(["bash", str(self.standalone_install_sh())], "y\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a zsh-smart-suggest git clone", result.stderr)
        self.assertTrue((self.install_dir / "keep").exists())
        self.assertEqual(self.zshrc(), ZSHRC)

    def test_refuses_unrelated_git_clone(self):
        # A git repo without our plugin file: never retarget or pull it.
        self.install_dir.mkdir()
        self.git("init", "-q", "-b", "main", cwd=self.install_dir)
        self.git("remote", "add", "origin", "https://example.invalid/other.git", cwd=self.install_dir)
        result = self.exec(["bash", str(self.standalone_install_sh())], "y\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a zsh-smart-suggest git clone", result.stderr)
        remote = subprocess.run(
            ["git", "-C", str(self.install_dir), "remote", "get-url", "origin"],
            env=self.env, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(remote, "https://example.invalid/other.git")
        self.assertEqual(self.zshrc(), ZSHRC)

    def test_rerun_honours_repo_url_and_branch(self):
        sh = str(self.standalone_install_sh())
        self.assertEqual(self.exec(["bash", sh], "y\n").returncode, 0)
        fork = self.tmp / "fork"
        self.git("clone", "-q", str(self.origin), str(fork), cwd=self.tmp)
        self.git("checkout", "-q", "-b", "beta", cwd=fork)
        self.commit_to(fork, "BETA_FILE")
        self.env["ZSS_REPO_URL"] = str(fork)
        self.env["ZSS_BRANCH"] = "beta"
        result = self.exec(["bash", sh], "y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.install_dir / "BETA_FILE").exists())
        self.assertEqual(self.git_out("remote", "get-url", "origin"), str(fork))
        self.assertEqual(self.git_out("rev-parse", "--abbrev-ref", "HEAD"), "beta")
        # Switching back to main (already a local branch) works too.
        self.env["ZSS_BRANCH"] = "main"
        result = self.exec(["bash", sh], "y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git_out("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertFalse((self.install_dir / "BETA_FILE").exists())

    def test_local_checkout_installs_itself(self):
        result = self.exec(["bash", str(self.origin / "install.sh")], "y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.install_dir.exists())
        self.assertIn(f'source "{self.origin}/zsh-smart-suggest.plugin.zsh"', self.zshrc())


class TestZssUpdate(RemoteInstallTestCase):
    def run_zss(self, *args, root, input_text=""):
        self.env["PYTHONPATH"] = str(root)
        return self.exec([sys.executable, "-m", "zss", *args], input_text)

    def test_update_pulls_new_commits(self):
        self.git("clone", "-q", str(self.origin), str(self.install_dir), cwd=self.tmp)
        (self.home / ".zshrc").write_text(installer.compute_install(ZSHRC, str(self.install_dir)))
        self.commit_to_origin("NEW_FILE")
        result = self.run_zss("update", root=self.install_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.install_dir / "NEW_FILE").exists())
        self.assertIn("nothing to do", result.stdout)

    def test_update_reinstalls_changed_block(self):
        # The pulled installer renders a different block than the one in
        # ~/.zshrc, so `zss update` must offer (and apply) the new one.
        self.git("clone", "-q", str(self.origin), str(self.install_dir), cwd=self.tmp)
        old_zshrc = installer.compute_install(ZSHRC, str(self.install_dir))
        (self.home / ".zshrc").write_text(old_zshrc)
        installer_py = self.origin / "zss" / "installer.py"
        self.commit_to_origin(
            "zss/installer.py",
            installer_py.read_text().replace("HISTSIZE=50000", "HISTSIZE=60000"),
        )
        result = self.run_zss("update", root=self.install_dir, input_text="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HISTSIZE=60000", self.zshrc())
        self.assertEqual(self.zshrc().count(installer.BLOCK_START), 1)

    def test_update_declined_reinstall_reports_failure(self):
        self.git("clone", "-q", str(self.origin), str(self.install_dir), cwd=self.tmp)
        self.commit_to_origin("NEW_FILE")
        result = self.run_zss("update", root=self.install_dir, input_text="n\n")
        self.assertEqual(result.returncode, 1)
        self.assertTrue((self.install_dir / "NEW_FILE").exists())
        self.assertIn("~/.zshrc was not changed", result.stderr)
        self.assertEqual(self.zshrc(), ZSHRC)

    def test_update_outside_git_clone_fails(self):
        plain = self.tmp / "plain"
        shutil.copytree(self.origin / "zss", plain / "zss")
        result = self.run_zss("update", root=plain)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a git clone", result.stderr)


class TestUninstallRemovesClone(RemoteInstallTestCase):
    def setUp(self):
        super().setUp()
        self.git("clone", "-q", str(self.origin), str(self.install_dir), cwd=self.tmp)
        self.uninstall = str(self.install_dir / "uninstall.sh")

    def test_confirm_removes_clone(self):
        # ~/.zshrc has no block, so the zshrc step doesn't prompt and the
        # only answer on stdin goes to the removal prompt.
        result = self.exec(["bash", self.uninstall], "y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.install_dir.exists())

    def test_default_keeps_clone(self):
        result = self.exec(["bash", self.uninstall], "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.install_dir.exists())

    def test_declined_zshrc_change_keeps_clone_without_asking(self):
        (self.home / ".zshrc").write_text(installer.compute_install(ZSHRC, str(self.install_dir)))
        result = self.exec(["bash", self.uninstall], "n\n")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(self.install_dir.exists())
        self.assertNotIn("Also delete", result.stdout + result.stderr)

    def test_refuses_dir_that_is_not_our_clone(self):
        other = self.tmp / "other"
        other.mkdir()
        self.env["ZSS_INSTALL_DIR"] = str(other)
        result = self.exec(["bash", self.uninstall], "y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(other.exists())
        self.assertIn("doesn't look like", result.stderr)


if __name__ == "__main__":
    unittest.main()
