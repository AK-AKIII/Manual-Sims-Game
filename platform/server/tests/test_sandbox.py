import unittest

from prtest_helpers import make_settings  # noqa: F401  (sets up sys.path)

from prplatform import config, sandbox


class DockerCommandTest(unittest.TestCase):
    def test_exact_command(self):
        s = config.load({"PR_MEMORY_MB": "512", "PR_TIME_LIMIT": "2.0", "PR_JUDGE_IMAGE": "poker-run-judge"})
        documented = (
            "docker run --rm -i --network none --memory 512m --memory-swap 512m --cpus 1 --pids-limit 64 "
            "--read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 --cap-drop ALL "
            "--security-opt no-new-privileges "
            "-v /var/jobs/abc:/job:ro poker-run-judge python3 /runner.py /job 2.0"
        ).split()
        self.assertEqual(sandbox.docker_command(s, "/var/jobs/abc"), documented)
        # With a container name (what the worker uses, so it can `docker kill` on timeout).
        named = sandbox.docker_command(s, "/var/jobs/abc", name="pr-judge-1")
        self.assertEqual(named, documented[:4] + ["--name", "pr-judge-1"] + documented[4:])

    def test_defaults(self):
        cmd = sandbox.docker_command(config.load({}), "/j")
        self.assertIn("1024m", cmd)
        self.assertEqual(cmd[-4:], ["python3", "/runner.py", "/job", "2.0"])
        self.assertEqual(sandbox.overall_timeout(50, 2.0), 50 * 3 + 30)

    def test_get_backend(self):
        self.assertIsInstance(sandbox.get(config.load({"PR_SANDBOX": "docker"})), sandbox.DockerSandbox)
        self.assertIsInstance(sandbox.get(config.load({})), sandbox.LocalSandbox)


class ConfigTest(unittest.TestCase):
    def test_seeds(self):
        self.assertEqual(config.parse_seeds("81723,90211,1000-1003"), [81723, 90211, 1000, 1001, 1002, 1003])
        self.assertEqual(config.parse_seeds("3, 1-3"), [3, 1, 2])
        for bad in ("x", "5-1", "1-", "-4"):
            with self.assertRaises(config.ConfigError, msg=bad):
                config.parse_seeds(bad)

    def test_defaults(self):
        s = config.load({})
        self.assertEqual(s.hidden_seeds, tuple(range(1001, 1051)))
        self.assertEqual(s.public_seeds, tuple(range(1, 11)))
        self.assertEqual(s.sandbox, "local")
        self.assertEqual(s.db, "./data/platform.db")
        self.assertFalse(s.secure_cookies)
        self.assertTrue(s.secret_generated)
        with self.assertRaises(config.ConfigError):
            config.load({"PR_SANDBOX": "chroot"})
        with self.assertRaises(config.ConfigError):
            config.load({"PR_START": "tomorrow"})

    def test_times(self):
        s = config.load({"PR_START": "2026-01-01T00:00:00Z", "PR_END": "2026-01-02T00:00:00+00:00"})
        self.assertFalse(s.is_open(config.parse_time("2025-12-31T23:59:59Z", "t")))
        self.assertTrue(s.is_open(config.parse_time("2026-01-01T12:00:00Z", "t")))
        self.assertFalse(s.is_open(config.parse_time("2026-01-02T00:00:00Z", "t")))


if __name__ == "__main__":
    unittest.main()


class RunnerSweepTest(unittest.TestCase):
    """judge/runner.py clears everything a solver left behind between tests (in the container only)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / "judge" / "runner.py"
        spec = importlib.util.spec_from_file_location("pr_runner", path)
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def test_never_sweeps_outside_the_container(self):
        self.assertFalse(self.runner.in_container())  # the test process is never PID 1

    def test_clear_dirs_keeps_only_the_scratch(self):
        import os
        import tempfile
        root = tempfile.mkdtemp()
        keep = os.path.join(root, "judge-scratch")
        os.mkdir(keep)
        open(os.path.join(keep, "out"), "w").close()
        os.mkdir(os.path.join(root, "stash"))
        open(os.path.join(root, "stash", "answers.txt"), "w").close()
        open(os.path.join(root, "answers.txt"), "w").close()
        os.symlink(keep, os.path.join(root, "link"))
        self.runner.clear_dirs([root, os.path.join(root, "missing")], keep=keep)
        self.assertEqual(sorted(os.listdir(root)), ["judge-scratch"])
        self.assertEqual(os.listdir(keep), ["out"])  # the symlink was removed, not followed
