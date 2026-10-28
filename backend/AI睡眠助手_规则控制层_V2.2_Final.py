#!/usr/bin/env python3
"""AI睡眠助手规则控制层 V2.2 Final（职责拆分冻结版）。

职责：
- 读取标准化睡眠数据；
- 建立近期个人基线；
- 识别值得解释的变化及其持续性；
- 输出页面展示优先级，以及供大模型使用的事实与表达边界。
- 为独立的个性化解读、推荐追问选择器和点击回答模块提供统一输入。

本脚本不负责选择 question_id、生成问题文本、检索知识、疾病诊断、
睡眠质量评分或病因推断。
脚本中的阈值是产品展示阈值，不是医学诊断阈值。

直接点击运行：读取同目录下的“AI睡眠助手_睡眠测试输入_案例01.json”，
并生成“AI睡眠助手_规则输出_V2.2_Final.json”。

指定文件运行：
python AI睡眠助手_规则控制层_V2.2_Final.py 输入.json -o 输出.json
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from statistics import median, quantiles
from typing import Any, Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "AI睡眠助手_睡眠测试输入_案例01.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "AI睡眠助手_规则输出_V2.2_Final.json"


RULE_CONFIG: Dict[str, Any] = {
    # 保持 V2.2 规则契约，确保与已冻结的 QUESTION-SELECTOR-V1.0 兼容。
    "contract_version": "2.2-final",
    "rule_version": "SLEEP-CONTROL-V2.2",
    "threshold_meaning": "product_display_threshold_not_medical_diagnosis",
    "baseline": {
        "minimum_valid_nights": 7,
        "window_days": 14,
    },
    "change_detection": {
        "duration_relative_threshold": 0.20,
        "duration_absolute_threshold_minutes": 15,
        "ratio_relative_threshold": 0.20,
        "ratio_absolute_threshold_points": 0.03,
    },
    "persistence": {
        "observation_valid_nights": 4,
        "required_notable_nights": 3,
    },
}


METRIC_POLICY: Dict[str, Any] = {
    "metric_id": "deep_sleep",
    "fixed_page_messages": [
        "可穿戴设备的睡眠分期属于算法估算",
    ],
    "prohibited_claims": [
        "诊断深睡不足或其他睡眠疾病",
        "仅凭深睡指标判断整体睡眠质量或疾病",
        "在用户没有提供信息时推断压力、饮酒、咖啡、运动或疾病等原因",
        "声称增加深睡一定能改善健康",
        "根据单一设备指标给出治疗建议",
    ],
}


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def round_or_none(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


def percentile_inc(values: List[float]) -> Tuple[float, float]:
    if len(values) == 1:
        return values[0], values[0]
    q1, _, q3 = quantiles(values, n=4, method="inclusive")
    return q1, q3


def normalise_night(raw_night: Dict[str, Any]) -> Dict[str, Any]:
    """校验并标准化一晚数据，不接受外部预判结果。"""
    night = deepcopy(raw_night)
    total = night.get("total_sleep_minutes")
    deep = night.get("deep_sleep_minutes")
    ratio = night.get("deep_sleep_ratio")

    if (
        ratio is None
        and isinstance(total, (int, float))
        and total > 0
        and isinstance(deep, (int, float))
    ):
        ratio = deep / total

    calculated_valid = (
        parse_date(night.get("sleep_date")) is not None
        and night.get("stage_data_status", "complete") == "complete"
        and isinstance(total, (int, float))
        and total > 0
        and isinstance(deep, (int, float))
        and deep >= 0
        and isinstance(ratio, (int, float))
        and 0 <= ratio <= 1
    )
    declared_valid = night.get("record_valid", True)

    return {
        "sleep_date": night.get("sleep_date"),
        "source_id": night.get("source_id"),
        "stage_data_status": night.get("stage_data_status", "complete"),
        "record_valid": bool(declared_valid and calculated_valid),
        "total_sleep_minutes": total,
        "deep_sleep_minutes": deep,
        "deep_sleep_ratio": ratio,
    }


def validate_payload(document: Dict[str, Any]) -> Dict[str, Any]:
    required = ["request_id", "current_night", "history_nights"]
    missing = [key for key in required if key not in document]
    if missing:
        raise ValueError(f"输入文件缺少必需字段：{', '.join(missing)}")
    if not isinstance(document["current_night"], dict):
        raise ValueError("current_night 必须是对象")
    if not isinstance(document["history_nights"], list):
        raise ValueError("history_nights 必须是数组")

    return {
        "request_id": document["request_id"],
        "evaluated_at": document.get("evaluated_at"),
        "current_night": normalise_night(document["current_night"]),
        "history_nights": [normalise_night(row) for row in document["history_nights"]],
        "subjective_feedback": document.get("subjective_feedback") or {},
    }


def select_valid_history(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    current = payload["current_night"]
    current_date = parse_date(current["sleep_date"])
    source_id = current["source_id"]
    window_days = RULE_CONFIG["baseline"]["window_days"]
    result: List[Dict[str, Any]] = []

    for night in payload["history_nights"]:
        night_date = parse_date(night["sleep_date"])
        if not night["record_valid"] or night["source_id"] != source_id:
            continue
        if current_date is None or night_date is None:
            continue
        day_gap = (current_date - night_date).days
        if 1 <= day_gap <= window_days:
            result.append(night)

    return sorted(result, key=lambda row: row["sleep_date"])


def calculate_baseline(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    minimum = RULE_CONFIG["baseline"]["minimum_valid_nights"]
    if len(rows) < minimum:
        return {
            "available": False,
            "valid_nights": len(rows),
            "minimum_required_nights": minimum,
            "reason": "insufficient_valid_history",
        }

    durations = [float(row["deep_sleep_minutes"]) for row in rows]
    ratios = [float(row["deep_sleep_ratio"]) for row in rows]
    duration_q1, duration_q3 = percentile_inc(durations)
    ratio_q1, ratio_q3 = percentile_inc(ratios)

    return {
        "available": True,
        "valid_nights": len(rows),
        "window_start": rows[0]["sleep_date"],
        "window_end": rows[-1]["sleep_date"],
        "duration_median": median(durations),
        "duration_q1": duration_q1,
        "duration_q3": duration_q3,
        "ratio_median": median(ratios),
        "ratio_q1": ratio_q1,
        "ratio_q3": ratio_q3,
        "method": "median_and_percentile_inc",
    }


def classify_change(night: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    if not night["record_valid"] or not baseline.get("available"):
        return {
            "direction": "unknown",
            "level": "not_evaluated",
            "duration_notable": False,
            "ratio_notable": False,
        }

    duration = float(night["deep_sleep_minutes"])
    ratio = float(night["deep_sleep_ratio"])
    duration_median = float(baseline["duration_median"])
    ratio_median = float(baseline["ratio_median"])
    duration_drop = max(0.0, duration_median - duration)
    ratio_drop = max(0.0, ratio_median - ratio)
    duration_drop_rate = duration_drop / duration_median if duration_median > 0 else 0.0
    ratio_drop_rate = ratio_drop / ratio_median if ratio_median > 0 else 0.0
    config = RULE_CONFIG["change_detection"]

    duration_notable = (
        duration_drop >= config["duration_absolute_threshold_minutes"]
        and duration_drop_rate >= config["duration_relative_threshold"]
    )
    ratio_notable = (
        ratio_drop >= config["ratio_absolute_threshold_points"]
        and ratio_drop_rate >= config["ratio_relative_threshold"]
    )

    if duration_notable and ratio_notable:
        level = "notable"
    elif duration < baseline["duration_q1"] or ratio < baseline["ratio_q1"]:
        level = "mild"
    else:
        level = "typical_variation"

    if duration < duration_median or ratio < ratio_median:
        direction = "below_baseline"
    elif duration > duration_median or ratio > ratio_median:
        direction = "above_baseline"
    else:
        direction = "near_baseline"

    return {
        "direction": direction,
        "level": level,
        "duration_notable": duration_notable,
        "ratio_notable": ratio_notable,
        "duration_drop_minutes": duration_drop,
        "duration_drop_rate": duration_drop_rate,
        "ratio_drop_points": ratio_drop,
        "ratio_drop_rate": ratio_drop_rate,
    }


def classify_range_position(
    current: Dict[str, Any], baseline: Dict[str, Any]
) -> Dict[str, str]:
    """判断当前值在个人Q1～Q3典型区间中的位置，供页面和大模型直接使用。"""
    if not current["record_valid"] or not baseline.get("available"):
        return {"duration": "not_evaluated", "ratio": "not_evaluated"}

    def locate(value: float, q1: float, q3: float) -> str:
        if value < q1:
            return "below_typical_range"
        if value > q3:
            return "above_typical_range"
        return "within_typical_range"

    return {
        "duration": locate(
            float(current["deep_sleep_minutes"]),
            float(baseline["duration_q1"]),
            float(baseline["duration_q3"]),
        ),
        "ratio": locate(
            float(current["deep_sleep_ratio"]),
            float(baseline["ratio_q1"]),
            float(baseline["ratio_q3"]),
        ),
    }


def calculate_persistence(
    history: List[Dict[str, Any]],
    current: Dict[str, Any],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    config = RULE_CONFIG["persistence"]
    recent = (history + [current])[-config["observation_valid_nights"] :]
    notable_dates = [
        row["sleep_date"]
        for row in recent
        if classify_change(row, baseline)["level"] == "notable"
    ]

    if len(recent) < config["observation_valid_nights"]:
        status = "insufficient_observation"
    elif len(notable_dates) >= config["required_notable_nights"]:
        status = "repeated"
    elif notable_dates:
        status = "single_or_occasional"
    else:
        status = "not_observed"

    return {
        "status": status,
        "matched_nights": len(notable_dates),
        "observed_nights": len(recent),
        "notable_dates": notable_dates,
        "method_note": "仅用于页面解释优先级，不代表医学异常",
    }


def evaluate_subjective_feedback(feedback: Dict[str, Any]) -> Dict[str, Any]:
    daytime = feedback.get("daytime_sleepiness", "unknown")
    functional = feedback.get("functional_impact", "unknown")
    safety = feedback.get("safety_impact")
    available = any(value not in (None, "unknown") for value in (daytime, functional, safety))
    needs_attention = bool(safety is True or functional in {"severe", "high"})
    return {
        "available": available,
        "daytime_sleepiness": daytime,
        "functional_impact": functional,
        "safety_impact": safety,
        "needs_attention_from_subjective_feedback": needs_attention,
    }


def evaluate(document: Dict[str, Any]) -> Dict[str, Any]:
    payload = validate_payload(document)
    current = payload["current_night"]
    current_date = parse_date(current["sleep_date"])
    if current_date is None:
        raise ValueError("current_night.sleep_date 不是有效日期")

    history = select_valid_history(payload)
    baseline = calculate_baseline(history)
    change = classify_change(current, baseline)
    range_position = classify_range_position(current, baseline)
    persistence = (
        calculate_persistence(history, current, baseline)
        if baseline.get("available")
        else {"status": "not_evaluated", "matched_nights": 0, "observed_nights": 0, "notable_dates": []}
    )
    subjective = evaluate_subjective_feedback(payload["subjective_feedback"])

    usable = current["record_valid"]
    invalid_reasons: List[str] = []
    if not usable:
        invalid_reasons.append("current_night_stage_data_invalid")
    if not baseline.get("available"):
        invalid_reasons.append("personal_baseline_unavailable")

    if not usable:
        confidence = "unavailable"
        priority = "none"
        headline_key = "deep_sleep_data_unavailable"
    elif not baseline.get("available"):
        confidence = "low"
        priority = "educational"
        headline_key = "deep_sleep_baseline_building"
    elif baseline["valid_nights"] >= 14:
        confidence = "high"
        priority, headline_key = choose_presentation(change, persistence)
    else:
        confidence = "medium"
        priority, headline_key = choose_presentation(change, persistence)

    detail_state = (
        "data_unavailable"
        if not usable
        else "baseline_building"
        if not baseline.get("available")
        else "ready"
    )
    highlight_on_homepage = priority in {"primary", "secondary"}

    matched_rules = ["DATA-VALID-001" if usable else "DATA-INVALID-001"]
    matched_rules.append("BASELINE-AVAILABLE-001" if baseline.get("available") else "BASELINE-INSUFFICIENT-001")
    if change["level"] == "notable":
        matched_rules.append("CHANGE-NOTABLE-001")
    if persistence["status"] == "repeated":
        matched_rules.append("PERSISTENCE-REPEATED-001")
    if subjective["needs_attention_from_subjective_feedback"]:
        matched_rules.append("SUBJECTIVE-ATTENTION-001")

    return {
        "contract": {
            "name": "AI睡眠助手规则控制层",
            "contract_version": RULE_CONFIG["contract_version"],
            "rule_version": RULE_CONFIG["rule_version"],
            "purpose": "输出睡眠指标的结构化事实、展示优先级和表达边界",
            "threshold_meaning": RULE_CONFIG["threshold_meaning"],
        },
        "request_id": payload["request_id"],
        "sleep_date": current["sleep_date"],
        "data_quality": {
            "usable": usable,
            "confidence": confidence,
            "valid_baseline_nights": baseline.get("valid_nights", 0),
            "invalid_reasons": invalid_reasons,
            "limitations": ["consumer_wearable_sleep_stage_estimate"],
        },
        "metric_signal": {
            "metric_id": METRIC_POLICY["metric_id"],
            "current_value": {
                "minutes": current["deep_sleep_minutes"],
                "ratio": round_or_none(current["deep_sleep_ratio"]),
            },
            "personal_baseline": {
                "available": baseline.get("available", False),
                "duration_median": round_or_none(baseline.get("duration_median"), 1),
                "duration_q1": round_or_none(baseline.get("duration_q1"), 1),
                "duration_q3": round_or_none(baseline.get("duration_q3"), 1),
                "ratio_median": round_or_none(baseline.get("ratio_median")),
                "ratio_q1": round_or_none(baseline.get("ratio_q1")),
                "ratio_q3": round_or_none(baseline.get("ratio_q3")),
                "valid_nights": baseline.get("valid_nights", 0),
                "window_start": baseline.get("window_start"),
                "window_end": baseline.get("window_end"),
                "window_days": RULE_CONFIG["baseline"]["window_days"],
            },
            "range_position": range_position,
            "change": {
                "direction": change["direction"],
                "level": change["level"],
                "duration_notable": change.get("duration_notable", False),
                "ratio_notable": change.get("ratio_notable", False),
                "duration_drop_minutes": round_or_none(change.get("duration_drop_minutes"), 1),
                "duration_drop_rate": round_or_none(change.get("duration_drop_rate")),
                "ratio_drop_points": round_or_none(change.get("ratio_drop_points")),
                "ratio_drop_rate": round_or_none(change.get("ratio_drop_rate")),
            },
            "persistence": persistence,
        },
        "subjective_context": subjective,
        "presentation": {
            "detail_card_available": usable,
            "detail_state": detail_state,
            "highlight_on_homepage": highlight_on_homepage,
            "presentation_priority": priority,
            "signal_key": headline_key,
            "show_personal_typical_range": baseline.get("available", False),
            "show_population_reference_range": False,
        },
        "llm_context": {
            "grounding_facts": {
                "metric_id": METRIC_POLICY["metric_id"],
                "sleep_date": current["sleep_date"],
                "current_value": {
                    "minutes": current["deep_sleep_minutes"],
                    "ratio": round_or_none(current["deep_sleep_ratio"]),
                },
                "personal_baseline": {
                    "available": baseline.get("available", False),
                    "duration_median": round_or_none(baseline.get("duration_median"), 1),
                    "duration_q1": round_or_none(baseline.get("duration_q1"), 1),
                    "duration_q3": round_or_none(baseline.get("duration_q3"), 1),
                    "ratio_median": round_or_none(baseline.get("ratio_median")),
                    "ratio_q1": round_or_none(baseline.get("ratio_q1")),
                    "ratio_q3": round_or_none(baseline.get("ratio_q3")),
                    "valid_nights": baseline.get("valid_nights", 0),
                    "window_days": RULE_CONFIG["baseline"]["window_days"],
                },
                "range_position": range_position,
                "change": {
                    "direction": change["direction"],
                    "level": change["level"],
                    "duration_drop_minutes": round_or_none(change.get("duration_drop_minutes"), 1),
                    "duration_drop_rate": round_or_none(change.get("duration_drop_rate")),
                    "ratio_drop_points": round_or_none(change.get("ratio_drop_points")),
                    "ratio_drop_rate": round_or_none(change.get("ratio_drop_rate")),
                },
                "persistence": {
                    "status": persistence["status"],
                    "matched_nights": persistence["matched_nights"],
                    "observed_nights": persistence["observed_nights"],
                },
                "data_quality": {
                    "usable": usable,
                    "confidence": confidence,
                },
                "subjective_context": subjective,
            },
            "tasks": {
                "personalized_interpretation": {
                    "enabled": usable,
                    "max_sentences": 2,
                    "include_action_advice": False,
                }
            },
            "downstream_modules": {
                "recommended_question_selector": {
                    "owner": "QUESTION-SELECTOR-V1.0",
                    "input": "llm_context.grounding_facts",
                    "selection_mode": "fixed_question_id_mapping",
                    "max_count": 1,
                    "allow_null": True,
                    "model_generation_enabled": False,
                },
                "question_answer": {
                    "requires_user_click": True,
                    "knowledge_mode": "fixed_mapping_reviewed_knowledge",
                    "use_model_general_knowledge": False,
                    "max_knowledge_entries": 3,
                },
            },
            "fixed_page_messages": METRIC_POLICY["fixed_page_messages"],
            "subjective_attention_message": (
                "用户报告的日间功能或安全影响需要单独重视"
                if subjective["needs_attention_from_subjective_feedback"]
                else None
            ),
            "prohibited_claims": METRIC_POLICY["prohibited_claims"],
        },
        "audit": {
            "matched_rule_ids": matched_rules,
            "calculation_uses_unrounded_values": True,
            "display_rounding_applied_after_decision": True,
            "rule_config": RULE_CONFIG,
        },
    }


def choose_presentation(
    change: Dict[str, Any], persistence: Dict[str, Any]
) -> Tuple[str, str]:
    if change["level"] == "notable" and persistence["status"] == "repeated":
        return "primary", "deep_sleep_repeated_below_baseline"
    if change["level"] == "notable":
        return "secondary", "deep_sleep_single_below_baseline"
    return "none", "deep_sleep_no_notable_change"


def main() -> None:
    parser = argparse.ArgumentParser(description="运行AI睡眠助手规则控制层")
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT,
        help="标准化睡眠输入JSON；不填写时使用同目录默认测试输入",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="输出JSON；不填写时保存到同目录默认输出文件",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(
            f"找不到输入文件：{args.input}\n"
            "请把测试输入文件放在脚本同一目录，或在运行参数中指定输入文件。"
        )

    with args.input.open("r", encoding="utf-8") as file:
        document = json.load(file)
    result = evaluate(document)

    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"规则运行成功，输出文件：{args.output}")
    print(
        "结果摘要：",
        result["presentation"]["presentation_priority"],
        result["presentation"]["signal_key"],
    )


if __name__ == "__main__":
    main()
