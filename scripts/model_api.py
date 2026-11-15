import os
from pathlib import Path
from typing import Mapping


class ModelConfigurationError(RuntimeError):
    pass


class ModelRequestError(RuntimeError):
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TONGJI_HOST = "agi.tongji.edu.cn"


def _read_dotenv(dotenv_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not dotenv_path.is_file():
        return values
    for raw_line in dotenv_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_project_environment(
    environ: Mapping[str, str] | None = None,
    *,
    project_root: str | Path | None = None,
) -> dict[str, str]:
    """Load project .env values without overriding explicit process settings."""
    use_process_environment = environ is None or environ is os.environ
    source = os.environ if use_process_environment else environ
    merged = {str(key): str(value) for key, value in source.items()}
    dotenv_values = (
        _read_dotenv(Path(project_root) / ".env" if project_root else PROJECT_ROOT / ".env")
        if use_process_environment or project_root is not None
        else {}
    )
    for key, value in dotenv_values.items():
        merged.setdefault(key, value)

    if use_process_environment:
        for key, value in dotenv_values.items():
            os.environ.setdefault(key, value)

    mode = merged.get("OPENAI_API_MODE", "responses").strip().lower()
    base_url = merged.get("OPENAI_BASE_URL", "").lower()
    if mode == "chat_completions" or TONGJI_HOST in base_url:
        proxy_entries: list[str] = []
        seen: set[str] = set()
        for proxy_value in (merged.get("NO_PROXY", ""), merged.get("no_proxy", "")):
            for entry in proxy_value.split(","):
                entry = entry.strip()
                if entry and entry.casefold() not in seen:
                    seen.add(entry.casefold())
                    proxy_entries.append(entry)
        if TONGJI_HOST.casefold() not in seen:
            proxy_entries.append(TONGJI_HOST)
        no_proxy = ",".join(proxy_entries)
        merged["NO_PROXY"] = no_proxy
        merged["no_proxy"] = no_proxy
        if use_process_environment:
            os.environ["NO_PROXY"] = no_proxy
            os.environ["no_proxy"] = no_proxy
    return merged


def validate_model_configuration(environ: Mapping[str, str]) -> str:
    mode = environ.get("OPENAI_API_MODE", "responses").strip().lower()
    if mode not in {"responses", "chat_completions"}:
        raise ModelConfigurationError("OPENAI_API_MODE 必须是 responses 或 chat_completions。")
    if not environ.get("OPENAI_API_KEY", "").strip():
        dotenv_path = (PROJECT_ROOT / ".env").as_posix()
        raise ModelConfigurationError(
            f"缺少 OPENAI_API_KEY。请打开项目根目录的 `{dotenv_path}`，填写 OPENAI_API_KEY 后重启程序。"
        )
    if not environ.get("OPENAI_MODEL", "").strip():
        raise ModelConfigurationError("缺少环境变量 OPENAI_MODEL，请检查项目根目录的 .env。")
    if mode == "chat_completions" and not environ.get("OPENAI_BASE_URL", "").strip():
        raise ModelConfigurationError(
            "同济 chat_completions 模式必须显式设置 OPENAI_BASE_URL；已停止请求以避免将学校 Key 发往默认地址。"
        )
    return mode


def response_format_unsupported(error) -> bool:
    if getattr(error, "status_code", None) != 400:
        return False
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        error_body = body.get("error", body)
        if isinstance(error_body, dict):
            message = " ".join(
                str(error_body.get(key, "")) for key in ("message", "code", "type", "param")
            )
        else:
            message = str(error_body)
    else:
        message = str(error)
    lowered = message.lower()
    mentions_format = "response_format" in lowered or "json_object" in lowered
    rejects_format = any(
        phrase in lowered
        for phrase in (
            "not support",
            "unsupported",
            "not supported",
            "unrecognized",
            "unknown parameter",
            "invalid parameter",
        )
    )
    return mentions_format and rejects_format


def create_openai_client(environ: Mapping[str, str]):
    from openai import OpenAI

    config = load_project_environment(environ)
    mode = validate_model_configuration(config)
    options = {"api_key": config["OPENAI_API_KEY"]}
    if mode == "chat_completions":
        options["base_url"] = config["OPENAI_BASE_URL"]
    return OpenAI(**options)


def chat_completion_json(client, model: str, messages: list[dict]) -> str:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        from openai import BadRequestError, OpenAIError

        if isinstance(exc, BadRequestError) and response_format_unsupported(exc):
            try:
                response = client.chat.completions.create(model=model, messages=messages)
            except OpenAIError as retry_error:
                raise ModelRequestError(
                    f"普通文本回退请求失败（{type(retry_error).__name__}）。"
                ) from None
        elif isinstance(exc, OpenAIError):
            raise ModelRequestError(f"Chat Completions 请求失败（{type(exc).__name__}）。") from None
        else:
            raise

    choices = getattr(response, "choices", None)
    if not choices:
        raise ModelRequestError("Chat Completions 返回空响应：没有 choices。")
    content = getattr(getattr(choices[0], "message", None), "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ModelRequestError("Chat Completions 返回空响应：message.content 为空。")
    return content.strip()
