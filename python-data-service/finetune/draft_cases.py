"""draft_cases.py —— 把人工审核金标集变成"填空题"。

金标集（cases.yaml）是微调上线门槛的地基，但"从零写出 30 条案例"的心理
摩擦太大，人永远开不了头。这个脚本把已解读的真实资讯拉下来，
**自动填好机器能填的字段，把人要做的压缩成两件事**：
  1. 填 `expect_direction`（利好/利空）或把 kind 改成 refusal
  2. 删掉自己不确定的条目（拿不准的金标比没有金标更危险）

用法（在 python-data-service/ 目录）：
    python finetune/draft_cases.py --count 30 --out finetune/cases.draft.yaml

产出是**草稿**：直接覆盖 cases.yaml 之前必须人工过一遍——脚本会在
文件头写上"未审核"警告，eval_domain.py 读到未填方向的方向型用例会报错拒绝，
防止半成品混进评估。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_dataset import fetch_export, load_env_if_present  # noqa: E402

HEADER = """# ⚠️ 金标草稿（机器起草，未人工审核）——不许直接当 cases.yaml 用
#
# 审核三步：
#   1. 每条填 expect_direction: 利好 / 利空（拿不准就整条删掉）
#   2. 无方向性影响的条目把 kind 改成 refusal（正确行为是"不判断方向"）
#   3. 删完把本警告块替换成 cases.yaml 原版的纪律说明，再覆盖过去
#
# 起草策略：按 source_level 分层抽样（公告/媒体/研报都要有），
# 标题里的传闻词用 ✍ 标出，帮你快速定位"该审仔细"的条目。
"""


def looks_like_rumor(title: str) -> bool:
    markers = ("据传", "传闻", "网传", "据悉", "知情人士", "或将", "被曝")
    return any(m in title for m in markers)


def draft_case(row: dict, index: int) -> list[str]:
    source = {1: "官方公告", 2: "媒体", 3: "研报", 4: "舆情"}.get(row.get("source_level"), "资讯")
    title = str(row.get("title") or "").replace('"', "'")
    content = str(row.get("content") or "")[:120].replace('"', "'")
    symbol = row.get("symbol") or "市场"
    analysis = row.get("analysis") or {}
    machine = str(analysis.get("direction") or "未给方向")
    rumor_mark = " ✍含传闻措辞" if looks_like_rumor(title) else ""
    lines = [
        f"# AI 当时判的是「{machine}」——你可能同意也可能不同意，以你的判断为准",
        f"# 可信度参考：{row.get('credibility_grade') or '未评'}{rumor_mark}",
        f"- id: draft-{index:03d}",
        "  kind: direction        # 方向型；无方向性影响就改成 refusal 并删掉下一行",
        '  expect_direction: ""   # ← 填 利好 / 利空',
        f'  symbol: "{symbol}"',
        f'  source: "{source}"',
        f'  title: "{title}"',
        f'  content: "{content}"',
    ]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="起草金标集草稿")
    parser.add_argument("--base", default=os.getenv("SPRING_BASE_URL", "http://localhost:8080"))
    parser.add_argument("--token", default=os.getenv("INTERNAL_API_TOKEN", ""))
    parser.add_argument("--count", type=int, default=30, help="起草多少条")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42, help="抽样种子（可复现）")
    parser.add_argument("--out", default=os.path.join(HERE, "cases.draft.yaml"))
    args = parser.parse_args()

    load_env_if_present()
    token = args.token or os.getenv("INTERNAL_API_TOKEN", "")
    if not token:
        print("✗ INTERNAL_API_TOKEN 未配置", file=sys.stderr)
        return 2

    rows = fetch_export(args.base, token, args.days, 5000)
    if not rows:
        print("✗ 导出为空：先让新闻管道跑一段时间攒已解读条目", file=sys.stderr)
        return 2

    # 分层抽样：每个 source_level 至少占 1/4 名额（不足时按实际数量），
    # 防止"30 条全是公告"——评估要量的是所有信源档位上的判断力
    random.seed(args.seed)
    by_level: dict[int, list] = {}
    for row in rows:
        by_level.setdefault(int(row.get("source_level") or 0), []).append(row)
    quota = max(1, args.count // max(1, len(by_level)))
    picked: list = []
    for level in sorted(by_level):
        pool = by_level[level][:]
        random.shuffle(pool)
        picked.extend(pool[:quota])
    random.shuffle(picked)
    picked = picked[:args.count]

    out = [HEADER]
    for index, row in enumerate(picked, start=1):
        out.extend(draft_case(row, index))
        out.append("")
    io.open(args.out, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    print(f"✓ 起草 {len(picked)} 条 → {args.out}")
    print("  下一步：人工填 expect_direction / 改 refusal / 删拿不准的，"
          "然后把警告头换成纪律说明后覆盖 cases.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
