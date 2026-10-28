#!/usr/bin/env python3
"""AI睡眠助手：深睡指标卡端到端编排 V1.1。

本脚本串联已经冻结的四个模块：
1. 规则控制层：计算个人基线、区间位置、偏离程度与重复变化；
2. 个性化解读 Prompt：把规则事实转成简短解读；
3. 推荐追问选择器：固定选择 DS01、DS21、DS15 或 null；
4. 点击回答模块：按 question_id 检索审核知识并生成回答。

支持三种模式：
- mock：无需 API Key，使用确定性文案完成本地联调；
- openai：通过 OpenAI Responses API 调用真实模型。
- aliyun：通过阿里云百炼 OpenAI 兼容接口调用千问模型。

快速体验：
    python AI睡眠助手_端到端编排_V1.1.py --case P04 --answer-selected

运行四个冻结场景测试：
    python AI睡眠助手_端到端编排_V1.1.py --self-test

使用自己的标准化输入：
    python AI睡眠助手_端到端编排_V1.1.py 输入.json -o 页面输出.json

调用真实模型：
    export OPENAI_API_KEY="你的API Key"
    python AI睡眠助手_端到端编排_V1.1.py 输入.json \
        --mode openai --model gpt-5-mini --answer-selected

调用阿里云百炼：
    export DASHSCOPE_API_KEY="你的API Key"
    python AI睡眠助手_端到端编排_V1.1.py --case P04 \
        --mode aliyun --model qwen3.7-plus --answer-selected

安全说明：API Key 只从环境变量读取，不要写入代码或提交到 GitHub。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol


SCRIPT_DIR = Path(__file__).resolve().parent
RULE_MODULE_PATH = SCRIPT_DIR / "AI睡眠助手_规则控制层_V2.2_Final.py"
SELECTOR_MODULE_PATH = SCRIPT_DIR / "AI睡眠助手_推荐追问选择器_V1.py"
INTERPRETATION_PROMPT_PATH = SCRIPT_DIR / "AI睡眠助手_个性化解读Prompt_V1.0.md"
ANSWER_PROMPT_PATH = SCRIPT_DIR / "AI睡眠助手_推荐追问回答Prompt_V1.1.md"
TEST_SUITE_PATH = SCRIPT_DIR / "AI睡眠助手_深睡规则测试集_V2.2_Final.json"


def first_existing_path(candidates: Iterable[Path]) -> Path:
    for path in candidates:
        if path.exists():
            return path
    joined = "\n".join(f"- {path}" for path in candidates)
    raise FileNotFoundError(f"找不到所需文件，已检查：\n{joined}")


KNOWLEDGE_BASE_PATH = first_existing_path(
    [
        SCRIPT_DIR / "AI睡眠助手_深睡MVP审核知识库_V1.1.json",
        SCRIPT_DIR.parent / "AI睡眠助手_深睡MVP审核知识库_V1.1.json",
    ]
)


INTERPRETATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "personalized_interpretation"],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "personalized_interpretation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "evidence_refs"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 100},
                "evidence_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "uniqueItems": True,
                },
            },
        },
    },
}


ANSWER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "question_answer"],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "question_answer": {
            "type": "object",
            "additionalProperties": False,
            "required": ["question_id", "text", "evidence_refs", "knowledge_refs"],
            "properties": {
                "question_id": {"type": "string"},
                "text": {"type": "string", "minLength": 1},
                "evidence_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "knowledge_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "uniqueItems": True,
                },
            },
        },
    },
}


def load_python_module(path: Path, module_name: str) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"找不到模块：{path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RULES = load_python_module(RULE_MODULE_PATH, "sleep_rules_final")
SELECTOR = load_python_module(SELECTOR_MODULE_PATH, "sleep_question_selector")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"找不到文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON格式错误：{path}（第{exc.lineno}行，第{exc.colno}列）"
        ) from exc


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def extract_system_prompt(path: Path) -> str:
    """从现有 Markdown Prompt 中提取 System Prompt 正文。"""
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"## System Prompt\s*(.*?)(?=\n---\s*(?:\n|$))",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"无法从文件提取 System Prompt：{path}")
    content = match.group(1).strip()
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        content = "\n".join(lines[1:-1]).strip()
    return content


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def clean_json_text(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型未返回有效JSON：{cleaned[:300]}") from exc
    if not isinstance(result, dict):
        raise ValueError("模型输出的最外层必须是JSON对象")
    return result


class ModelProvider(Protocol):
    name: str

    def generate_interpretation(
        self,
        grounding_facts: Dict[str, Any],
        visible_card_content: Dict[str, Any],
        conversation_context: List[Dict[str, Any]],
        safety_constraints: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    def generate_answer(
        self,
        question_selection: Dict[str, Any],
        grounding_facts: Dict[str, Any],
        personalized_interpretation: Dict[str, Any],
        conversation_context: List[Dict[str, Any]],
        knowledge_context: List[Dict[str, Any]],
        safety_constraints: Dict[str, Any],
    ) -> Dict[str, Any]: ...


class MockProvider:
    """确定性本地实现，只用于联调流程，不代替正式模型效果评测。"""

    name = "mock"

    def generate_interpretation(
        self,
        grounding_facts: Dict[str, Any],
        visible_card_content: Dict[str, Any],
        conversation_context: List[Dict[str, Any]],
        safety_constraints: Dict[str, Any],
    ) -> Dict[str, Any]:
        del visible_card_content, conversation_context, safety_constraints
        quality = grounding_facts["data_quality"]
        baseline = grounding_facts["personal_baseline"]
        position = grounding_facts["range_position"]
        persistence = grounding_facts["persistence"]

        if quality["usable"] is not True:
            text = "本次睡眠分期数据不足以支持可靠解读，暂不判断深睡的高低或变化。"
            refs = ["grounding_facts.data_quality"]
        elif baseline["available"] is not True:
            text = "当前有效历史记录还不足以建立个人近期典型区间，暂不判断这次结果相对个人水平的位置。"
            refs = ["grounding_facts.personal_baseline"]
        elif (
            position["duration"] == "below_typical_range"
            and position["ratio"] == "below_typical_range"
            and persistence["status"] == "repeated"
        ):
            text = (
                "昨晚深睡时长和占比均低于个人近期典型水平，"
                f"最近{persistence['observed_nights']}个有效夜晚中有"
                f"{persistence['matched_nights']}晚出现类似变化。"
            )
            refs = [
                "grounding_facts.range_position",
                "grounding_facts.persistence",
            ]
        elif (
            position["duration"] == "below_typical_range"
            and position["ratio"] == "below_typical_range"
        ):
            text = "昨晚深睡时长和占比均低于个人近期典型水平，目前更接近单晚或偶发波动。"
            refs = [
                "grounding_facts.range_position",
                "grounding_facts.persistence",
            ]
        elif (
            position["duration"] == "below_typical_range"
            and position["ratio"] == "within_typical_range"
        ):
            text = "昨晚深睡时长低于个人近期典型区间，深睡占比仍位于个人近期典型区间内。"
            refs = ["grounding_facts.range_position"]
        elif (
            position["duration"] == "within_typical_range"
            and position["ratio"] == "below_typical_range"
        ):
            text = "昨晚深睡时长仍位于个人近期典型区间内，深睡占比低于个人近期典型区间。"
            refs = ["grounding_facts.range_position"]
        elif (
            position["duration"] == "within_typical_range"
            and position["ratio"] == "within_typical_range"
        ):
            text = "昨晚深睡时长和占比均位于个人近期典型区间内，未见明显偏离。"
            refs = ["grounding_facts.range_position"]
        elif (
            position["duration"] == "above_typical_range"
            and position["ratio"] == "above_typical_range"
        ):
            text = "昨晚深睡时长和占比均高于个人近期典型区间，这只说明本次记录相对近期水平偏高。"
            refs = ["grounding_facts.range_position"]
        else:
            text = "昨晚深睡时长与占比处于不同的个人区间位置，需要分别理解两项记录。"
            refs = ["grounding_facts.range_position"]

        return {
            "schema_version": "1.0",
            "personalized_interpretation": {
                "text": text,
                "evidence_refs": refs,
            },
        }

    def generate_answer(
        self,
        question_selection: Dict[str, Any],
        grounding_facts: Dict[str, Any],
        personalized_interpretation: Dict[str, Any],
        conversation_context: List[Dict[str, Any]],
        knowledge_context: List[Dict[str, Any]],
        safety_constraints: Dict[str, Any],
    ) -> Dict[str, Any]:
        del personalized_interpretation, conversation_context, safety_constraints
        question_id = question_selection["question_id"]
        available_ids = {item["knowledge_id"] for item in knowledge_context}

        if question_id == "DS01":
            text = (
                "不是。深睡会随年龄、个体和不同夜晚发生变化，现有资料不支持把某个固定比例"
                "作为所有人的最优目标，也不能把深睡简单理解为越多越好。整体睡眠还需要结合"
                "总睡眠时间、睡眠连续性、醒后恢复感和日间状态理解。"
            )
            evidence_refs: List[str] = []
            knowledge_refs = ["K-DS-AMOUNT-01", "K-SLEEP-QUALITY-01"]
        elif question_id == "DS21":
            text = (
                "不一定。单晚记录同时包含自然夜间波动和设备估算的不确定性，首先只能说明这一晚"
                "与个人近期水平存在差异；是否形成稳定变化，还需要结合后续多晚记录观察。判断"
                "这一晚的整体睡眠，还要结合总睡眠时间、睡眠连续性、醒后恢复感和日间状态。"
            )
            evidence_refs = [
                "grounding_facts.range_position",
                "grounding_facts.persistence",
            ]
            knowledge_refs = ["K-DS-NIGHT-01", "K-SLEEP-QUALITY-01"]
        elif question_id == "DS15":
            p = grounding_facts["persistence"]
            text = (
                f"最近{p['observed_nights']}个有效夜晚中有{p['matched_nights']}晚出现类似变化，"
                "比单晚结果更值得继续观察，但它仍是设备记录到的深睡趋势，不能单独说明整体"
                "睡眠质量已经变差。整体判断还需结合总睡眠时间、睡眠连续性、醒后恢复感和"
                "日间状态。"
            )
            evidence_refs = [
                "grounding_facts.range_position",
                "grounding_facts.persistence",
            ]
            knowledge_refs = ["K-DS-TREND-01", "K-SLEEP-QUALITY-01"]
        else:
            raise ValueError(f"mock模式尚不支持问题：{question_id}")

        missing = [item for item in knowledge_refs if item not in available_ids]
        if missing:
            raise ValueError(f"知识映射缺少条目：{', '.join(missing)}")

        return {
            "schema_version": "1.0",
            "question_answer": {
                "question_id": question_id,
                "text": text,
                "evidence_refs": evidence_refs,
                "knowledge_refs": knowledge_refs,
            },
        }


class OpenAIProvider:
    """使用标准库调用 OpenAI Responses API，不额外依赖 openai Python 包。"""

    name = "openai"

    def __init__(self, model: str, api_key: Optional[str] = None) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "openai模式需要环境变量 OPENAI_API_KEY；请勿把API Key写入代码。"
            )
        self.interpretation_system = extract_system_prompt(
            INTERPRETATION_PROMPT_PATH
        )
        self.answer_system = extract_system_prompt(ANSWER_PROMPT_PATH)

    def _request(
        self,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API请求失败（HTTP {exc.code}）：{detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接OpenAI API：{exc.reason}") from exc

        output_text = result.get("output_text")
        if not output_text:
            for item in result.get("output", []):
                if item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if content.get("type") == "output_text" and content.get("text"):
                        output_text = content["text"]
                        break
                if output_text:
                    break
        if not output_text:
            raise RuntimeError("OpenAI API响应中没有可解析的文本输出")
        return clean_json_text(output_text)

    def generate_interpretation(
        self,
        grounding_facts: Dict[str, Any],
        visible_card_content: Dict[str, Any],
        conversation_context: List[Dict[str, Any]],
        safety_constraints: Dict[str, Any],
    ) -> Dict[str, Any]:
        user_prompt = f"""请根据以下输入生成深度睡眠指标卡的个性化解读。

【规则层事实】
{compact_json(grounding_facts)}

【当前指标卡已经展示的内容】
{compact_json(visible_card_content)}

【可用对话上下文】
{compact_json(conversation_context)}

【安全限制】
{compact_json(safety_constraints)}

请避免重复指标卡已经直接展示的内容，只补充用户仍需要理解的信息。
严格按照System Prompt规定的JSON结构返回。"""
        return self._request(
            self.interpretation_system,
            user_prompt,
            "sleep_interpretation_v1",
            INTERPRETATION_SCHEMA,
        )

    def generate_answer(
        self,
        question_selection: Dict[str, Any],
        grounding_facts: Dict[str, Any],
        personalized_interpretation: Dict[str, Any],
        conversation_context: List[Dict[str, Any]],
        knowledge_context: List[Dict[str, Any]],
        safety_constraints: Dict[str, Any],
    ) -> Dict[str, Any]:
        user_prompt = f"""请回答用户已经点击的推荐追问。

【已选择的推荐追问】
{compact_json(question_selection)}

【规则层事实】
{compact_json(grounding_facts)}

【指标卡已有个性化解读】
{compact_json(personalized_interpretation)}

【可用对话上下文】
{compact_json(conversation_context)}

【知识依据】
{compact_json(knowledge_context)}

【安全限制】
{compact_json(safety_constraints)}

请直接回答当前问题，不要复述整张指标卡，不要生成新的推荐追问。
严格按照System Prompt规定的JSON结构返回。"""
        return self._request(
            self.answer_system,
            user_prompt,
            "sleep_question_answer_v1",
            ANSWER_SCHEMA,
        )


class AliyunBailianProvider:
    """调用阿里云百炼 OpenAI 兼容 Chat Completions 接口。"""

    name = "aliyun"

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "aliyun模式需要环境变量 DASHSCOPE_API_KEY；请勿把API Key写入代码。"
            )
        self.base_url = (
            base_url
            or os.environ.get("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.interpretation_system = extract_system_prompt(
            INTERPRETATION_PROMPT_PATH
        )
        self.answer_system = extract_system_prompt(ANSWER_PROMPT_PATH)

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _request(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_completion_tokens": 2048,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"阿里云百炼API请求失败（HTTP {exc.code}）：{detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接阿里云百炼API：{exc.reason}") from exc

        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("阿里云百炼API响应中没有 choices")
        message = choices[0].get("message", {})
        output_text = message.get("content")
        if isinstance(output_text, list):
            output_text = "".join(
                item.get("text", "")
                for item in output_text
                if isinstance(item, dict)
            )
        if not isinstance(output_text, str) or not output_text.strip():
            raise RuntimeError("阿里云百炼API响应中没有可解析的文本输出")
        return clean_json_text(output_text)

    def generate_interpretation(
        self,
        grounding_facts: Dict[str, Any],
        visible_card_content: Dict[str, Any],
        conversation_context: List[Dict[str, Any]],
        safety_constraints: Dict[str, Any],
    ) -> Dict[str, Any]:
        user_prompt = f"""请根据以下输入生成深度睡眠指标卡的个性化解读。

【规则层事实】
{compact_json(grounding_facts)}

【当前指标卡已经展示的内容】
{compact_json(visible_card_content)}

【可用对话上下文】
{compact_json(conversation_context)}

【安全限制】
{compact_json(safety_constraints)}

请避免重复指标卡已经直接展示的内容，只补充用户仍需要理解的信息。
只返回一个合法JSON对象，严格按照System Prompt规定的JSON结构返回。"""
        return self._request(self.interpretation_system, user_prompt)

    def generate_answer(
        self,
        question_selection: Dict[str, Any],
        grounding_facts: Dict[str, Any],
        personalized_interpretation: Dict[str, Any],
        conversation_context: List[Dict[str, Any]],
        knowledge_context: List[Dict[str, Any]],
        safety_constraints: Dict[str, Any],
    ) -> Dict[str, Any]:
        user_prompt = f"""请回答用户已经点击的推荐追问。

【已选择的推荐追问】
{compact_json(question_selection)}

【规则层事实】
{compact_json(grounding_facts)}

【指标卡已有个性化解读】
{compact_json(personalized_interpretation)}

【可用对话上下文】
{compact_json(conversation_context)}

【知识依据】
{compact_json(knowledge_context)}

【安全限制】
{compact_json(safety_constraints)}

请直接回答当前问题，不要复述整张指标卡，不要生成新的推荐追问。
只返回一个合法JSON对象，严格按照System Prompt规定的JSON结构返回。"""
        return self._request(self.answer_system, user_prompt)


def load_knowledge_base() -> Dict[str, Any]:
    knowledge_base = read_json(KNOWLEDGE_BASE_PATH)
    if knowledge_base.get("usage_policy", {}).get("retrieval_mode") != "fixed_mapping":
        raise ValueError("当前编排脚本只支持 fixed_mapping 审核知识库")
    return knowledge_base


def retrieve_knowledge(
    knowledge_base: Dict[str, Any], question_id: str
) -> List[Dict[str, Any]]:
    mapping = knowledge_base.get("question_mapping", {}).get(question_id)
    if not mapping:
        raise KeyError(f"知识库中没有 question_id={question_id} 的固定映射")

    # 当前MVP自动注入 required 和 optional；总量仍受 max_entries_per_answer 限制。
    requested_ids = list(mapping.get("required", [])) + list(mapping.get("optional", []))
    max_entries = int(
        knowledge_base.get("usage_policy", {}).get("max_entries_per_answer", 3)
    )
    requested_ids = requested_ids[:max_entries]
    entries = {
        item["knowledge_id"]: item
        for item in knowledge_base.get("knowledge_entries", [])
    }
    missing = [item for item in requested_ids if item not in entries]
    if missing:
        raise KeyError(f"知识库缺少映射条目：{', '.join(missing)}")
    return [entries[item] for item in requested_ids]


def build_visible_card_content(rule_output: Dict[str, Any]) -> Dict[str, Any]:
    baseline = rule_output["metric_signal"]["personal_baseline"]
    return {
        "current_value_shown": True,
        "personal_baseline_shown": baseline["available"],
        "quartile_range_shown": baseline["available"],
        "valid_nights_shown": True,
        "fixed_metric_definition_shown": True,
        "fixed_uncertainty_note_shown": True,
    }


def build_safety_constraints(rule_output: Dict[str, Any]) -> Dict[str, Any]:
    context = rule_output["llm_context"]
    return {
        "fixed_page_messages": context["fixed_page_messages"],
        "prohibited_claims": context["prohibited_claims"],
    }


def validate_interpretation(result: Dict[str, Any]) -> None:
    if result.get("schema_version") != "1.0":
        raise ValueError("个性化解读 schema_version 必须为 1.0")
    item = result.get("personalized_interpretation")
    if not isinstance(item, dict) or not item.get("text"):
        raise ValueError("个性化解读缺少 personalized_interpretation.text")
    if not isinstance(item.get("evidence_refs"), list) or not item["evidence_refs"]:
        raise ValueError("个性化解读必须包含 evidence_refs")


def validate_answer(
    result: Dict[str, Any],
    question_id: str,
    allowed_knowledge_ids: Iterable[str],
    required_knowledge_ids: Iterable[str],
) -> None:
    if result.get("schema_version") != "1.0":
        raise ValueError("追问回答 schema_version 必须为 1.0")
    answer = result.get("question_answer")
    if not isinstance(answer, dict) or not answer.get("text"):
        raise ValueError("追问回答缺少 question_answer.text")
    if answer.get("question_id") != question_id:
        raise ValueError("回答中的 question_id 与选择器结果不一致")
    refs = answer.get("knowledge_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("追问回答必须包含 knowledge_refs")
    allowed = set(allowed_knowledge_ids)
    unexpected = [item for item in refs if item not in allowed]
    if unexpected:
        raise ValueError(
            "回答引用了未提供的知识条目：" + ", ".join(unexpected)
        )
    required = set(required_knowledge_ids)
    missing_required = sorted(required - set(refs))
    if missing_required:
        raise ValueError(
            "回答遗漏必需知识条目：" + ", ".join(missing_required)
        )

    evidence_refs = answer.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        raise ValueError("追问回答必须包含 evidence_refs 数组")
    required_evidence_by_question = {
        "DS01": set(),
        "DS21": {
            "grounding_facts.range_position",
            "grounding_facts.persistence",
        },
        "DS15": {
            "grounding_facts.range_position",
            "grounding_facts.persistence",
        },
    }
    required_evidence = required_evidence_by_question.get(question_id, set())
    missing_evidence = sorted(required_evidence - set(evidence_refs))
    if missing_evidence:
        raise ValueError(
            "回答遗漏必需个人事实引用：" + ", ".join(missing_evidence)
        )


def build_metric_card(
    rule_output: Dict[str, Any],
    interpretation_result: Dict[str, Any],
    question_selection: Dict[str, Any],
) -> Dict[str, Any]:
    signal = rule_output["metric_signal"]
    baseline = signal["personal_baseline"]
    question = None
    if question_selection["selected"]:
        question = {
            "question_id": question_selection["question_id"],
            "intent": question_selection["intent"],
            "text": question_selection["text"],
            "context_source": question_selection["context_source"],
        }

    return {
        "sleep_date": rule_output["sleep_date"],
        "metric_id": signal["metric_id"],
        "current_value": signal["current_value"],
        "personal_baseline": {
            "available": baseline["available"],
            "duration_median": baseline["duration_median"],
            "duration_q1": baseline["duration_q1"],
            "duration_q3": baseline["duration_q3"],
            "ratio_median": baseline["ratio_median"],
            "ratio_q1": baseline["ratio_q1"],
            "ratio_q3": baseline["ratio_q3"],
            "valid_nights": baseline["valid_nights"],
            "window_days": baseline["window_days"],
        },
        "range_position": signal["range_position"],
        "personalized_interpretation": interpretation_result[
            "personalized_interpretation"
        ],
        "recommended_question": question,
        "fixed_page_messages": rule_output["llm_context"]["fixed_page_messages"],
    }


def run_pipeline(
    input_document: Dict[str, Any],
    provider: ModelProvider,
    *,
    seen_question_ids: Optional[Iterable[str]] = None,
    conversation_context: Optional[List[Dict[str, Any]]] = None,
    answer_selected: bool = False,
    include_debug: bool = False,
) -> Dict[str, Any]:
    conversation = conversation_context or []
    rule_output = RULES.evaluate(input_document)
    grounding_facts = rule_output["llm_context"]["grounding_facts"]
    visible_card_content = build_visible_card_content(rule_output)
    safety_constraints = build_safety_constraints(rule_output)

    interpretation_result = provider.generate_interpretation(
        grounding_facts,
        visible_card_content,
        conversation,
        safety_constraints,
    )
    validate_interpretation(interpretation_result)

    question_selection = SELECTOR.select_recommended_question(
        rule_output,
        seen_question_ids=seen_question_ids,
    )
    metric_card = build_metric_card(
        rule_output,
        interpretation_result,
        question_selection,
    )

    result: Dict[str, Any] = {
        "schema_version": "1.0",
        "pipeline": {
            "version": "SLEEP-END-TO-END-V1.1",
            "mode": provider.name,
            "rule_version": rule_output["contract"]["rule_version"],
            "selector_version": SELECTOR.SELECTOR_VERSION,
            "knowledge_mode": "fixed_mapping_reviewed_knowledge",
        },
        "metric_card": metric_card,
        "question_answer": None,
    }

    if answer_selected and question_selection["selected"]:
        knowledge_base = load_knowledge_base()
        knowledge_context = retrieve_knowledge(
            knowledge_base,
            question_selection["question_id"],
        )
        answer_result = provider.generate_answer(
            question_selection,
            grounding_facts,
            interpretation_result["personalized_interpretation"],
            conversation,
            knowledge_context,
            safety_constraints,
        )
        validate_answer(
            answer_result,
            question_selection["question_id"],
            [item["knowledge_id"] for item in knowledge_context],
            knowledge_base["question_mapping"][
                question_selection["question_id"]
            ].get("required", []),
        )
        result["question_answer"] = answer_result["question_answer"]

    if include_debug:
        result["debug"] = {
            "rule_output": rule_output,
            "question_selection": question_selection,
        }
    return result


CASE_TO_TEST_ID = {
    "P01": "DS-T01",
    "P03": "DS-T03",
    "P04": "DS-T04",
    "P05": "DS-T05",
}


def build_case_document(case_name: str) -> Dict[str, Any]:
    if case_name not in CASE_TO_TEST_ID:
        raise ValueError(f"未知案例：{case_name}")
    suite = read_json(TEST_SUITE_PATH)
    target_id = CASE_TO_TEST_ID[case_name]
    case = next(item for item in suite["cases"] if item["id"] == target_id)
    return {
        "request_id": f"demo-{case_name}",
        "evaluated_at": "2026-10-21T00:00:00+08:00",
        "current_night": case["current_night"],
        "history_nights": suite["fixtures"][case["history_fixture"]],
        "subjective_feedback": case.get("subjective_feedback", {}),
    }


def run_self_test() -> Dict[str, Any]:
    expected_questions = {
        "P01": "DS01",
        "P03": "DS21",
        "P04": "DS15",
        "P05": None,
    }
    provider = MockProvider()
    results = []
    for case_name, expected_question in expected_questions.items():
        output = run_pipeline(
            build_case_document(case_name),
            provider,
            answer_selected=True,
        )
        question = output["metric_card"]["recommended_question"]
        actual_question = question["question_id"] if question else None
        answer_present = output["question_answer"] is not None
        expected_answer_present = expected_question is not None
        passed = (
            actual_question == expected_question
            and answer_present == expected_answer_present
        )
        results.append(
            {
                "case": case_name,
                "expected_question_id": expected_question,
                "actual_question_id": actual_question,
                "answer_present": answer_present,
                "passed": passed,
            }
        )
    passed_count = sum(item["passed"] for item in results)
    return {
        "suite": "AI睡眠助手端到端冻结场景测试",
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行AI睡眠助手端到端流程")
    parser.add_argument("input", nargs="?", type=Path, help="标准化睡眠输入JSON")
    parser.add_argument(
        "--case",
        choices=sorted(CASE_TO_TEST_ID),
        help="直接运行冻结案例P01、P03、P04或P05",
    )
    parser.add_argument(
        "--mode",
        choices=["mock", "openai", "aliyun"],
        default="mock",
        help="mock无需API Key；openai或aliyun调用真实模型",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="模型名称；openai默认gpt-5-mini，aliyun默认qwen3.7-plus",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="aliyun可选Base URL；默认读取DASHSCOPE_BASE_URL或使用北京公共兼容地址",
    )
    parser.add_argument(
        "--conversation-context",
        type=Path,
        help="可选的对话上下文JSON数组",
    )
    parser.add_argument(
        "--seen",
        nargs="*",
        default=[],
        metavar="QUESTION_ID",
        help="已经向用户展示过的问题ID",
    )
    parser.add_argument(
        "--answer-selected",
        action="store_true",
        help="模拟用户点击当前推荐问题并生成回答",
    )
    parser.add_argument(
        "--include-debug",
        action="store_true",
        help="在输出中附加完整规则结果和选择器结果",
    )
    parser.add_argument("-o", "--output", type=Path, help="保存最终JSON的路径")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="运行P01、P03、P04、P05四个端到端测试",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.self_test:
        report = run_self_test()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report["failed"] == 0 else 1)

    if args.input and args.case:
        raise ValueError("input文件和--case只能选择一种")
    input_document = (
        read_json(args.input)
        if args.input
        else build_case_document(args.case or "P04")
    )

    conversation_context: List[Dict[str, Any]] = []
    if args.conversation_context:
        loaded_context = read_json(args.conversation_context)
        if not isinstance(loaded_context, list):
            raise ValueError("conversation_context必须是JSON数组")
        conversation_context = loaded_context

    provider: ModelProvider
    if args.mode == "openai":
        model = args.model or os.environ.get("OPENAI_MODEL", "gpt-5-mini")
        provider = OpenAIProvider(model)
    elif args.mode == "aliyun":
        model = args.model or os.environ.get("DASHSCOPE_MODEL", "qwen3.7-plus")
        provider = AliyunBailianProvider(model, base_url=args.base_url)
    else:
        provider = MockProvider()

    result = run_pipeline(
        input_document,
        provider,
        seen_question_ids=args.seen,
        conversation_context=conversation_context,
        answer_selected=args.answer_selected,
        include_debug=args.include_debug,
    )

    if args.output:
        write_json(args.output, result)
        print(f"已生成：{args.output}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
