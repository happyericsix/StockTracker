# -*- coding: utf-8 -*-
"""run_regression.py —— 提示词/上下文/路由改动的统一回归门禁（2026-10-18）。

为什么需要它：API 模式下我们改不了权重，"优化模型"的每一次动作都落在提示词、
证据包、输出契约、模型路由上——这些改动的**验收**必须有一键可跑的回归，否则
就是盲改。本脚本聚合三件已有的评测设施，各自原有口径不变，只做编排与判分：

  scenario  场景实验室（scenario_lab.py）     10 个极端场景真调 LLM，
            退出码即门禁（异常/降级/解析失败为红）
  domain    领域金标集（finetune/eval_domain.py）
            schema 合法率 ≥95%、拒答率 ≥90%、校准单调；
            方向准确率对比 --baseline 报告最多允许掉 3pt
  tools     工具选择评测（tests/tool_eval/runner.py）
            退出码即门禁（选择 ≥0.75 / 参数 ≥0.9 / 语义 ≥0.8 / 0 假调用）

用法（项目根 = python-data-service/）：
    python scripts/run_regression.py                     # 全跑（真调 LLM，花 token）
    python scripts/run_regression.py --skip domain,tools # 只跑场景实验室
    python scripts/run_regression.py --baseline finetune/eval_report.json

注意：三套全部真调模型（这是刻意的——提示词改动的回归不能用替身）。
LLM 不可用时 scenario/tools 会按各自内部的降级路径处理；门禁结果如实反映。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 与 finetune/eval_domain.py 的 GATES 同口径（复制而不是 import：那是个脚本，
# import 它会触发 argparse 之外的模块级副作用，且门槛值该在门禁处独立可读）
DOMAIN_GATES = {"schema_valid_rate": 0.95, "refusal_rate": 0.90,
                "direction_accuracy_floor_delta": -0.03}


def _run(cmd: list[str]) -> tuple[int, str]:
    """跑子进程：UTF-8 收敛（Windows 控制台默认 GBK，符号会打崩），流式透传输出。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
    return proc.returncode, ""


def _check_domain(report_path: Path, baseline_path: Path | None) -> tuple[bool, list[str]]:
    """领域金标集判分：eval_domain 的 main() 恒返回 0（它把对比留给人），判分在这里做。"""
    failures: list[str] = []
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, [f"报告读不出来（{report_path}）: {exc}"]
    summary = report.get("summary") or {}
    schema_rate = summary.get("schema_rate")
    if schema_rate is None or schema_rate < DOMAIN_GATES["schema_valid_rate"]:
        failures.append(f"schema 合法率 {schema_rate} < {DOMAIN_GATES['schema_valid_rate']}")
    refusal = summary.get("refusal_rate")
    if refusal is not None and refusal < DOMAIN_GATES["refusal_rate"]:
        failures.append(f"拒答率 {refusal} < {DOMAIN_GATES['refusal_rate']}")
    if summary.get("calibration_ok") is False:
        failures.append("校准单调性被破坏（高置信桶准确率低于低置信桶）")
    acc = summary.get("direction_acc")
    if baseline_path and baseline_path.exists():
        try:
            base = json.loads(baseline_path.read_text(encoding="utf-8"))
            base_acc = (base.get("summary") or {}).get("direction_acc")
            if acc is not None and base_acc is not None:
                delta = acc - base_acc
                if delta < DOMAIN_GATES["direction_accuracy_floor_delta"]:
                    failures.append(
                        f"方向准确率 {acc:.3f} 比基线 {base_acc:.3f} 掉了 {abs(delta):.3f}"
                        f"（允许 {abs(DOMAIN_GATES['direction_accuracy_floor_delta']):.2f}）")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"基线报告读不出来（{baseline_path}）: {exc}")
    elif baseline_path:
        failures.append(f"指定的基线报告不存在：{baseline_path}")
    return not failures, failures


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description="提示词/上下文/路由改动回归门禁")
    parser.add_argument("--skip", default="", help="逗号分隔：scenario,domain,tools")
    parser.add_argument("--baseline", default="",
                        help="eval_domain 的基线报告（JSON），方向准确率与之对比")
    parser.add_argument("--only", default="", help="透传给 scenario_lab 的 --only（场景子集）")
    args = parser.parse_args()
    skip = {x.strip() for x in args.skip.split(",") if x.strip()}

    suites: list[tuple[str, bool, list[str]]] = []   # (名字, 是否过, 失败清单)

    if "scenario" not in skip:
        cmd = [sys.executable, "scenario_lab.py"]
        if args.only:
            cmd += ["--only", args.only]
        code, _ = _run(cmd)
        suites.append(("scenario(场景实验室)", code == 0,
                       [] if code == 0 else ["存在异常/降级/解析失败的场景（见上方输出）"]))

    if "domain" not in skip:
        report = ROOT / "finetune" / "eval_report_regression.json"
        code, _ = _run([sys.executable, "finetune/eval_domain.py",
                        "--save", str(report)])
        if code != 0:
            suites.append(("domain(领域金标集)", False, [f"eval_domain 退出码 {code}"]))
        else:
            ok, failures = _check_domain(report, Path(args.baseline) if args.baseline else None)
            suites.append(("domain(领域金标集)", ok, failures))

    if "tools" not in skip:
        code, _ = _run([sys.executable, "tests/tool_eval/runner.py",
                        "--arm", "with",
                        "--json", str(ROOT / "tests" / "tool_eval" / "report_regression.json")])
        suites.append(("tools(工具选择评测)", code == 0,
                       [] if code == 0 else ["工具选择/参数/语义门槛未过（见上方输出）"]))

    print("\n" + "=" * 60)
    print("回归门禁汇总（prompt_version / role_system_version 见 /health 与痕迹）")
    print("=" * 60)
    all_ok = True
    for name, ok, failures in suites:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}")
        for failure in failures:
            print(f"         - {failure}")
        all_ok = all_ok and ok
    print("=" * 60)
    print("结论：全部通过" if all_ok else "结论：存在失败项——先修再合入")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
