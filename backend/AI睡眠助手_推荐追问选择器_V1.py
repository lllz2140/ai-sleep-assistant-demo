#!/usr/bin/env python3
"""AI睡眠助手：深睡推荐追问选择器 V1.0。

职责：
- 读取“规则控制层 V2.2”的结构化输出；
- 根据数据状态选择一个已审核的推荐追问 ID，或返回 null；
- 不重新计算个人基线，不生成个性化解读，不调用大模型；
- 不要求每一种数据组合都必须映射到问题。

默认运行：
    python AI睡眠助手_推荐追问选择器_V1.py

指定输入与输出：
    python AI睡眠助手_推荐追问选择器_V1.py 规则输出.json -o 推荐追问选择结果.json

传入已经看过的问题：
    python AI睡眠助手_推荐追问选择器_V1.py \
        规则输出.json --seen DS01 DS15 -o 推荐追问选择结果.json

运行内置测试：
    python AI睡眠助手_推荐追问选择器_V1.py --self-test
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RULE_OUTPUT = SCRIPT_DIR / "AI睡眠助手_规则输出_V2.json"
DEFAULT_SELECTION_OUTPUT = SCRIPT_DIR / "AI睡眠助手_推荐追问选择结果_V1.json"

SELECTOR_VERSION = "QUESTION-SELECTOR-V1.0"
SUPPORTED_RULE_VERSION = "SLEEP-CONTROL-V2.2"


# Demo 首版只启用三个经过审核的主动推荐意图。
# 其他问题仍可保留在完整意图库中，但不会自动进入推荐结果。
ACTIVE_QUESTION_CATALOG: Dict[str, Dict[str, str]] = {
    "DS01": {
        "intent": "understand_optimal_amount",
        "text": "深睡是不是越多越好？",
        "context_source": "data_only",
    },
    "DS15": {
        "intent": "understand_repeated_change_implication",
        "text": "深睡近期多次低于个人水平，就说明睡眠质量变差了吗？",
        "context_source": "data_only",
    },
    "DS21": {
        "intent": "understand_single_night_implication",
        "text": "单晚深睡偏低，为什么不一定意味着这一晚的睡眠质量变差了？",
        "context_source": "data_only",
    },
}


def read_json(path: Path) -> Dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"找不到输入文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON格式错误：{path}（第{exc.lineno}行，第{exc.colno}列）"
        ) from exc

    if not isinstance(document, dict):
        raise ValueError("输入JSON的最外层必须是对象")
    return document


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def extract_grounding_facts(rule_output: Dict[str, Any]) -> Dict[str, Any]:
    """提取选择器需要的事实，同时兼容完整规则输出和单独事实对象。"""
    llm_context = rule_output.get("llm_context")
    if isinstance(llm_context, dict):
        facts = llm_context.get("grounding_facts")
        if isinstance(facts, dict):
            return facts

    facts = rule_output.get("grounding_facts")
    if isinstance(facts, dict):
        return facts

    metric_signal = rule_output.get("metric_signal")
    if isinstance(metric_signal, dict):
        return {
            "metric_id": metric_signal.get("metric_id"),
            "sleep_date": rule_output.get("sleep_date"),
            "current_value": metric_signal.get("current_value", {}),
            "personal_baseline": metric_signal.get("personal_baseline", {}),
            "range_position": metric_signal.get("range_position", {}),
            "change": metric_signal.get("change", {}),
            "persistence": metric_signal.get("persistence", {}),
            "data_quality": rule_output.get("data_quality", {}),
            "subjective_context": rule_output.get("subjective_context", {}),
        }

    # 便于内置测试或独立调用：允许直接传入事实字段。
    if "range_position" in rule_output:
        return rule_output

    raise ValueError(
        "输入中找不到 llm_context.grounding_facts、grounding_facts 或 metric_signal"
    )


def validate_rule_version(rule_output: Dict[str, Any]) -> None:
    contract = rule_output.get("contract")
    if not isinstance(contract, dict):
        return

    rule_version = contract.get("rule_version")
    if rule_version and rule_version != SUPPORTED_RULE_VERSION:
        raise ValueError(
            f"选择器要求 {SUPPORTED_RULE_VERSION}，当前输入为 {rule_version}"
        )


def make_null_selection(reason_code: str) -> Dict[str, Any]:
    return {
        "selected": False,
        "question_id": None,
        "intent": None,
        "text": None,
        "context_source": None,
        "reason_code": reason_code,
    }


def make_question_selection(question_id: str, reason_code: str) -> Dict[str, Any]:
    item = ACTIVE_QUESTION_CATALOG[question_id]
    return {
        "selected": True,
        "question_id": question_id,
        "intent": item["intent"],
        "text": item["text"],
        "context_source": item["context_source"],
        "reason_code": reason_code,
    }


def select_recommended_question(
    rule_output: Dict[str, Any],
    seen_question_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """选择一个推荐追问，无法匹配时明确返回 null。

    选择顺序：
    1. 数据不可用、基线不足或主观安全信号需要优先处理时，不推荐问题；
    2. 时长和占比均低于个人范围且近期重复出现，选择 DS15；
    3. 时长和占比均低于个人范围且仅单晚/偶发，选择 DS21；
    4. 时长和占比均处于个人范围，或均高于个人范围时，可选择 DS01；
    5. 其他组合返回 null。

    已经看过的结果型问题不会自动降级为无关的教育问题，而是返回 null。
    """
    validate_rule_version(rule_output)
    facts = extract_grounding_facts(rule_output)
    seen: Set[str] = set(seen_question_ids or [])

    if facts.get("metric_id") not in (None, "deep_sleep"):
        return make_null_selection("unsupported_metric")

    data_quality = facts.get("data_quality") or {}
    if data_quality.get("usable") is not True:
        return make_null_selection("data_unusable")

    subjective = facts.get("subjective_context") or {}
    if subjective.get("needs_attention_from_subjective_feedback") is True:
        return make_null_selection("subjective_or_safety_attention_has_priority")

    baseline = facts.get("personal_baseline") or {}
    if baseline.get("available") is not True:
        return make_null_selection("personal_baseline_unavailable")

    range_position = facts.get("range_position") or {}
    duration_position = range_position.get("duration")
    ratio_position = range_position.get("ratio")

    change = facts.get("change") or {}
    change_level = change.get("level")

    persistence = facts.get("persistence") or {}
    persistence_status = persistence.get("status")

    both_below = (
        duration_position == "below_typical_range"
        and ratio_position == "below_typical_range"
    )
    both_within = (
        duration_position == "within_typical_range"
        and ratio_position == "within_typical_range"
    )
    both_above = (
        duration_position == "above_typical_range"
        and ratio_position == "above_typical_range"
    )

    if both_below and change_level == "notable" and persistence_status == "repeated":
        if "DS15" in seen:
            return make_null_selection("matched_ds15_but_already_seen")
        return make_question_selection("DS15", "repeated_below_personal_range")

    if (
        both_below
        and change_level == "notable"
        and persistence_status == "single_or_occasional"
    ):
        if "DS21" in seen:
            return make_null_selection("matched_ds21_but_already_seen")
        return make_question_selection("DS21", "single_night_below_personal_range")

    if both_within or both_above:
        if "DS01" in seen:
            return make_null_selection("matched_ds01_but_already_seen")
        reason = (
            "metric_education_within_personal_range"
            if both_within
            else "metric_education_above_personal_range"
        )
        return make_question_selection("DS01", reason)

    return make_null_selection("no_high_value_active_question_match")


def build_selection_output(
    rule_output: Dict[str, Any],
    seen_question_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    facts = extract_grounding_facts(rule_output)
    selection = select_recommended_question(rule_output, seen_question_ids)
    return {
        "contract": {
            "name": "AI睡眠助手推荐追问选择器",
            "selector_version": SELECTOR_VERSION,
            "supported_rule_version": SUPPORTED_RULE_VERSION,
            "selection_policy": "sparse_match_allow_null",
        },
        "request_id": rule_output.get("request_id"),
        "sleep_date": facts.get("sleep_date"),
        "metric_id": facts.get("metric_id"),
        "seen_question_ids": sorted(set(seen_question_ids or [])),
        "question_selection": selection,
    }


def test_facts(
    duration: str,
    ratio: str,
    *,
    persistence: str = "single_or_occasional",
    change_level: str = "typical_variation",
    usable: bool = True,
    baseline_available: bool = True,
    needs_attention: bool = False,
) -> Dict[str, Any]:
    return {
        "metric_id": "deep_sleep",
        "sleep_date": "2026-10-08",
        "personal_baseline": {"available": baseline_available},
        "range_position": {"duration": duration, "ratio": ratio},
        "change": {"level": change_level},
        "persistence": {"status": persistence},
        "data_quality": {"usable": usable},
        "subjective_context": {
            "needs_attention_from_subjective_feedback": needs_attention
        },
    }


def run_self_test() -> None:
    cases = [
        (
            "典型范围触发DS01",
            test_facts("within_typical_range", "within_typical_range"),
            set(),
            "DS01",
        ),
        (
            "单晚明显偏低触发DS21",
            test_facts(
                "below_typical_range",
                "below_typical_range",
                change_level="notable",
            ),
            set(),
            "DS21",
        ),
        (
            "近期重复偏低触发DS15",
            test_facts(
                "below_typical_range",
                "below_typical_range",
                persistence="repeated",
                change_level="notable",
            ),
            set(),
            "DS15",
        ),
        (
            "时长偏低但比例稳定返回null",
            test_facts("below_typical_range", "within_typical_range"),
            set(),
            None,
        ),
        (
            "比例偏低但时长稳定返回null",
            test_facts("within_typical_range", "below_typical_range"),
            set(),
            None,
        ),
        (
            "个人基线不足返回null",
            test_facts(
                "below_typical_range",
                "below_typical_range",
                change_level="notable",
                baseline_available=False,
            ),
            set(),
            None,
        ),
        (
            "数据不可用返回null",
            test_facts(
                "below_typical_range",
                "below_typical_range",
                change_level="notable",
                usable=False,
            ),
            set(),
            None,
        ),
        (
            "主观或安全影响优先时返回null",
            test_facts(
                "below_typical_range",
                "below_typical_range",
                persistence="repeated",
                change_level="notable",
                needs_attention=True,
            ),
            set(),
            None,
        ),
        (
            "已看过DS15后不降级推荐其他问题",
            test_facts(
                "below_typical_range",
                "below_typical_range",
                persistence="repeated",
                change_level="notable",
            ),
            {"DS15"},
            None,
        ),
    ]

    failures = []
    for name, facts, seen, expected_id in cases:
        actual = select_recommended_question(facts, seen).get("question_id")
        if actual != expected_id:
            failures.append(
                {"name": name, "expected": expected_id, "actual": actual}
            )

    if failures:
        raise AssertionError(
            "内置测试失败：\n" + json.dumps(failures, ensure_ascii=False, indent=2)
        )

    print(f"PASS：{len(cases)}/{len(cases)} 个推荐追问选择测试通过")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="选择深睡指标卡推荐追问")
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=DEFAULT_RULE_OUTPUT,
        help=f"规则控制层输出JSON，默认：{DEFAULT_RULE_OUTPUT.name}",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_SELECTION_OUTPUT,
        help=f"选择结果JSON，默认：{DEFAULT_SELECTION_OUTPUT.name}",
    )
    parser.add_argument(
        "--seen",
        nargs="*",
        default=[],
        metavar="QUESTION_ID",
        help="用户已经看过的问题ID，例如：--seen DS01 DS15",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="运行内置测试后退出",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        run_self_test()
        return

    rule_output = read_json(args.input)
    result = build_selection_output(rule_output, args.seen)
    write_json(args.output, result)
    print(f"已生成：{args.output}")
    print(
        json.dumps(
            result["question_selection"],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
