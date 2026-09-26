import unittest

from zss import installer

SAMPLE_ZSHRC = """\
# Enable Powerlevel10k instant prompt
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-$(whoami).zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-$(whoami).zsh"
fi

export SOME_TOKEN=abc123secret
source $(brew --prefix zsh-autosuggestions)/share/zsh-autosuggestions/zsh-autosuggestions.zsh
source $(brew --prefix zsh-fast-syntax-highlighting)/share/zsh-fast-syntax-highlighting/fast-syntax-highlighting.plugin.zsh

alias ll="ls -la"
"""


class TestInstall(unittest.TestCase):
    def test_disables_old_plugin_lines(self):
        new_text = installer.compute_install(SAMPLE_ZSHRC, "/repo")
        self.assertIn("# source $(brew --prefix zsh-autosuggestions)", new_text)
        self.assertIn("zss:disabled", new_text)
        # the token line is untouched and never logged/printed by this module
        self.assertIn("export SOME_TOKEN=abc123secret", new_text)

    def test_inserts_block_where_old_lines_were(self):
        new_text = installer.compute_install(SAMPLE_ZSHRC, "/repo")
        lines = new_text.splitlines()
        fast_idx = next(i for i, l in enumerate(lines) if "fast-syntax-highlighting.plugin.zsh" in l)
        block_start_idx = next(i for i, l in enumerate(lines) if l == installer.BLOCK_START)
        self.assertEqual(block_start_idx, fast_idx + 1)

    def test_block_contents(self):
        new_text = installer.compute_install(SAMPLE_ZSHRC, "/repo")
        self.assertIn("HISTSIZE=50000", new_text)
        self.assertIn("SAVEHIST=50000", new_text)
        self.assertIn("setopt INC_APPEND_HISTORY HIST_FCNTL_LOCK", new_text)
        self.assertIn('source "/repo/zsh-smart-suggest.plugin.zsh"', new_text)
        self.assertIn('export PATH="/repo/bin:$PATH"', new_text)

    def test_idempotent(self):
        once = installer.compute_install(SAMPLE_ZSHRC, "/repo")
        twice = installer.compute_install(once, "/repo")
        self.assertEqual(once, twice)
        # exactly one block, not two
        self.assertEqual(once.count(installer.BLOCK_START), 1)
        self.assertEqual(once.count("zss:disabled"), 2)

    def test_no_old_plugins_appends_at_end(self):
        text = "alias ll='ls -la'\n"
        new_text = installer.compute_install(text, "/repo")
        self.assertTrue(new_text.startswith(text))
        self.assertIn(installer.BLOCK_START, new_text)


class TestUninstall(unittest.TestCase):
    def test_reverses_install(self):
        installed = installer.compute_install(SAMPLE_ZSHRC, "/repo")
        uninstalled = installer.compute_uninstall(installed)
        self.assertEqual(uninstalled, SAMPLE_ZSHRC)

    def test_uninstall_of_untouched_file_is_noop(self):
        self.assertEqual(installer.compute_uninstall(SAMPLE_ZSHRC), SAMPLE_ZSHRC)


class TestDiff(unittest.TestCase):
    def test_diff_shows_only_changed_lines(self):
        new_text = installer.compute_install(SAMPLE_ZSHRC, "/repo")
        d = installer.diff(SAMPLE_ZSHRC, new_text)
        self.assertIn("+HISTSIZE=50000", d)
        self.assertIn("-source $(brew --prefix zsh-autosuggestions)", d)
        # unrelated lines are not duplicated as changes
        self.assertNotIn("+alias ll", d)


if __name__ == "__main__":
    unittest.main()
