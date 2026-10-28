"""AI 睡眠助手作品集 Demo。

公开演示默认读取已经通过真实模型验收的脱敏案例，不调用外部 API。
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

SCENARIOS = {
    "日常正常波动": {
        "case_id": "P01",
        "file": "P01_百炼完整输出_V1.1.json",
        "eyebrow": "深睡处于个人典型范围",
        "description": "理解深睡不是越多越好",
        "accent": "#5B5BD6",
    },
    "单晚明显偏低": {
        "case_id": "P03",
        "file": "P03_百炼完整输出_V1.1.json",
        "eyebrow": "单晚深睡时长与占比偏低",
        "description": "区分单晚波动与稳定趋势",
        "accent": "#7C5CE7",
    },
    "近期重复偏低": {
        "case_id": "P04",
        "file": "P04_百炼完整输出_V1.1.json",
        "eyebrow": "近4晚中有3晚低于个人水平",
        "description": "理解重复变化的意义与边界",
        "accent": "#8F5AE8",
    },
    "时长低、占比正常": {
        "case_id": "P05",
        "file": "P05_百炼完整输出_V1.1.json",
        "eyebrow": "深睡时长偏低但占比稳定",
        "description": "信息不足时不强行推荐问题",
        "accent": "#4078C0",
    },
}


@st.cache_data
def load_case(filename: str) -> dict:
    with (DATA_DIR / filename).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def minutes_label(minutes: float) -> str:
    minutes = int(round(minutes))
    hours, rest = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{rest}分钟" if rest else f"{hours}小时"
    return f"{rest}分钟"


def e(value: object) -> str:
    return html.escape(str(value))


def range_position(value: float, q1: float, q3: float) -> tuple[float, float, float]:
    """Return marker, band start and band end positions on a 0-100 track."""
    span = max(q3 - q1, abs(q3) * 0.02, 0.01)
    scale_min = q1 - span * 0.8
    scale_max = q3 + span * 0.8
    to_percent = lambda item: (item - scale_min) / (scale_max - scale_min) * 100
    marker = min(97.0, max(3.0, to_percent(value)))
    return marker, to_percent(q1), to_percent(q3)


def range_status(value: float, q1: float, q3: float) -> tuple[str, str]:
    if value < q1:
        return "低于个人典型范围", "below"
    if value > q3:
        return "高于个人典型范围", "above"
    return "处于个人典型范围", "within"


def range_item(
    title: str,
    current_label: str,
    q1_label: str,
    q3_label: str,
    value: float,
    q1: float,
    q3: float,
) -> str:
    marker, band_start, band_end = range_position(value, q1, q3)
    status_text, status_class = range_status(value, q1, q3)
    return f"""
      <div class="range-item">
        <div class="range-head">
          <div><div class="range-name">{e(title)}</div><div class="range-current">当前 {e(current_label)}</div></div>
          <div class="range-status {status_class}">{e(status_text)}</div>
        </div>
        <div class="range-track">
          <div class="range-band" style="left:{band_start:.1f}%;width:{band_end - band_start:.1f}%"></div>
          <div class="range-marker" style="left:{marker:.1f}%"><span>当前</span></div>
        </div>
        <div class="range-labels"><span>较低</span><span>典型范围 {e(q1_label)}—{e(q3_label)}</span><span>较高</span></div>
      </div>
    """


def free_question_answer(question: str, current_case_id: str) -> tuple[str, str, str]:
    """Provide a deterministic, locally grounded answer for the portfolio demo.

    Returns answer text, references and a short scope label.  The selected
    scenario is passed explicitly so the demo never pretends that a generic
    knowledge answer was inferred from the current user's data.
    """
    normalized = question.strip().lower()
    if any(word in normalized for word in ("rem", "快速眼动", "清醒", "核心睡眠")):
        return (
            "当前 MVP 只完成了深睡指标的规则、知识和评测闭环，暂不对 REM、清醒或核心睡眠作扩展判断。正式版本会为这些指标分别建立规则和审核知识后再开放回答。",
            "MVP 产品边界",
            "超出当前 MVP 范围",
        )
    if any(word in normalized for word in ("连续", "多次", "最近", "近期", "趋势")):
        if current_case_id != "P04":
            return (
                "当前选中的演示场景没有提供足够的多晚趋势证据，因此不能据此判断近期是否持续偏低。你可以切换到“近期重复偏低”场景查看系统如何结合多晚记录；一般而言，重复变化值得继续观察，但仍需结合总睡眠、连续性和日间感受理解。",
                "K-DS-TREND-01 · K-SLEEP-QUALITY-01",
                "当前场景数据 + 审核知识",
            )
        item = load_case("P04_百炼完整输出_V1.1.json")["question_answer"]
    elif any(word in normalized for word in ("单晚", "一晚", "昨晚", "偶尔")):
        item = load_case("P03_百炼完整输出_V1.1.json")["question_answer"]
    elif any(word in normalized for word in ("越多", "多少", "最优", "正常值", "正常范围")):
        item = load_case("P01_百炼完整输出_V1.1.json")["question_answer"]
    else:
        return (
            "仅凭深睡记录还不足以判断具体原因，也不宜直接推断压力、疾病或某项生活习惯。可以结合总睡眠时间、睡眠连续性、醒后恢复感和日间状态，并继续观察后续多晚趋势。",
            "K-SLEEP-QUALITY-01",
            "审核知识回答",
        )
    return item["text"], " · ".join(item.get("knowledge_refs", [])), "审核知识回答"


def inject_css(accent: str) -> None:
    st.markdown(
        f"""
        <style>
        :root {{ --accent: {accent}; }}
        [data-testid="stAppViewContainer"] {{
            background:
                radial-gradient(circle at 15% 0%, rgba(111, 92, 231, .10), transparent 28rem),
                #F6F7FB;
        }}
        [data-testid="stHeader"] {{ background: transparent; }}
        [data-testid="stSidebar"] {{
            background: rgba(255,255,255,.88);
            border-right: 1px solid #E9EAF1;
        }}
        .block-container {{ max-width: 1180px; padding-top: 2.2rem; padding-bottom: 4rem; }}
        h1, h2, h3, p, div {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", "Segoe UI", sans-serif; }}
        .hero {{ padding: 1rem 0 1.4rem; }}
        .brand-row {{ display:flex; align-items:center; gap:.65rem; margin-bottom:.7rem; }}
        .moon {{
            width:2.45rem; height:2.45rem; border-radius: .85rem;
            display:grid; place-items:center; color:white; font-size:1.25rem;
            background:linear-gradient(145deg, var(--accent), #9A77F6);
            box-shadow:0 10px 30px rgba(91,91,214,.22);
        }}
        .brand {{ color:#1E2030; font-size:1.02rem; font-weight:750; letter-spacing:.01em; }}
        .version-chip {{
            display:inline-flex; padding:.26rem .58rem; border-radius:999px;
            background:#EEEAFE; color:#5B46B8; font-size:.72rem; font-weight:700;
        }}
        .hero-title {{
            color:#171927; font-size:clamp(2rem,5vw,3.55rem); line-height:1.08;
            font-weight:780; letter-spacing:-.045em; margin:.2rem 0 .75rem;
        }}
        .hero-sub {{ max-width:46rem; color:#676B7D; font-size:1.02rem; line-height:1.75; }}
        .surface {{
            background:rgba(255,255,255,.94); border:1px solid rgba(230,231,239,.95);
            border-radius:24px; padding:1.35rem 1.45rem;
            box-shadow:0 16px 45px rgba(43,45,72,.06);
        }}
        .metric-card {{
            min-height:125px; background:white; border:1px solid #E9EAF1;
            border-radius:22px; padding:1.15rem 1.2rem; box-shadow:0 10px 30px rgba(43,45,72,.045);
        }}
        .metric-label {{ color:#85899A; font-size:.78rem; margin-bottom:.42rem; }}
        .metric-value {{ color:#1D1F2C; font-size:1.65rem; line-height:1.15; font-weight:760; letter-spacing:-.03em; }}
        .metric-note {{ color:#8C90A1; font-size:.72rem; margin-top:.48rem; }}
        .section-kicker {{ color:var(--accent); font-size:.76rem; font-weight:760; letter-spacing:.08em; text-transform:uppercase; }}
        .section-title {{ color:#202231; font-size:1.28rem; font-weight:760; margin:.3rem 0 .55rem; }}
        .body-copy {{ color:#555A6C; line-height:1.82; font-size:.96rem; }}
        .status-pill {{
            display:inline-flex; align-items:center; gap:.42rem; padding:.38rem .72rem;
            border-radius:999px; color:#5140B5; background:#F0EDFF; font-size:.76rem; font-weight:720;
        }}
        .status-dot {{ width:.48rem; height:.48rem; border-radius:50%; background:var(--accent); box-shadow:0 0 0 4px rgba(91,91,214,.11); }}
        .range-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:1rem; margin-top:1rem; }}
        .range-item {{ background:#F8F8FC; border:1px solid #ECECF3; border-radius:18px; padding:1rem 1rem .85rem; }}
        .range-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:.8rem; margin-bottom:1.25rem; }}
        .range-name {{ color:#717587; font-size:.76rem; }}
        .range-current {{ color:#202231; font-size:1.12rem; font-weight:760; margin-top:.2rem; }}
        .range-status {{ white-space:nowrap; border-radius:999px; padding:.28rem .55rem; font-size:.68rem; font-weight:760; }}
        .range-status.within {{ color:#267052; background:#E5F5ED; }}
        .range-status.below {{ color:#A64B35; background:#FCEBE6; }}
        .range-status.above {{ color:#7B50B5; background:#F0E8FB; }}
        .range-track {{ height:11px; border-radius:99px; background:#E1E3EA; position:relative; overflow:visible; }}
        .range-band {{ position:absolute; height:11px; border-radius:99px; background:linear-gradient(90deg,#B8A9F8,var(--accent)); }}
        .range-marker {{ position:absolute; top:50%; transform:translate(-50%,-50%); width:19px; height:19px; border:4px solid white; border-radius:50%; background:#24263A; box-shadow:0 4px 12px rgba(36,38,58,.25); }}
        .range-marker span {{ position:absolute; left:50%; bottom:18px; transform:translateX(-50%); white-space:nowrap; color:#373A4B; background:white; border:1px solid #E3E4EB; border-radius:6px; padding:.12rem .3rem; font-size:.58rem; font-weight:700; }}
        .range-labels {{ display:flex; justify-content:space-between; gap:.4rem; color:#8A8E9E; font-size:.66rem; margin-top:.62rem; }}
        .range-labels span:nth-child(2) {{ color:#5E52B5; font-weight:700; text-align:center; }}
        .question-card {{
            background:linear-gradient(135deg, #282A40 0%, #3B3568 52%, var(--accent) 135%);
            border-radius:24px; padding:1.45rem 1.5rem; color:white; box-shadow:0 20px 44px rgba(43,42,78,.18);
        }}
        .question-eyebrow {{ color:#CDC8F5; font-size:.76rem; font-weight:700; letter-spacing:.06em; }}
        .question-text {{ color:white; font-size:1.22rem; line-height:1.55; font-weight:730; margin:.48rem 0 .2rem; }}
        .answer-card {{
            border-left:4px solid var(--accent); background:#F5F3FF; color:#44485A;
            border-radius:5px 18px 18px 5px; padding:1rem 1.15rem; line-height:1.85; margin-top:.9rem;
        }}
        .quiet-card {{ background:#F0F3F8; color:#5F6574; border-radius:20px; padding:1.15rem 1.25rem; line-height:1.7; }}
        .ask-shell {{ margin-top:1.35rem; padding:1.3rem 1.4rem; border-radius:24px; background:white; border:1px solid #E8E9F0; box-shadow:0 12px 32px rgba(43,45,72,.05); }}
        .ask-title {{ color:#242635; font-size:1.05rem; font-weight:760; }}
        .ask-sub {{ color:#85899A; font-size:.76rem; line-height:1.6; margin:.28rem 0 .7rem; }}
        .demo-guide {{ display:flex; gap:.8rem; align-items:flex-start; background:#EEF1FF; border:1px solid #E0E4FA; color:#51566B; border-radius:18px; padding:.9rem 1rem; margin:.2rem 0 1.2rem; font-size:.78rem; line-height:1.65; }}
        .demo-guide strong {{ color:#34384C; white-space:nowrap; }}
        .answer-scope {{ display:inline-flex; margin:.15rem 0 .55rem; padding:.24rem .55rem; border-radius:999px; background:#EEEAFE; color:#5B46B8; font-size:.68rem; font-weight:750; }}
        .flow-grid {{ display:grid; grid-template-columns:repeat(5,1fr); gap:.65rem; margin:1rem 0; }}
        .flow-item {{ background:white; border:1px solid #E8E9F0; border-radius:18px; padding:1rem; min-height:112px; }}
        .flow-num {{ color:var(--accent); font-size:.72rem; font-weight:800; }}
        .flow-name {{ color:#242635; font-weight:750; margin:.35rem 0; }}
        .flow-desc {{ color:#777B8C; font-size:.74rem; line-height:1.55; }}
        .pass-badge {{ color:#207A55; background:#E8F8F0; padding:.27rem .55rem; border-radius:999px; font-size:.72rem; font-weight:750; }}
        .fine-print {{ color:#9497A6; font-size:.72rem; line-height:1.65; }}
        div[data-testid="stButton"] > button {{
            border-radius:999px; border:0; background:var(--accent); color:white;
            font-weight:720; padding:.55rem 1.15rem; box-shadow:0 8px 20px rgba(91,91,214,.18);
        }}
        div[data-testid="stButton"] > button:hover {{ color:white; border:0; filter:brightness(.96); }}
        [data-testid="stMetric"] {{ background:white; border:1px solid #E9EAF1; border-radius:20px; padding:1rem; }}
        @media (max-width: 850px) {{
            .flow-grid {{ grid-template-columns:1fr; }}
            .range-grid {{ grid-template-columns:1fr; }}
            .hero-title {{ font-size:2.2rem; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.markdown(
        """
        <div class="hero">
          <div class="brand-row">
            <div class="moon">☾</div>
            <div class="brand">眠知 · AI睡眠助手</div>
            <div class="version-chip">Portfolio Demo · V1.3</div>
          </div>
          <div class="hero-title">读懂你的睡眠，<br>不被单个数字困住。</div>
          <div class="hero-sub">基于个人近期基线识别单晚波动与重复变化，用规则层控制事实边界，再由审核知识约束大模型回答。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_product(case_name: str, config: dict, payload: dict) -> None:
    card = payload["metric_card"]
    current = card["current_value"]
    baseline = card["personal_baseline"]
    interpretation = card["personalized_interpretation"]
    question = card.get("recommended_question")
    answer = payload.get("question_answer")

    st.markdown(
        """
        <div class="demo-guide"><strong>如何体验</strong><span>先在左侧切换四种睡眠场景，观察当前值与个人区间；再展开系统推荐的问题，或在页面下方输入自己的问题。</span></div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div class="status-pill"><span class="status-dot"></span>{e(config["eyebrow"])}</div>',
        unsafe_allow_html=True,
    )
    st.caption(f'{config["description"]} · 脱敏测试场景 {config["case_id"]}')

    c1, c2, c3, c4 = st.columns(4)
    metrics = [
        ("记录日期", card["sleep_date"], "演示数据"),
        ("深睡时长", minutes_label(current["minutes"]), "昨晚记录"),
        ("深睡占比", f'{current["ratio"] * 100:.1f}%', "占总睡眠比例"),
        ("个人基线", minutes_label(baseline["duration_median"]), f'近{baseline["window_days"]}天 · {baseline["valid_nights"]}晚有效'),
    ]
    for column, (label, value, note) in zip((c1, c2, c3, c4), metrics):
        with column:
            st.markdown(
                f'<div class="metric-card"><div class="metric-label">{e(label)}</div><div class="metric-value">{e(value)}</div><div class="metric-note">{e(note)}</div></div>',
                unsafe_allow_html=True,
            )

    st.write("")
    st.markdown(
        f"""
        <div class="surface">
          <div class="section-kicker">Personalized interpretation</div>
          <div class="section-title">结合你的个人水平来看</div>
          <div class="body-copy">{e(interpretation["text"])}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.write("")
    duration_item = range_item(
        "深睡时长",
        minutes_label(current["minutes"]),
        minutes_label(baseline["duration_q1"]),
        minutes_label(baseline["duration_q3"]),
        current["minutes"],
        baseline["duration_q1"],
        baseline["duration_q3"],
    )
    ratio_item = range_item(
        "深睡占比",
        f'{current["ratio"] * 100:.1f}%',
        f'{baseline["ratio_q1"] * 100:.1f}%',
        f'{baseline["ratio_q3"] * 100:.1f}%',
        current["ratio"],
        baseline["ratio_q1"],
        baseline["ratio_q3"],
    )
    st.markdown(
        f"""
        <div class="surface">
          <div class="section-kicker">Personal baseline</div>
          <div class="section-title">当前值落在个人区间的哪里</div>
          <div class="body-copy">分别比较深睡时长和占比，避免只看其中一个指标。</div>
          <div class="range-grid">{duration_item}{ratio_item}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.write("")
    if question and answer:
        st.markdown(
            f"""
            <div class="question-card">
              <div class="question-eyebrow">你可能想了解</div>
              <div class="question-text">{e(question["text"])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        state_key = f'show_answer_{config["case_id"]}'
        if st.button("查看 AI 解读", key=f'button_{config["case_id"]}'):
            st.session_state[state_key] = True
        if st.session_state.get(state_key):
            st.markdown(f'<div class="answer-card">{e(answer["text"])}</div>', unsafe_allow_html=True)
            refs = " · ".join(answer.get("knowledge_refs", []))
            st.caption(f"审核知识引用：{refs}")
    else:
        st.markdown(
            """
            <div class="quiet-card"><strong>本场景不推荐追问</strong><br>深睡时长偏低，但占比仍处于个人典型区间。现有信息不足以支持一个真正有价值的新问题，因此系统保持克制。</div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")
    st.markdown(
        """
        <div class="ask-shell">
          <div class="ask-title">还有其他疑问？直接问睡眠助手</div>
          <div class="ask-sub">推荐追问帮助你快速理解当前结果；自由提问入口始终保留，作为用户主动探索的兜底。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    case_id = config["case_id"]
    question_state_key = f"free_question_{case_id}"
    answer_state_key = f"free_answer_{case_id}"
    refs_state_key = f"free_refs_{case_id}"
    scope_state_key = f"free_scope_{case_id}"

    with st.form(f'free_question_form_{case_id}', clear_on_submit=False):
        free_question = st.text_input(
            "自由提问",
            placeholder="例如：最近几天深睡都偏少，需要担心吗？",
            label_visibility="collapsed",
            key=f"free_question_input_{case_id}",
        )
        submitted = st.form_submit_button("查看回答")
    if submitted:
        if free_question.strip():
            free_answer, free_refs, free_scope = free_question_answer(free_question, case_id)
            st.session_state[question_state_key] = free_question.strip()
            st.session_state[answer_state_key] = free_answer
            st.session_state[refs_state_key] = free_refs
            st.session_state[scope_state_key] = free_scope
        else:
            st.warning("请先输入一个问题。")
    if st.session_state.get(question_state_key):
        with st.chat_message("user"):
            st.write(st.session_state[question_state_key])
        with st.chat_message("assistant"):
            st.markdown(f'<div class="answer-scope">{e(st.session_state[scope_state_key])}</div>', unsafe_allow_html=True)
            st.write(st.session_state[answer_state_key])
            st.caption(f'回答依据：{st.session_state[refs_state_key]} · 本地审核知识演示')

    st.caption("作品集版不会把自由问题发送到线上模型；正式产品将结合当前数据、历史趋势和 RAG 检索知识生成回答。")

    st.write("")
    st.markdown(
        '<div class="fine-print">ⓘ 可穿戴设备的睡眠分期属于算法估算。本 Demo 用于展示产品逻辑，不提供疾病诊断或治疗建议。</div>',
        unsafe_allow_html=True,
    )


def render_architecture() -> None:
    st.markdown("## 从数据到回答，哪些交给规则，哪些交给模型")
    st.write("产品的核心不是让大模型自由判断，而是先把事实、选择和边界结构化，再让模型完成用户可读的表达。")
    items = [
        ("01", "标准化数据", "昨晚指标、近14天有效记录与数据质量"),
        ("02", "规则层", "计算个人基线、区间位置与重复变化"),
        ("03", "个性化解读", "只把已确认事实转成简洁用户文案"),
        ("04", "追问选择", "固定规则选择 DS01、DS21、DS15 或 null"),
        ("05", "双入口回答", "推荐问题走固定映射，自由提问走审核知识检索"),
    ]
    blocks = "".join(
        f'<div class="flow-item"><div class="flow-num">{num}</div><div class="flow-name">{name}</div><div class="flow-desc">{desc}</div></div>'
        for num, name, desc in items
    )
    st.markdown(f'<div class="flow-grid">{blocks}</div>', unsafe_allow_html=True)

    a, b, c = st.columns(3)
    with a:
        st.markdown("### 个人基线优先")
        st.write("避免用一个通用比例给所有人贴上“正常”或“异常”标签，重点观察用户自己的近期变化。")
    with b:
        st.markdown("### 规则与模型分工")
        st.write("规则层决定事实和问题，大模型负责自然语言表达，减少模型自由发挥造成的错误。")
    with c:
        st.markdown("### 审核知识约束")
        st.write("回答只能使用与问题固定映射的权威知识条目，并通过知识引用与事实引用校验。")

    st.divider()
    st.markdown("### 为什么不是直接把截图丢给通用大模型")
    st.write(
        "通用模型能生成流畅的单晚解读，但容易套用固定比例、过度推断因果，或忽略个人趋势。本方案把竞争力放在持续数据、个人基线、稳定输出和可审查知识上。"
    )
    st.markdown("### 为什么同时保留推荐追问和自由提问")
    st.write("推荐追问降低用户不知道该问什么的门槛；自由提问承接用户自己的疑问。前者保证核心场景稳定，后者保证产品不会变成只能点击固定问题的封闭工具。")


def render_evaluation() -> None:
    st.markdown("## 真实模型回归测试")
    st.write("回答模型使用 qwen3.7-plus。四个冻结场景均完成真实接口测试，并通过结构、内容、知识引用与证据引用检查。")
    c1, c2, c3 = st.columns(3)
    c1.metric("冻结场景", "4")
    c2.metric("通过", "4")
    c3.metric("通过率", "100%")

    st.write("")
    rows = [
        {"案例": "P01", "场景": "处于个人典型范围", "推荐": "DS01", "结果": "通过"},
        {"案例": "P03", "场景": "单晚时长与占比偏低", "推荐": "DS21", "结果": "通过"},
        {"案例": "P04", "场景": "近期重复偏低", "推荐": "DS15", "结果": "通过"},
        {"案例": "P05", "场景": "时长低、占比正常", "推荐": "null", "结果": "通过"},
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("### 当前产品边界")
        st.markdown(
            """
            - MVP 当前只覆盖深睡指标卡
            - 可穿戴数据用于趋势参考，不用于疾病诊断
            - 公开 Demo 使用脱敏、预先验收的模型输出
            - 自由提问使用本地审核知识模拟，不调用线上模型
            - 尚未直接接入 Apple Health / HealthKit
            """
        )
    with right:
        st.markdown("### 下一阶段")
        st.markdown(
            """
            - 扩展 REM 与夜间清醒指标
            - 增加更多边界与异常数据测试
            - 为自由提问接入版本化 RAG 与回答评测
            - 通过 iOS HealthKit 获取授权数据
            - 验证真实用户是否更容易理解睡眠结果
            """
        )


def main() -> None:
    st.set_page_config(
        page_title="眠知 · AI睡眠助手",
        page_icon="🌙",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    with st.sidebar:
        st.markdown("## 体验不同睡眠场景")
        selected = st.radio(
            "选择场景",
            list(SCENARIOS),
            label_visibility="collapsed",
        )
        st.divider()
        st.markdown("**当前版本**  ")
        st.caption("SLEEP-END-TO-END-V1.1")
        st.markdown("**演示方式**  ")
        st.caption("使用已通过百炼真实模型验收的脱敏案例，不消耗线上 API。")

    config = SCENARIOS[selected]
    payload = load_case(config["file"])
    inject_css(config["accent"])
    render_header()

    product_tab, architecture_tab, evaluation_tab = st.tabs(["产品体验", "方案与架构", "评测与边界"])
    with product_tab:
        render_product(selected, config, payload)
    with architecture_tab:
        render_architecture()
    with evaluation_tab:
        render_evaluation()


if __name__ == "__main__":
    main()
