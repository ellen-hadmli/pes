#!/usr/bin/env python3
"""Build a portable Hermes v0.22.2 config and store a Telegram token safely."""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "config" / "config.template.yaml"
LIBRARY_ROOT = ROOT / "Library"
LIBRARY_README = LIBRARY_ROOT / "README.md"
GROUP_PROMPT = (
    "В групповом чате используй доступные инструменты по запросу участников. "
    "Изменять собственный код, skills и конфигурацию разрешается только в личном "
    "чате с владельцем. Не переноси личный или относящийся к третьим лицам контекст "
    "между чатами, если владелец явно этого не попросил."
)
TOKEN_LINE = re.compile(r"(?m)^TELEGRAM_BOT_TOKEN=.*(?:\n|$)")


def _ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    values = [part.strip() for part in raw.split(",") if part.strip()]
    if any(not re.fullmatch(r"-?\d+", value) for value in values):
        raise ValueError("ID чата должен быть числовым Telegram ID.")
    return list(dict.fromkeys(values))


def _deep_merge(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Overlay generated settings while retaining unrelated nested user settings."""
    result = dict(old)
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _atomic_private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token or any(ch in token for ch in "\r\n\x00"):
        raise ValueError("Файл токена должен содержать одну непустую строку.")
    # Keep dotenv parsing inert: no quotes, whitespace, interpolation or shell syntax.
    if any(ch.isspace() for ch in token) or any(ch in token for ch in "'\"$`"):
        raise ValueError("Формат токена в отдельном файле не поддерживается.")
    return token


def _write_env(home: Path, token: str, replace_token: bool) -> None:
    path = home / ".env"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    match = TOKEN_LINE.search(old)
    if match and not replace_token:
        raise FileExistsError("В ~/.hermes/.env уже задан Telegram token; для замены используйте --replace-token.")
    new = TOKEN_LINE.sub("", old).rstrip()
    if new:
        new += "\n"
    new += f"TELEGRAM_BOT_TOKEN={token}\n"
    _atomic_private_write(path, new.encode("utf-8"))


def build_config(owner_id: str, group_ids: list[str], template_path: Path = TEMPLATE,
                 existing: dict[str, Any] | None = None) -> dict[str, Any]:
    base = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
    merged = _deep_merge(existing or {}, base)
    prompt = merged["agent"]["system_prompt"]
    merged["agent"]["system_prompt"] = (prompt
        .replace("{{PROJECT_ROOT}}", str(ROOT.resolve()))
        .replace("{{LIBRARY_README}}", str(LIBRARY_README.resolve())))
    telegram = merged["platforms"]["telegram"]
    telegram["allowed_chats"] = list(dict.fromkeys([owner_id, *group_ids]))
    telegram["group_allowed_chats"] = group_ids
    # Telegram's adapter selects allow_from for DMs and group_allow_from for groups.
    # Pin DM access to the owner while explicitly leaving every member of allowed groups eligible.
    telegram["allow_from"] = [owner_id]
    telegram["group_allow_from"] = ["*"]
    # The v0.22.2 loader lets authored `extra` keys win over matching shared keys.
    # Pin both locations so a stale extra.allow_from cannot override the owner.
    telegram["extra"]["allow_from"] = [owner_id]
    telegram["extra"]["group_allow_from"] = ["*"]
    telegram["channel_prompts"] = {**telegram.get("channel_prompts", {}),
                                   **{group_id: GROUP_PROMPT for group_id in group_ids}}
    return merged


def configure(args: argparse.Namespace) -> None:
    owner = args.owner_id or os.environ.get("PES_OWNER_ID")
    groups = list(dict.fromkeys([*_ids(os.environ.get("PES_GROUP_IDS")), *_ids(",".join(args.group_id))]))
    if not owner or not re.fullmatch(r"-?\d+", owner):
        raise ValueError("Укажите числовой --owner-id или PES_OWNER_ID.")
    token_file = args.telegram_token_file or (Path(os.environ["PES_TELEGRAM_TOKEN_FILE"])
                                               if os.environ.get("PES_TELEGRAM_TOKEN_FILE") else None)
    if token_file is None:
        raise ValueError("Укажите --telegram-token-file или PES_TELEGRAM_TOKEN_FILE (путь к отдельному файлу токена).")
    token = _read_token(token_file)

    home = args.home.expanduser()
    config_path = home / "config.yaml"
    existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else None
    if existing is not None and not isinstance(existing, dict):
        raise ValueError("Существующий config.yaml должен быть YAML mapping.")
    config = build_config(owner, groups, existing=existing)
    encoded = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")
    env_path = home / ".env"
    if env_path.exists() and TOKEN_LINE.search(env_path.read_text(encoding="utf-8")) and not args.replace_token:
        raise FileExistsError("В ~/.hermes/.env уже задан Telegram token; для замены используйте --replace-token.")
    _write_env(home, token, args.replace_token)
    _atomic_private_write(config_path, encoded)
    print(f"Созданы приватные файлы Hermes config и .env в {home}; значения токена не выводятся.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", help="Telegram ID владельца; или PES_OWNER_ID")
    parser.add_argument("--group-id", action="append", default=[], help="Разрешённый Telegram group ID (повторять)")
    parser.add_argument("--telegram-token-file", type=Path, help="Отдельный файл, содержащий только токен")
    parser.add_argument("--replace-token", action="store_true", help="Разрешить замену токена в целевом .env")
    parser.add_argument("--home", type=Path, default=Path.home() / ".hermes", help="Тестовый/целевой Hermes home")
    args = parser.parse_args(argv)
    try:
        configure(args)
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
