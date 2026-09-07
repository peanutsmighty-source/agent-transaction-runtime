from argparse import Namespace
from pathlib import Path

from runtime.cli import _read_api_key, build_parser


def test_cli_accepts_generic_responses_provider_configuration() -> None:
    args = build_parser().parse_args(
        [
            "run",
            "inspect workspace",
            "--workspace",
            ".",
            "--provider",
            "responses",
            "--provider-name",
            "deepseek",
            "--api-base-url",
            "https://api.deepseek.com",
            "--api-key-file",
            "E:/tmp/key.txt",
            "--model",
            "deepseek-v4-flash",
        ]
    )

    assert args.provider == "responses"
    assert args.provider_name == "deepseek"
    assert args.api_base_url == "https://api.deepseek.com"
    assert args.api_key_file == Path("E:/tmp/key.txt")


def test_cli_reads_key_from_named_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_PROVIDER_KEY", "secret-value")
    args = Namespace(api_key_file=None, api_key_env="DEMO_PROVIDER_KEY")

    assert _read_api_key(args) == "secret-value"


def test_cli_reads_key_file_without_putting_secret_in_arguments(tmp_path) -> None:
    key_file = tmp_path / "provider.key"
    key_file.write_text("secret-value\n", encoding="utf-8")
    args = Namespace(api_key_file=key_file, api_key_env="UNUSED")

    assert _read_api_key(args) == "secret-value"
