import asyncio
import json
import logging
import os
from datetime import datetime
import re
from pathlib import Path

from openai import AsyncOpenAI

"""Agent 层 — 封装 DeepSeek 调用，运行各类分析师。"""


logger = logging.getLogger("stock2.agents")

PROMPTS_DIR = Path(__file__).parent / "prompts"
MODEL = "deepseek-chat"
BASE_URL = "https://api.deepseek.com/v1"

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("dspkey")
        if not api_key:
            raise RuntimeError(
                "未设置 DEEPSEEK_API_KEY 环境变量。"
                "Windows: set DEEPSEEK_API_KEY=sk-xxx"
            )
        _client = AsyncOpenAI(api_key=api_key, base_url=BASE_URL)
    return _client


# ============================================================
# Prompt 加载
# ============================================================
def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt 不存在：{path}")
    return path.read_text(encoding="utf-8")


def _render(template: str, **kwargs) -> str:
    """用 {{key}} 占位符渲染，避免与 markdown 的 {} 冲突。"""
    out = template
    for k, v in kwargs.items():
        out = out.replace(f"{{{{{k}}}}}", str(v))
    return out


# ============================================================
# JSON 解析
# ============================================================
def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON。多次兜底。"""
    text = text.strip()

    # 1. 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 找 ```json ... ``` 块
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 找最外层 { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return {"error": "JSON 解析失败", "raw": text[:1000]}


# ============================================================
# 底层 LLM 调用
# ============================================================
async def _call_llm(prompt: str,
                    system: str = "",
                    temperature: float = 0.3,
                    max_tokens: int = 4000) -> str:
    client = _get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    logger.debug("LLM call | prompt_len=%d | model=%s", len(prompt), MODEL)

    resp = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = resp.choices[0].message.content or ""
    logger.debug("LLM resp | resp_len=%d | tokens=%s",
                 len(content),
                 getattr(resp.usage, "total_tokens", "?"))
    return content


# ============================================================
# 基本面分析师
# ============================================================
async def analyze_fundamental(pre: dict) -> dict:
    """基本面分析师。"""
    template = _load_prompt("fundamental")
    prompt = _render(
        template,
        basic_text=pre.get("basic_text", ""),
        financial_text=pre.get("financial_text", ""),
        indicators_text=pre.get("indicators_text", ""),
    )

    try:
        raw = await _call_llm(prompt)
        result = _extract_json(raw)
        result["_agent"] = "fundamental"
        if "_raw" not in result and "error" in result:
            result["_raw"] = raw
        logger.info(
            "fundamental done | score=%s | rating=%s",
            result.get("score"), result.get("rating"),
        )
        return result
    except Exception as e:
        logger.exception("fundamental failed")
        return {
            "_agent": "fundamental",
            "error": f"{type(e).__name__}: {e}",
        }

# ============================================================
# 事件分析师
# ============================================================
async def analyze_event(pre: dict) -> dict:
    """事件分析师。"""
    template = _load_prompt("event")
    prompt = _render(
        template,
        basic_text=pre.get("basic_text", ""),
        announcements_text=pre.get("announcements_text", ""),
    )
    try:
        raw = await _call_llm(prompt)
        result = _extract_json(raw)
        result["_agent"] = "event"
        logger.info(
            "event done | sentiment=%s | key=%s",
            result.get("overall_sentiment"),
            str(result.get("key_event", ""))[:40],
        )
        return result
    except Exception as e:
        logger.exception("event failed")
        return {"_agent": "event", "error": f"{type(e).__name__}: {e}"}


# ============================================================
# 风险评估师
# ============================================================
async def analyze_risk(pre: dict) -> dict:
    """风险评估师。"""
    template = _load_prompt("risk")
    prompt = _render(
        template,
        basic_text=pre.get("basic_text", ""),
        financial_text=pre.get("financial_text", ""),
        indicators_text=pre.get("indicators_text", ""),
        announcements_text=pre.get("announcements_text", ""),
        quote_text=pre.get("quote_text", ""),
    )
    try:
        raw = await _call_llm(prompt)
        result = _extract_json(raw)
        result["_agent"] = "risk"
        logger.info(
            "risk done | level=%s | score=%s",
            result.get("risk_level"), result.get("risk_score"),
        )
        return result
    except Exception as e:
        logger.exception("risk failed")
        return {"_agent": "risk", "error": f"{type(e).__name__}: {e}"}


# ============================================================
# 行业分析师
# ============================================================
async def analyze_industry(pre: dict) -> dict:
    """行业分析师。"""
    template = _load_prompt("industry")
    prompt = _render(
        template,
        basic_text=pre.get("basic_text", ""),
        financial_text=pre.get("financial_text", ""),
        announcements_text=pre.get("announcements_text", ""),
    )
    try:
        raw = await _call_llm(prompt)
        result = _extract_json(raw)
        result["_agent"] = "industry"
        logger.info(
            "industry done | outlook=%s | score=%s",
            result.get("industry_outlook"), result.get("industry_score"),
        )
        return result
    except Exception as e:
        logger.exception("industry failed")
        return {"_agent": "industry", "error": f"{type(e).__name__}: {e}"}


# ============================================================
# 并行调度：同时跑 4 个分析师
# ============================================================
async def run_all_analysts(pre: dict) -> dict:
    """并行运行 4 个分析师。

    返回：
        {
            "fundamental": {...},
            "event": {...},
            "risk": {...},
            "industry": {...},
            "elapsed": float,
        }
    """
    import time
    t0 = time.time()

    results = await asyncio.gather(
        analyze_fundamental(pre),
        analyze_event(pre),
        analyze_risk(pre),
        analyze_industry(pre),
        return_exceptions=True,
    )

    names = ["fundamental", "event", "risk", "industry"]
    out = {}
    for name, r in zip(names, results):
        if isinstance(r, Exception):
            logger.error("%s raised | %s", name, r)
            out[name] = {"_agent": name, "error": str(r)}
        else:
            out[name] = r

    out["elapsed"] = round(time.time() - t0, 1)
    logger.info("all analysts done | elapsed=%.1fs", out["elapsed"])
    return out

# ============================================================
# 综合评审
# ============================================================
async def judge(pre: dict, analyst_results: dict,
                history_text: str = "",
                scenario_hint: str = "") -> str:
    """综合评审，输出 Markdown 报告（YAML frontmatter + 正文）。

    参数：
        pre: 预处理后的数据 dict
        analyst_results: 4 个分析师的结果
        history_text: 历史报告文本（可选）
        scenario_hint: 场景提示（可选，不强制）
    """
    from .rule_book import load_rules_text

    template = _load_prompt("judge")

    # ---- 加载规则库 ----
    rules_text = load_rules_text(scenario_hint)
    logger.info("judge rules loaded | scenario=%s | len=%d",
                scenario_hint or "all", len(rules_text))

    # ---- 各分析师结果 ----
    fund = analyst_results.get("fundamental", {})
    event = analyst_results.get("event", {})
    risk = analyst_results.get("risk", {})
    industry = analyst_results.get("industry", {})

    prompt = _render(
        template,
        ts_code=pre.get("ts_code", ""),
        name=pre.get("name", ""),
        today=datetime.now().strftime("%Y-%m-%d"),
        basic_text=pre.get("basic_text", ""),
        financial_text=pre.get("financial_text", ""),
        indicators_text=pre.get("indicators_text", ""),
        announcements_text=pre.get("announcements_text", ""),
        quote_text=pre.get("quote_text", ""),
        fundamental_score=fund.get("score", "?"),
        fundamental_json=json.dumps(
            {k: v for k, v in fund.items() if not k.startswith("_")},
            ensure_ascii=False, indent=2,
        ),
        event_sentiment=event.get("overall_sentiment", "?"),
        event_json=json.dumps(
            {k: v for k, v in event.items() if not k.startswith("_")},
            ensure_ascii=False, indent=2,
        ),
        risk_level=risk.get("risk_level", "?"),
        risk_score=risk.get("risk_score", "?"),
        risk_json=json.dumps(
            {k: v for k, v in risk.items() if not k.startswith("_")},
            ensure_ascii=False, indent=2,
        ),
        industry_score=industry.get("industry_score", "?"),
        industry_outlook=industry.get("industry_outlook", "?"),
        industry_json=json.dumps(
            {k: v for k, v in industry.items() if not k.startswith("_")},
            ensure_ascii=False, indent=2,
        ),
        rules_text=rules_text,
    )

    # 若有历史报告，追加到 prompt 末尾
    if history_text:
        prompt += f"\n\n# 历史分析（供参考，避免重复犯错）\n\n{history_text}"

    try:
        raw = await _call_llm(prompt, temperature=0.2, max_tokens=8000)
        logger.info("judge done | report_len=%d", len(raw))
        return raw
    except Exception as e:
        logger.exception("judge failed")
        return f"# 分析失败\n\n{type(e).__name__}: {e}\n"


# ============================================================
# 完整 pipeline
# ============================================================
async def analyze_full(ts_code: str,
                       save: bool = True,
                       use_cache: bool = True,
                       scenario_hint: str = "",
                       force_refresh: bool = False) -> dict:
    """完整分析 pipeline。

    流程：
        1. 检查当天是否已有报告（use_cache）
        2. 抓数据
        3. 预处理
        4. 读历史报告（最近 5 份）
        5. 并行 4 分析师
        6. 综合评审
        7. 保存报告

    返回：
        {
            "ts_code": str,
            "name": str,
            "report": str,          # Markdown 全文
            "report_path": Path,    # 保存路径（若 save=True）
            "cached": bool,         # 是否命中缓存
            "analysts": dict,       # 4 个分析师的 JSON
            "elapsed": float,
        }
    """
    from .data_fetcher import fetch_all
    from .preprocessor import preprocess
    from . import report_store as rs
    import time

    t0 = time.time()

    # ---- 1. 检查缓存 ----
    if use_cache and rs.latest_report_exists(ts_code):
        files = rs.list_reports(ts_code, 1)
        if files:
            content = files[0].read_text(encoding="utf-8")
            logger.info("cache hit | %s | %s", ts_code, files[0].name)
            return {
                "ts_code": ts_code,
                "name": "",
                "report": content,
                "report_path": files[0],
                "cached": True,
                "analysts": {},
                "elapsed": 0,
            }

    # ---- 2. 抓数据 ----
    # data = fetch_all(ts_code)
    # pre = preprocess(data)
    
    # ---- 2. 抓数据（走缓存） ----
    from .data_cache import get_data, refresh_cache
    if force_refresh:
        data = refresh_cache(ts_code)
    else:
        data = get_data(ts_code, max_age_hours=24.0)
    pre = preprocess(data)

    # ---- 3. 读历史报告 ----
    history_text = rs.load_recent_reports_text(ts_code, limit=5)

    # ---- 4. 并行分析师 ----
    analysts = await run_all_analysts(pre)

    # ---- 5. 综合评审 ----
    report_text = await judge(
        pre, analysts,
        history_text=history_text,
        scenario_hint=scenario_hint,
    )

    # ---- 6. 保存 ----
    report_path = None
    if save:
        report_path = rs.save_report(ts_code, pre.get("name", ""), report_text)

    elapsed = round(time.time() - t0, 1)
    logger.info("analyze_full done | %s | elapsed=%.1fs", ts_code, elapsed)

    return {
        "ts_code": ts_code,
        "name": pre.get("name", ""),
        "report": report_text,
        "report_path": report_path,
        "cached": False,
        "analysts": analysts,
        "elapsed": elapsed,
    }


# ============================================================
# 测试入口（升级：跑全部 4 个分析师）
# ============================================================
async def _test_all(ts_code: str):
    """测试全部 4 个分析师。"""
    from .data_fetcher import fetch_all
    from .preprocessor import preprocess

    print(f"\n{'='*60}\n测试 4 个分析师 | {ts_code}\n{'='*60}\n")

    data = fetch_all(ts_code)
    pre = preprocess(data)
    print(f"股票：{pre['name']} ({pre['ts_code']})\n")
    print(f"启动并行分析（4 个 agent）...\n")

    results = await run_all_analysts(pre)

    print(f"\n{'='*60}\n全部完成 | 耗时 {results['elapsed']}s\n{'='*60}\n")

    for name in ["fundamental", "event", "risk", "industry"]:
        r = results.get(name, {})
        print(f"\n{'─'*60}")
        print(f"【{name.upper()}】")
        print(f"{'─'*60}")
        print(json.dumps(
            {k: v for k, v in r.items() if not k.startswith("_")},
            ensure_ascii=False, indent=2,
        ))

# ============================================================
# 测试入口
# ============================================================
async def _test(ts_code: str):
    """独立测试：只跑基本面分析。"""
    from .data_fetcher import fetch_all
    from .preprocessor import preprocess

    print(f"\n{'='*60}\n测试基本面分析师 | {ts_code}\n{'='*60}\n")

    data = fetch_all(ts_code)
    pre = preprocess(data)
    print(f"股票：{pre['name']} ({pre['ts_code']})\n")

    result = await analyze_fundamental(pre)

    print(f"\n{'='*60}\n分析结果\n{'='*60}\n")
    print(json.dumps(
        {k: v for k, v in result.items() if not k.startswith("_")},
        ensure_ascii=False, indent=2,
    ))

    if result.get("_raw"):
        print(f"\n{'='*60}\n原始输出\n{'='*60}\n")
        print(result["_raw"][:2000])


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "688508.SH"
    if os.getenv("TEST_MODE") == "all":
        asyncio.run(_test_all(code))
    else:
        asyncio.run(_test(code))


