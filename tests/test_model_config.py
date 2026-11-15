import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from model_api import ModelConfigurationError, load_project_environment, validate_model_configuration


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ModelConfigTests(unittest.TestCase):
    def test_project_env_loads_for_a_new_process_started_outside_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            scripts = project / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "model_api.py").write_text(
                (PROJECT_ROOT / "scripts" / "model_api.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (project / ".env").write_text(
                "OPENAI_API_MODE=chat_completions\n"
                "OPENAI_BASE_URL=https://agi.tongji.edu.cn/v1\n"
                "OPENAI_MODEL=DeepSeek-V4.1-Flash\n"
                "OPENAI_API_KEY=test-only-secret\n"
                "NO_PROXY=internal.example\n",
                encoding="utf-8",
            )
            probe = Path(temp_dir) / "probe.py"
            probe.write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(scripts)!r})\n"
                "from model_api import load_project_environment\n"
                "env = load_project_environment()\n"
                "print(env['OPENAI_API_MODE'])\n"
                "print(env['OPENAI_MODEL'])\n"
                "print('key_loaded=' + str(bool(env.get('OPENAI_API_KEY'))))\n"
                "print(env['NO_PROXY'])\n",
                encoding="utf-8",
            )
            clean_env = os.environ.copy()
            for key in ("OPENAI_API_MODE", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY", "NO_PROXY", "no_proxy"):
                clean_env.pop(key, None)
            output = subprocess.check_output(
                [sys.executable, str(probe)],
                cwd=temp_dir,
                env=clean_env,
                text=True,
            )

            self.assertIn("chat_completions", output)
            self.assertIn("DeepSeek-V4.1-Flash", output)
            self.assertIn("key_loaded=True", output)
            self.assertIn("internal.example,agi.tongji.edu.cn", output)
            self.assertNotIn("test-only-secret", output)

    def test_explicit_environment_overrides_dotenv_and_no_proxy_entries_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "OPENAI_API_MODE=chat_completions\nOPENAI_MODEL=from-file\nNO_PROXY=internal.example\n",
                encoding="utf-8",
            )
            env = load_project_environment(
                {
                    "OPENAI_API_MODE": "chat_completions",
                    "OPENAI_MODEL": "from-environment",
                    "OPENAI_BASE_URL": "https://agi.tongji.edu.cn/v1",
                    "OPENAI_API_KEY": "unit-test-key",
                    "NO_PROXY": "proxy-bypass.local",
                },
                project_root=root,
            )

            self.assertEqual(env["OPENAI_MODEL"], "from-environment")
            self.assertEqual(env["NO_PROXY"], "proxy-bypass.local,agi.tongji.edu.cn")
            self.assertEqual(env["no_proxy"], env["NO_PROXY"])

    def test_missing_key_has_project_env_instruction_and_base_url_remains_required(self):
        with self.assertRaises(ModelConfigurationError) as missing_key:
            validate_model_configuration({
                "OPENAI_API_MODE": "chat_completions",
                "OPENAI_MODEL": "DeepSeek-V4.1-Flash",
            })
        self.assertIn((PROJECT_ROOT / ".env").as_posix(), str(missing_key.exception))
        self.assertIn("OPENAI_API_KEY", str(missing_key.exception))
        with self.assertRaisesRegex(ModelConfigurationError, "OPENAI_BASE_URL"):
            validate_model_configuration({
                "OPENAI_API_MODE": "chat_completions",
                "OPENAI_API_KEY": "unit-test-key",
                "OPENAI_MODEL": "DeepSeek-V4.1-Flash",
            })


if __name__ == "__main__":
    unittest.main()
