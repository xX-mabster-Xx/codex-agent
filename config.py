from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    telegram_user_id: int
    project_dir: Path
    projects_root: Path | None = None
    agent_topic_name: str = "Agent"
    agent_topic_id: int | None = None
    agent_project_dir: Path | None = None
    agent_thread_id: str | None = None
    proxy_url: str | None = None
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    openrouter_api_key: str | None = None
    openrouter_audio_model: str = "google/gemini-3.5-flash-lite"
    log_level: str = "INFO"
    log_file: Path = Path("logs/bot.log")

    @classmethod
    def load(cls) -> "Config":
        # Do not put secrets from .env into this process environment: the Codex
        # child process must never inherit the Telegram or OpenRouter token.
        values = {
            **dotenv_values(Path(__file__).with_name(".env")),
            **os.environ,
        }

        def value(name: str, default: str = "") -> str:
            return str(values.get(name) or default).strip()

        token = value("BOT_TOKEN")
        user_id = value("TELEGRAM_USER_ID")
        project_dir = Path(value("PROJECT_DIR")).expanduser()
        projects_root_raw = value("PROJECTS_ROOT")
        agent_topic_name = value("AGENT_TOPIC_NAME", "Agent")
        agent_topic_id_raw = value("AGENT_TOPIC_ID")
        agent_project_raw = value("AGENT_PROJECT_DIR")
        agent_thread_id = value("AGENT_THREAD_ID") or None
        proxy_url = value("PROXY_URL", "socks5://127.0.0.1:2060") or None
        google_oauth_client_id = value("GOOGLE_OAUTH_CLIENT_ID") or None
        google_oauth_client_secret = value("GOOGLE_OAUTH_CLIENT_SECRET") or None
        openrouter_api_key = value("OPENROUTER_API_KEY") or None
        openrouter_audio_model = value(
            "OPENROUTER_AUDIO_MODEL", "google/gemini-3.5-flash-lite"
        )
        log_level = value("LOG_LEVEL", "INFO").upper()
        log_file = Path(value("LOG_FILE", "logs/bot.log")).expanduser()

        if not token:
            raise RuntimeError("BOT_TOKEN is missing in .env")
        if not user_id.isdecimal():
            raise RuntimeError("TELEGRAM_USER_ID must be an integer")
        if not project_dir.is_absolute() or not project_dir.is_dir():
            raise RuntimeError("PROJECT_DIR must be an existing absolute directory")
        projects_root = (
            Path(projects_root_raw).expanduser()
            if projects_root_raw
            else project_dir / ".telegram-codex-projects"
        )
        if not projects_root.is_absolute():
            raise RuntimeError("PROJECTS_ROOT must be an absolute directory")
        try:
            projects_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise RuntimeError(f"Cannot create PROJECTS_ROOT: {error}") from error
        if agent_topic_id_raw and not agent_topic_id_raw.isdecimal():
            raise RuntimeError("AGENT_TOPIC_ID must be an integer")
        agent_topic_id = int(agent_topic_id_raw) if agent_topic_id_raw else None
        agent_project_dir = (
            Path(agent_project_raw).expanduser() if agent_project_raw else project_dir
        )
        if not agent_project_dir.is_absolute() or not agent_project_dir.is_dir():
            raise RuntimeError(
                "AGENT_PROJECT_DIR must be an existing absolute directory"
            )
        if proxy_url:
            parsed = urlparse(proxy_url)
            try:
                port = parsed.port
            except ValueError as error:
                raise RuntimeError("PROXY_URL contains an invalid port") from error
            if parsed.scheme not in {"http", "https", "socks4", "socks5"}:
                raise RuntimeError(
                    "PROXY_URL must start with http://, https://, socks4:// or socks5://"
                )
            if not parsed.hostname or port is None:
                raise RuntimeError("PROXY_URL must contain a host and port")
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise RuntimeError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        if not log_file.is_absolute():
            log_file = Path(__file__).parent / log_file

        return cls(
            bot_token=token,
            telegram_user_id=int(user_id),
            project_dir=project_dir.resolve(),
            projects_root=projects_root.resolve(),
            agent_topic_name=agent_topic_name,
            agent_topic_id=agent_topic_id,
            agent_project_dir=agent_project_dir.resolve(),
            agent_thread_id=agent_thread_id,
            proxy_url=proxy_url,
            google_oauth_client_id=google_oauth_client_id,
            google_oauth_client_secret=google_oauth_client_secret,
            openrouter_api_key=openrouter_api_key,
            openrouter_audio_model=openrouter_audio_model,
            log_level=log_level,
            log_file=log_file.resolve(),
        )
