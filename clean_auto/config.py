from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


PACKAGE_DIR = Path(__file__).resolve().parent

MAX_CHARS = 12000
DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024

CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 1200.0
WRITE_TIMEOUT = 60.0
POOL_TIMEOUT = 60.0

MAX_RETRIES = 3
RETRY_BASE_SECONDS = 5.0
MAX_RETRY_WAIT_SECONDS = 300.0
MAX_CONSECUTIVE_FAILURES = 5
BATCH_ID_PATTERN = re.compile(
    r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$"
)

REQUIRE_CONFIRMATION = True
MIN_WORKERS = 1
MAX_WORKERS = 5

_LOG_WRITE_LOCK = threading.Lock()

# 教材清洗默认保留兼容性。
# 使用 --strict 时，第一片必须有完整 Front Matter。
DEFAULT_STRICT_VALIDATION = False

REQUIRED_FRONT_MATTER_FIELDS = (
    "title",
    "subject",
    "source",
    "type",
    "year",
    "status",
)

ALLOWED_SUBJECTS = (
    "安全生产法律法规",
    "安全生产管理",
    "安全生产技术基础",
    "安全生产专业实务",
)

FRONT_MATTER_STATUS = "OCR清洗完成"

RETRYABLE_STATUS_CODES = {
    408,
    409,
    425,
    429,
}

SUSPICIOUS_PHRASES = (
    "以下是处理结果",
    "以下为处理结果",
    "处理结果如下",
    "清洗说明：",
    "修改说明：",
    "处理说明：",
)

SENSITIVE_PATTERNS = (
    (
        re.compile(
            r"(Bearer\s+)[^\s'\"\\]{8,}",
            re.IGNORECASE,
        ),
        r"\1***",
    ),
    (
        re.compile(
            r"(api[_-]?key\s*[:=]\s*)"
            r"[^\s'\"\\,]{8,}",
            re.IGNORECASE,
        ),
        r"\1***",
    ),
    (
        re.compile(
            r"(Authorization\s*[:=]\s*)"
            r"[^\s'\"\\,]{8,}",
            re.IGNORECASE,
        ),
        r"\1***",
    ),
    (
        re.compile(
            r"sk-[A-Za-z0-9_-]{8,}"
        ),
        "***",
    ),
    (
        re.compile(
            r"key-[A-Za-z0-9_-]{8,}"
        ),
        "***",
    ),
)


@dataclass
class RequestResult:
    text: str
    elapsed_seconds: float
    received_events: int
    received_chars: int
    truncated: bool = False


@dataclass
class FilePlan:
    source_path: Path
    relative_path: Path
    source_sha256: str
    source_chars: int
    chunks: list[str]
    output_dir: Path

    @property
    def is_empty(self) -> bool:
        return (
            self.source_chars == 0
            or not any(
                chunk.strip()
                for chunk in self.chunks
            )
        )


@dataclass
class ProcessStats:
    total_parts: int = 0
    success_parts: int = 0
    failed_parts: int = 0
    skipped_parts: int = 0


@dataclass
class ProcessOutcome:
    stats: ProcessStats
    consecutive_failures: int
    stopped: bool = False


@dataclass
class RuntimeConfig:
    api_key: str
    base_url: str
    model: str
    system_prompt: str
    prompt_sha256: str
    strict_validation: bool
    max_chars: int
    max_file_size: int
    pause_file: Path
    stop_file: Path
    base_dir: Path
    input_dir: Path
    output_dir: Path
    log_dir: Path
    lock_file: Path
    pause_between_files: float = 0.0
    pause_between_chunks: float = 0.0
    pause_after_files: int = 0
    max_files: int = 0
    dry_run: bool = False
    workers: int = 1


class GracefulStop(Exception):
    def __init__(
        self,
        message: str = "收到安全停止请求",
    ) -> None:
        super().__init__(message)


class RetryableRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        partial_text: str = "",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.partial_text = partial_text
        self.status_code = status_code


def get_base_dir() -> Path:
    """
    项目根目录优先级：

    1. --base-dir 在 load_runtime_config 中处理；
    2. RAG_CLEANER_HOME；
    3. 当前工作目录。
    """
    env = os.environ.get(
        "RAG_CLEANER_HOME",
        "",
    ).strip()

    if env:
        return Path(env).expanduser().resolve()

    return Path.cwd().resolve()


def get_paths(
    base_dir: Path | None = None,
) -> dict[str, Path]:
    root = (
        base_dir or get_base_dir()
    ).resolve()

    return {
        "base_dir": root,
        "input_dir": root / "input",
        "output_dir": root / "output",
        "log_dir": root / "logs",
        "prompt_file": root / "prompt.md",
        "lock_file": root / ".clean_auto.lock",
        "env_file": root / ".env",
    }


def normalize_base_url(value: str) -> str:
    """
    校验并规范化 API Base URL。

    禁止：

    - 非 http/https 协议；
    - 用户名和密码；
    - 查询参数；
    - URL 片段。

    这样可以避免把 api_key 放进 URL，
    进而写入日志、metadata 或浏览器历史。
    """
    value = value.strip()

    if not value:
        return ""

    parts = urlsplit(value)

    if parts.scheme not in {
        "http",
        "https",
    }:
        raise RuntimeError(
            "OPENAI_BASE_URL 必须使用 http 或 https"
        )

    if not parts.hostname:
        raise RuntimeError(
            "OPENAI_BASE_URL 缺少有效主机名"
        )

    if parts.scheme == "http":
        hostname = parts.hostname.rstrip(
            "."
        ).lower()

        is_loopback = hostname == "localhost"

        if not is_loopback:
            try:
                is_loopback = (
                    ipaddress.ip_address(
                        hostname
                    ).is_loopback
                )
            except ValueError:
                is_loopback = False

        if not is_loopback:
            raise RuntimeError(
                "OPENAI_BASE_URL 使用 HTTP 时"
                "只允许 localhost 或 loopback 地址；"
                "远程接口必须使用 HTTPS"
            )

    if parts.username or parts.password:
        raise RuntimeError(
            "OPENAI_BASE_URL 不允许包含用户名或密码"
        )

    if parts.query or parts.fragment:
        raise RuntimeError(
            "OPENAI_BASE_URL 不允许包含查询参数或片段"
        )

    path = parts.path.rstrip("/")

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            path,
            "",
            "",
        )
    )


def safe_base_url(value: str) -> str:
    """
    返回不包含查询参数、片段、用户名和密码的安全 URL。

    用于日志和 metadata。
    """
    try:
        return normalize_base_url(value)
    except RuntimeError:
        return value.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def resolve_control_file(
    base_dir: Path,
    value: str,
    option_name: str,
) -> Path:
    """
    将暂停/停止标记限制在项目根目录内。
    """
    root = base_dir.resolve()
    candidate = (
        root / value
    ).resolve()

    if not candidate.is_relative_to(root):
        raise RuntimeError(
            f"{option_name} 必须位于项目根目录内"
        )

    return candidate


def now_iso() -> str:
    return datetime.now().isoformat(
        timespec="seconds"
    )


def redact_secrets(text: str) -> str:
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(
            replacement,
            text,
        )

    return text


def compact_error(
    exc: Exception,
    limit: int = 2000,
) -> str:
    text = redact_secrets(str(exc))

    text = text.replace(
        "\r",
        " ",
    ).replace(
        "\n",
        " ",
    )

    return text[:limit]


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            block = file.read(1024 * 1024)

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


def read_text(path: Path) -> str:
    last_error: Exception | None = None

    for encoding in (
        "utf-8-sig",
        "utf-8",
        "gb18030",
        "gbk",
    ):
        try:
            with path.open(
                "r",
                encoding=encoding,
                newline="",
            ) as file:
                return file.read()
        except UnicodeDecodeError as exc:
            last_error = exc

    raise RuntimeError(
        f"无法识别文件编码：{path}"
    ) from last_error


def strip_outer_code_fence(
    text: str,
) -> str:
    text = text.strip()

    match = re.fullmatch(
        r"```(?:markdown|md|text)?"
        r"[ \t]*\r?\n"
        r"(.*?)"
        r"\r?\n```[ \t]*",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return (
        match.group(1).strip()
        if match
        else text
    )


def safe_name(
    name: str,
    fallback: str = "unnamed",
) -> str:
    name = re.sub(
        r'[\\/:*?"<>|]+',
        "_",
        name,
    )
    name = re.sub(
        r"\s+",
        "_",
        name,
    ).strip(" ._")

    return name or fallback


def atomic_write_text(
    path: Path,
    text: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )

    temporary_path = Path(
        temporary_name
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="",
        ) as file:
            file.write(text)
            file.flush()

            try:
                os.fsync(file.fileno())
            except OSError:
                pass

        temporary_path.replace(path)

    except Exception:
        try:
            temporary_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        raise


def atomic_write_json(
    path: Path,
    data: dict[str, Any],
) -> None:
    atomic_write_text(
        path,
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
    )


def append_log(
    log_dir: Path,
    filename: str,
    status: str,
    detail: str | dict[str, Any],
) -> None:
    """
    追加运行日志。

    URL、认证头和 API key 会经过基础脱敏。
    """
    log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if isinstance(detail, str):
        safe_detail: str | dict[str, Any] = (
            redact_secrets(detail)
        )
    else:
        raw = json.dumps(
            detail,
            ensure_ascii=False,
        )
        safe_detail = json.loads(
            redact_secrets(raw)
        )

    with _LOG_WRITE_LOCK:
        record = {
            "time": now_iso(),
            "file": redact_secrets(filename),
            "status": status,
            "detail": safe_detail,
        }
        serialized = json.dumps(
            record,
            ensure_ascii=False,
        ) + "\n"
        log_path = log_dir / "batch.jsonl"

        with log_path.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(serialized)


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rag-cleaner",
        description="Markdown 批量清洗工具",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过启动确认",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="跳过启动确认",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只生成处理计划，不调用 API",
    )
    parser.add_argument(
        "--selection-file",
        default="",
        help=(
            "UTF-8 JSON 选择清单；"
            "相对路径按项目根目录解析"
        ),
    )
    parser.add_argument(
        "--resume-batch",
        nargs="?",
        const="latest",
        default="",
        metavar="BATCH_ID",
        help=(
            "恢复 pending/interrupted 文件；"
            "省略 batch ID 时使用 latest"
        ),
    )
    parser.add_argument(
        "--retry-failed",
        nargs="?",
        const="latest",
        default="",
        metavar="BATCH_ID",
        help=(
            "重试父批次中的 failed 文件；"
            "省略 batch ID 时使用 latest"
        ),
    )
    parser.add_argument(
        "--batch-status",
        action="store_true",
        help="显示 latest 批次的只读状态摘要",
    )

    strict_group = (
        parser.add_mutually_exclusive_group()
    )

    strict_group.add_argument(
        "--strict",
        action="store_true",
        help="要求第一个分片包含完整 YAML Front Matter",
    )
    strict_group.add_argument(
        "--no-strict",
        action="store_true",
        help="不强制要求 YAML Front Matter",
    )

    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="本次最多处理多少个文件；0 表示不限制",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="同时处理的文件数，范围 1-5，默认 1",
    )
    parser.add_argument(
        "--pause-between-files",
        type=float,
        default=0,
        help="文件之间暂停秒数",
    )
    parser.add_argument(
        "--pause-between-chunks",
        type=float,
        default=0,
        help="分片之间暂停秒数",
    )
    parser.add_argument(
        "--pause-after-files",
        type=int,
        default=0,
        help="每处理多少个文件后等待 Enter",
    )
    parser.add_argument(
        "--pause-file",
        default="pause.flag",
        help="暂停标记文件名",
    )
    parser.add_argument(
        "--stop-file",
        default="stop.flag",
        help="停止标记文件名",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=MAX_CHARS,
        help=f"单片最大字符数，默认 {MAX_CHARS}",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=DEFAULT_MAX_FILE_SIZE,
        help=(
            "单个输入文件最大字节数，"
            f"默认 {DEFAULT_MAX_FILE_SIZE}"
        ),
    )
    parser.add_argument(
        "--force-unlock",
        action="store_true",
        help="仅在确认没有其他实例时清理失效锁",
    )
    parser.add_argument(
        "--base-dir",
        default="",
        help=(
            "项目根目录；默认当前工作目录，"
            "或环境变量 RAG_CLEANER_HOME"
        ),
    )

    return parser.parse_args(argv)


def validate_args(
    args: argparse.Namespace,
) -> None:
    resume_batch = getattr(
        args,
        "resume_batch",
        "",
    )
    retry_failed = getattr(
        args,
        "retry_failed",
        "",
    )
    batch_status = getattr(
        args,
        "batch_status",
        False,
    )

    if batch_status and any(
        (
            resume_batch,
            retry_failed,
            getattr(args, "selection_file", ""),
            getattr(args, "dry_run", False),
        )
    ):
        raise RuntimeError(
            "--batch-status 不能与处理、恢复、重试或 dry-run 模式同时使用"
        )

    if (
        retry_failed
        and retry_failed != "latest"
        and not BATCH_ID_PATTERN.fullmatch(retry_failed)
    ):
        raise RuntimeError(
            f"无效 batch ID：{retry_failed!r}"
        )

    if retry_failed and resume_batch:
        raise RuntimeError(
            "--retry-failed 不能与 --resume-batch 同时使用"
        )

    if retry_failed and getattr(
        args,
        "selection_file",
        "",
    ):
        raise RuntimeError(
            "--retry-failed 不能与 --selection-file 同时使用"
        )

    if retry_failed and getattr(
        args,
        "dry_run",
        False,
    ):
        raise RuntimeError(
            "--retry-failed 不能与 --dry-run 同时使用"
        )

    if resume_batch and getattr(
        args,
        "selection_file",
        "",
    ):
        raise RuntimeError(
            "--resume-batch 不能与 --selection-file 同时使用"
        )

    if resume_batch and getattr(
        args,
        "dry_run",
        False,
    ):
        raise RuntimeError(
            "--resume-batch 不能与 --dry-run 同时使用"
        )

    workers = getattr(args, "workers", 1)
    if not MIN_WORKERS <= workers <= MAX_WORKERS:
        raise RuntimeError("--workers 必须是 1 到 5 之间的整数")

    if workers > 1 and getattr(args, "dry_run", False):
        raise RuntimeError("--workers 大于 1 时不能使用 --dry-run")

    if workers > 1 and args.pause_after_files > 0:
        raise RuntimeError(
            "--workers 大于 1 时不能使用 --pause-after-files"
        )

    if workers > 1 and args.pause_between_files > 0:
        raise RuntimeError(
            "--workers 大于 1 时不能使用 --pause-between-files"
        )

    non_negative_values = {
        "--max-files": args.max_files,
        "--pause-between-files": (
            args.pause_between_files
        ),
        "--pause-between-chunks": (
            args.pause_between_chunks
        ),
        "--pause-after-files": (
            args.pause_after_files
        ),
    }

    for name, value in non_negative_values.items():
        if value < 0:
            raise RuntimeError(
                f"{name} 不能小于 0"
            )

    if args.max_chars <= 0:
        raise RuntimeError(
            "--max-chars 必须大于 0"
        )

    if args.max_file_size <= 0:
        raise RuntimeError(
            "--max-file-size 必须大于 0"
        )

    if args.max_chars > 1_000_000:
        raise RuntimeError(
            "--max-chars 不能超过 1000000"
        )

    if args.pause_file.strip() == "":
        raise RuntimeError(
            "--pause-file 不能为空"
        )

    if args.stop_file.strip() == "":
        raise RuntimeError(
            "--stop-file 不能为空"
        )


def load_runtime_config(
    args: argparse.Namespace,
) -> RuntimeConfig:
    if getattr(args, "base_dir", ""):
        base_dir = (
            Path(args.base_dir)
            .expanduser()
            .resolve()
        )
    else:
        base_dir = get_base_dir()

    paths = get_paths(base_dir)

    # 项目目录中的 .env 是本项目配置。
    # override=True 避免当前 Windows 环境变量串到其他项目。
    load_dotenv(
        paths["env_file"],
        override=True,
    )

    api_key = os.getenv(
        "OPENAI_API_KEY",
        "",
    ).strip()

    raw_base_url = os.getenv(
        "OPENAI_BASE_URL",
        "",
    ).strip()

    base_url = normalize_base_url(
        raw_base_url
    )

    model = os.getenv(
        "OPENAI_MODEL",
        "",
    ).strip()

    if not args.dry_run:
        if not api_key:
            raise RuntimeError(
                ".env 中缺少 OPENAI_API_KEY："
                f"{paths['env_file']}"
            )

        if not base_url:
            raise RuntimeError(
                ".env 中缺少 OPENAI_BASE_URL："
                f"{paths['env_file']}"
            )

        if not model:
            raise RuntimeError(
                ".env 中缺少 OPENAI_MODEL："
                f"{paths['env_file']}"
            )

    if not paths["prompt_file"].is_file():
        raise RuntimeError(
            f"找不到提示词文件："
            f"{paths['prompt_file']}"
        )

    if args.strict:
        strict_validation = True
    elif args.no_strict:
        strict_validation = False
    else:
        strict_validation = (
            DEFAULT_STRICT_VALIDATION
        )

    system_prompt = strip_outer_code_fence(
        read_text(paths["prompt_file"])
    )

    if not system_prompt:
        raise RuntimeError(
            f"提示词文件为空："
            f"{paths['prompt_file']}"
        )

    pause_file = resolve_control_file(
        base_dir,
        args.pause_file,
        "--pause-file",
    )

    stop_file = resolve_control_file(
        base_dir,
        args.stop_file,
        "--stop-file",
    )

    return RuntimeConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system_prompt=system_prompt,
        prompt_sha256=sha256_text(
            system_prompt
        ),
        strict_validation=strict_validation,
        max_chars=args.max_chars,
        max_file_size=args.max_file_size,
        pause_file=pause_file,
        stop_file=stop_file,
        base_dir=base_dir,
        input_dir=paths["input_dir"],
        output_dir=paths["output_dir"],
        log_dir=paths["log_dir"],
        lock_file=paths["lock_file"],
        pause_between_files=(
            args.pause_between_files
        ),
        pause_between_chunks=(
            args.pause_between_chunks
        ),
        pause_after_files=(
            args.pause_after_files
        ),
        max_files=args.max_files,
        dry_run=bool(args.dry_run),
        workers=getattr(args, "workers", 1),
    )
