"""结果存档（T2.2）：超过阈值的原始结果落盘，上下文里只留摘要与存档编号。

<h3>与 `tool_contract` 的字符裁剪是什么关系</h3>
它们是**互补**的两件事，不要混为一谈：

- **裁剪**（`to_tool_content`）解决"上下文放不下" —— 代价是内容真的丢了；
- **存档**（本模块）解决"丢了就查不到" —— 原文还在磁盘上，只是不进上下文。

外部数据源尤其需要后者：一份 200 条的快讯裁剪之后只剩尾巴，
而"这条消息到底怎么写的"往往正是排查/复盘时要看的东西。

<h3>诚实说明：存档编号现在给谁用</h3>
模型手里**没有读文件的工具**（代码执行模式是 T4 的事），所以别把存档说成
"模型可以继续读全文"。今天的用途是三个：

1. 审计与复盘：`meta.offload_id` 会进账本，人和运维能顺着编号取回原文；
2. 前端/接口可查：`GET /api/v1/external/archive/{id}` 直接返回原文；
3. 给 T4 留好接口：代码执行模式下，模型拿到编号就能自己读。

写盘失败**绝不影响**工具结果：失败时降级成"只裁剪、不存档"，并留下日志。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from agent.external_source import OFFLOAD_THRESHOLD_CHARS

logger = logging.getLogger(__name__)

# 存档目录：默认在 python-data-service 下，**不依赖 CWD**（服务被从别的目录启动也找得到）
DEFAULT_DIR = Path(__file__).resolve().parents[1] / ".external_offload"

# 上下文里给摘要留的字符预算（只用来说明"存了什么"，不是给全文）
INLINE_SUMMARY_CHARS = 1800
# 保留最近多少个存档文件（超出按时间淘汰，避免磁盘无限增长）
KEEP_FILES = 200
# 存档编号的形状：tool/hash —— 严格校验，杜绝路径穿越
ID_RE = re.compile(r"^[a-z0-9_]+/[0-9a-f]{16}$")


def archive_dir() -> Path:
    override = os.getenv("EXTERNAL_OFFLOAD_DIR", "").strip()
    return Path(override) if override else DEFAULT_DIR


def _tool_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", str(name or "tool").lower()).strip("_")
    return slug or "tool"


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _count_rows(payload) -> int:
    if isinstance(payload, dict):
        return max([_count_rows(v) for v in payload.values()] or [0])
    if isinstance(payload, list):
        return len(payload)
    return 0


def needs_archive(payload, threshold: int = OFFLOAD_THRESHOLD_CHARS) -> bool:
    try:
        return len(_dump(payload)) > threshold
    except Exception:  # noqa: BLE001 —— 序列化不了就不存档，别把结果搞崩
        return False


def store(tool_name: str, payload, note: str = "", rows: Optional[int] = None) -> Optional[dict]:
    """把完整结果写到磁盘，返回存档信息（写失败返回 None）。"""
    try:
        text = _dump(payload)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        offload_id = f"{_tool_slug(tool_name)}/{digest}"
        directory = archive_dir() / _tool_slug(tool_name)
        directory.mkdir(parents=True, exist_ok=True)
        record = {
            "offload_id": offload_id,
            "tool": tool_name,
            "created_at": _stamp(),
            "chars": len(text),
            "rows": rows if rows is not None else _count_rows(payload),
            "note": note,
            "payload": payload,
        }
        path = directory / f"{digest}.json"
        # 先写临时文件再改名：进程被中断时不会留下半个 JSON（读取端也不会读到残缺内容）
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)
        prune()
        return {key: record[key] for key in ("offload_id", "created_at", "chars", "rows")} | {
            "path": str(path)}
    except Exception as exc:  # noqa: BLE001 —— 存档是增强，不许影响工具结果
        logger.warning("结果存档失败 tool=%s: %s", tool_name, exc)
        return None


def load(offload_id: str) -> Optional[dict]:
    """按编号取回存档（编号非法或文件不存在都返回 None，绝不抛异常）。"""
    if not isinstance(offload_id, str) or not ID_RE.match(offload_id):
        return None
    path = archive_dir() / f"{offload_id}.json"
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取存档失败 id=%s: %s", offload_id, exc)
        return None


def prune(keep: int = KEEP_FILES) -> int:
    """只保留最近 keep 个存档文件。返回删除数量。"""
    directory = archive_dir()
    if not directory.is_dir():
        return 0
    try:
        files = sorted((p for p in directory.rglob("*.json")), key=lambda p: p.stat().st_mtime,
                       reverse=True)
    except Exception:  # noqa: BLE001
        return 0
    removed = 0
    for path in files[keep:]:
        try:
            path.unlink()
            removed += 1
        except Exception:  # noqa: BLE001
            continue
    return removed


def slim(payload, max_chars: int = INLINE_SUMMARY_CHARS):
    """把负载压成"能给模型看的那一份"：列表只留**最新**的若干条。

    为什么留最新的：新闻/快讯按时间升序排列（取数层已统一排序），
    尾部即最新；而裁剪逻辑（`tool_contract._keep_tail`）也是留尾部，
    两处语义一致，模型看到的就是"最近的 N 条"。
    """
    if isinstance(payload, dict):
        result: dict = {}
        omitted: dict = {}
        for key, value in payload.items():
            if isinstance(value, list) and len(value) > 1:
                kept: list = []
                used = 0
                for item in reversed(value):
                    size = len(_dump(item)) + 1
                    if kept and used + size > max_chars:
                        break
                    kept.append(item)
                    used += size
                kept.reverse()
                result[key] = kept
                if len(kept) < len(value):
                    omitted[key] = len(value) - len(kept)
            else:
                result[key] = value
        if omitted:
            result["omitted"] = omitted
        return result
    if isinstance(payload, list):
        return slim({"items": payload}, max_chars=max_chars)["items"]
    return payload


def archive_if_large(tool_name: str, payload, note: str = ""):
    """统一入口：超阈值就存档并返回 (瘦身后的负载, 存档信息 or None)。"""
    if not needs_archive(payload):
        return payload, None
    rows = _count_rows(payload)
    info = store(tool_name, payload, note=note, rows=rows)
    if info is None:
        return payload, None          # 没存成：原样返回，交给字符裁剪兜底
    from agent.external_source import REGISTRY

    REGISTRY.count_offload()
    return slim(payload), info


def _stamp() -> str:
    try:
        from agent import timeutil

        return timeutil.now_str()
    except Exception:  # noqa: BLE001
        return time.strftime("%Y-%m-%d %H:%M:%S")
