"""C-Trace Copilot: answers questions about one factory's own C-Trace data and C-Trace's guidance, in English or Bangla.

The Copilot never computes or invents a number. It calls tools that return verified data, and every answer must:
  * cite trace IDs, reduction IDs or knowledge-base chunk IDs, and
  * pass the number guard against the tool results (otherwise a safe answer is returned instead).

Two engines:
  * offline (default, no API key): intent routing + tool calls + templated answers in English or Bangla;
  * LLM (Claude with ANTHROPIC_API_KEY, or an OpenAI-compatible model with OPENAI_API_KEY): a tool-use loop over the
    same tools, with the same citation and number checks.
Questions outside the factory's data and the guidance are declined.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from . import config
from .report import number_guard
from .textnorm import BN_MONTHS, EN_MONTHS, normalize_text

Tools = dict[str, Callable[..., Any]]
LABEL = {"en": {"natural_gas": "natural gas", "grid_electricity": "grid electricity", "diesel": "diesel"},
         "bn": {"natural_gas": "প্রাকৃতিক গ্যাস", "grid_electricity": "গ্রিড বিদ্যুৎ", "diesel": "ডিজেল"}}
TOOL_SPECS = [
    {"name": "get_summary", "description": "Monthly and total Scope 1/2 emissions for the factory (tCO2e), optionally for one month (YYYY-MM) or one year (YYYY).",
     "input_schema": {"type": "object", "properties": {"period": {"type": "string", "description": "YYYY-MM, YYYY, or empty for all months"}},
                      "required": ["period"], "additionalProperties": False}},
    {"name": "get_ledger", "description": "Trace ledger rows (trace_id, quantity, tCO2e, bill) for a month and optionally one energy source.",
     "input_schema": {"type": "object", "properties": {"period": {"type": "string"}, "activity": {"type": "string", "enum": ["", "natural_gas", "grid_electricity", "diesel"]}},
                      "required": ["period", "activity"], "additionalProperties": False}},
    {"name": "get_verification", "description": "C-Trace Verifier grades, reasons, expected ranges and drivers, for all months or one month.",
     "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"], "additionalProperties": False}},
    {"name": "get_reductions", "description": "Proven reductions per intervention (avoided energy, tCO2e, interval, BDT, verified, signed).",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "search_guidance", "description": "Search C-Trace's guidance summaries (GHG Protocol, IFRS S2, BSEC, Bangladesh Bank, IPMVP, FAQ).",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}},
]


@dataclass
class Answer:
    text: str
    language: str
    citations: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    method: str = "offline"
    declined: bool = False

    def as_dict(self) -> dict:
        return {"text": self.text, "language": self.language, "citations": self.citations, "tool_calls": self.tool_calls,
                "method": self.method, "declined": self.declined}


def detect_language(text: str) -> str:
    return "bn" if sum("ঀ" <= ch <= "৿" for ch in text) >= 2 else "en"


def find_period(text: str, known: list[str]) -> str | None:
    """'July 2026', 'Jul', '2026-07', 'জুলাই ২০২৬', 'জুলাই' → a month present in the data (latest if no year)."""
    t = normalize_text(text).lower()
    m = re.search(r"(20\d{2})-(0[1-9]|1[0-2])", t)
    if m:
        return m.group(0)
    year = re.search(r"\b(20\d{2})\b", t)
    month = None
    for name, num in BN_MONTHS.items():
        if name in t:
            month = num
    for abbr, num in EN_MONTHS.items():
        if re.search(rf"\b{abbr}[a-z]*\b", t):
            month = num
    if month is None:
        return None
    cands = [p for p in known if int(p[5:7]) == month and (not year or p.startswith(year.group(1)))]
    return max(cands) if cands else None


INTENTS = {
    "why": (r"\bwhy\b|went up|go up|increase|rose|higher|lower|decrease|drop|change|কেন|বেড়েছে|বাড়ল|কমেছে|কমল|পরিবর্তন"),
    "savings": (r"sav(e|ed|ing)|reduc|retrofit|boiler|solar|econom|intervention|সাশ্রয়|হ্রাস|কমানো|বয়লার|সৌর"),
    "flags": (r"flag|low confidence|anomal|check|review|problem|wrong|issue|missing|যাচাই|সমস্যা|ভুল|পতাকা"),
    "total": (r"total|how much|emission|scope|intensity|tco2|tonne|ton|কত|মোট|নির্গমন|স্কোপ"),
    "guidance": (r"what is|what does|explain|meaning|ifrs|bsec|ghg protocol|taxonomy|passport|location-based|market-based|ipmvp|"
                 r"scope 3|bank|buyer|brand|কী|কি\b|মানে|ব্যাংক|ক্রেতা"),
}


def classify(q: str) -> list[str]:
    ql = q.lower()
    return [k for k, pat in INTENTS.items() if re.search(pat, ql)]


def _guarded(ans: Answer, results: list[Any]) -> Answer:
    bad = number_guard(ans.text, {"results": results})
    if bad:  # never show a number the tools did not return
        return Answer(text=("Sorry — I could not produce an answer that uses only verified figures." if ans.language == "en"
                            else "দুঃখিত — শুধু যাচাই-করা সংখ্যা দিয়ে উত্তর তৈরি করা গেল না।"),
                      language=ans.language, tool_calls=ans.tool_calls, method=ans.method + " (number guard blocked a draft)")
    return ans


def _n(x: float, d: int = 1) -> str:
    return f"{x:,.{d}f}"


# ------------------------------------------------------------------ offline engine
def answer_offline(question: str, tools: Tools, language: str | None = None) -> Answer:
    lang = language or detect_language(question)
    calls: list[dict] = []
    results: list[Any] = []

    def call(name: str, **kw: Any) -> Any:
        out = tools[name](**kw)
        calls.append({"tool": name, "args": kw})
        results.append(out)
        return out

    summary = call("get_summary", period="")
    known = [m["period"] for m in summary.get("months", [])]
    period = find_period(question, known)
    intents = classify(question)

    if "savings" in intents:
        reds = call("get_reductions")
        q = question.lower()
        picked = [r for r in reds if (("boiler" in q or "বয়লার" in q) and r["activity"] == "natural_gas")
                  or (("solar" in q or "সৌর" in q) and r["activity"] == "grid_electricity")] or reds
        if not picked:
            text = ("No reduction has been proven yet. Run verification, then compute reductions on the Reductions page." if lang == "en"
                    else "এখনও কোনো হ্রাস প্রমাণিত হয়নি। আগে যাচাই চালান, তারপর রিডাকশন পেজে হিসাব করুন।")
            return _guarded(Answer(text, lang, [], calls), results)
        parts, cites = [], []
        for r in picked:
            status = ("verified and signed" if r["status"] == "signed" else "verified, not yet signed") if r["verified"] else "not verified"
            if lang == "en":
                parts.append(f"{r.get('measure') or 'The intervention'} avoided {_n(r['avoided'], 0)} {r['unit']} of {LABEL['en'][r['activity']]} "
                             f"from {r['reporting_from']} to {r['reporting_to']} (90% range {_n(r['low'], 0)}–{_n(r['high'], 0)}), "
                             f"which is {_n(r['tco2e'])} tCO2e" + (f" and about BDT {_n(r['bdt_saved'], 0)}" if r.get("bdt_saved") else "")
                             + f" — {status}.")
            else:
                st = ("যাচাইকৃত ও স্বাক্ষরিত" if r["status"] == "signed" else "যাচাইকৃত, এখনও স্বাক্ষরিত নয়") if r["verified"] else "যাচাইকৃত নয়"
                parts.append(f"{r.get('measure') or 'উদ্যোগটি'} {r['reporting_from']} থেকে {r['reporting_to']} পর্যন্ত {_n(r['avoided'], 0)} {r['unit']} "
                             f"{LABEL['bn'][r['activity']]} সাশ্রয় করেছে (৯০% পরিসর {_n(r['low'], 0)}–{_n(r['high'], 0)}), অর্থাৎ {_n(r['tco2e'])} tCO2e"
                             + (f", প্রায় {_n(r['bdt_saved'], 0)} টাকা" if r.get("bdt_saved") else "") + f" — {st}।")
            cites.append({"type": "reduction", "id": r["id"], "label": r.get("measure") or r["activity"]})
        return _guarded(Answer(" ".join(parts), lang, cites, calls), results)

    if "flags" in intents and not ("why" in intents and period):
        ver = call("get_verification", period=period or "")
        if ver is None:
            text = "Verification has not been run yet." if lang == "en" else "যাচাই এখনও চালানো হয়নি।"
            return _guarded(Answer(text, lang, [], calls), results)
        low = [r for r in ver["results"] if r["confidence"] == "Low"]
        med = [r for r in ver["results"] if r["confidence"] == "Medium"]
        if lang == "en":
            text = (f"C-Trace graded {len(low)} month(s) Low and {len(med)} Medium. "
                    + " ".join(f"{r['period']}: {r['reasons'][0]}" + ("." if not r["reasons"][0].endswith(".") else "") for r in low)
                    + " A Low grade means a person should check the bill; it is not an accusation.")
        else:
            text = (f"C-Trace {len(low)}টি মাস Low এবং {len(med)}টি Medium গ্রেড দিয়েছে। "
                    + " ".join(f"{r['period']}: {r['reasons'][0]}।" for r in low) + " Low মানে বিলটি একজন মানুষকে মিলিয়ে দেখতে হবে; এটি অভিযোগ নয়।")
        cites = [{"type": "verification", "id": f"{ver['run']['id']}:{r['period']}", "label": r["period"]} for r in low]
        return _guarded(Answer(text, lang, cites, calls), results)

    if period and ("why" in intents or "total" in intents or not intents):
        i = known.index(period)
        cur = summary["months"][i]
        led = call("get_ledger", period=period, activity="")
        top = sorted(led, key=lambda r: -r["tco2e"])[:3]
        cites = [{"type": "trace", "id": r["trace_id"], "label": f"{r['doc_name']}"} for r in top]
        if "why" in intents and i > 0:
            prev = summary["months"][i - 1]
            change = cur.get("change_vs_previous_t") or {}
            delta = {a: float(change.get(a, 0.0)) for a in ("natural_gas", "grid_electricity", "diesel")}
            main = max(delta, key=lambda a: abs(delta[a]))
            ver = call("get_verification", period=period)
            row = ver["results"][0] if ver and ver["results"] else None
            target = {"natural_gas": "gas_m3", "grid_electricity": "grid_kwh", "diesel": "diesel_l"}[main]
            drivers = row["z"][target]["drivers"] if row and row["z"].get(target) else None
            up = cur["total_t"] >= prev["total_t"]
            if lang == "en":
                text = (f"Emissions in {period} were {_n(cur['total_t'])} tCO2e, {'up' if up else 'down'} from {_n(prev['total_t'])} in {prev['period']}. "
                        f"The largest change was {LABEL['en'][main]} ({'+' if delta[main] >= 0 else '−'}{_n(abs(delta[main]))} t).")
                if drivers:
                    named = sorted(((k, drivers[k]) for k in ("production", "weather", "solar", "season")), key=lambda kv: -abs(kv[1]))[:2]
                    text += (" Compared with a typical month, the C-Trace Verifier attributes the expected "
                             + LABEL["en"][main] + " use mostly to " + " and ".join(f"{k} ({'+' if v >= 0 else '−'}{_n(abs(v), 0)})" for k, v in named) + ".")
                if row:
                    text += f" The month is graded {row['confidence']}."
            else:
                text = (f"{period}-এ নির্গমন ছিল {_n(cur['total_t'])} tCO2e, {prev['period']}-এর {_n(prev['total_t'])} থেকে {'বেশি' if up else 'কম'}। "
                        f"সবচেয়ে বড় পরিবর্তন {LABEL['bn'][main]}-এ ({'+' if delta[main] >= 0 else '−'}{_n(abs(delta[main]))} t)।")
                if row:
                    text += f" মাসটির গ্রেড {row['confidence']}।"
            return _guarded(Answer(text, lang, cites, calls), results)
        if lang == "en":
            text = (f"In {period} the factory emitted {_n(cur['total_t'])} tCO2e: Scope 1 {_n(cur['scope1_t'])} and location-based Scope 2 {_n(cur['scope2_t'])}. "
                    f"The largest rows are cited below; open a trace ID to see its bill.")
        else:
            text = f"{period}-এ কারখানার নির্গমন {_n(cur['total_t'])} tCO2e: স্কোপ ১ {_n(cur['scope1_t'])}, স্কোপ ২ {_n(cur['scope2_t'])}। নিচের ট্রেস আইডি খুললে বিল দেখা যাবে।"
        return _guarded(Answer(text, lang, cites, calls), results)

    if "total" in intents and not ("guidance" in intents and "scope" in question.lower() and "what" in question.lower()):
        year = re.search(r"\b(20\d{2})\b", normalize_text(question))
        if year and any(k.startswith(year.group(1)) for k in known):
            summary = call("get_summary", period=year.group(1))   # "in 2025" means that year, not the whole ledger
        tot = summary.get("totals") or {}
        if not tot:
            return _guarded(Answer("No emissions in the ledger yet." if lang == "en" else "লেজারে এখনও কোনো নির্গমন নেই।", lang, [], calls), results)
        if lang == "en":
            text = (f"From {summary['period_from']} to {summary['period_to']} ({tot['months']} months) the factory emitted {_n(tot['total_tco2e'])} tCO2e: "
                    f"Scope 1 {_n(tot['scope1_tco2e'])} and location-based Scope 2 {_n(tot['scope2_location_tco2e'])}"
                    + (f"; intensity {tot['kgco2e_per_piece']} kg CO2e per piece." if tot.get("kgco2e_per_piece") else "."))
        else:
            text = (f"{summary['period_from']} থেকে {summary['period_to']} ({tot['months']} মাস) মোট নির্গমন {_n(tot['total_tco2e'])} tCO2e: "
                    f"স্কোপ ১ {_n(tot['scope1_tco2e'])}, স্কোপ ২ {_n(tot['scope2_location_tco2e'])}।")
        return _guarded(Answer(text, lang, [{"type": "summary", "id": "ledger", "label": "Trace ledger"}], calls), results)

    if "guidance" in intents or intents:
        hits = call("search_guidance", query=question)
        if hits:
            h = hits[0]
            sentences = re.split(r"(?<=[.!?])\s+", h["text"])
            body = " ".join(sentences[:3])
            text = (f"{body} (Source to consult: {h['source']}.)" if lang == "en"
                    else f"C-Trace নির্দেশিকা (ইংরেজিতে): {body} (মূল সূত্র: {h['source']}।)")
            return _guarded(Answer(text, lang, [{"type": "kb", "id": h["id"], "label": h["title"]}], calls), results)

    text = ("I can answer questions about this factory's C-Trace data — emissions, months, flags, reductions — and about C-Trace's guidance "
            "on reporting. Try “Why did emissions change in July 2026?” or “How much did the boiler retrofit save?”" if lang == "en" else
            "আমি এই কারখানার C-Trace তথ্য (নির্গমন, মাস, যাচাই, হ্রাস) এবং প্রতিবেদন-নির্দেশিকা নিয়ে প্রশ্নের উত্তর দিতে পারি। যেমন: “জুলাই ২০২৬-এ নির্গমন কেন বদলেছে?”")
    return Answer(text, lang, [], calls, declined=True)


# ------------------------------------------------------------------ Claude engine
CITE_RE = re.compile(r"\[(trace|kb|reduction):([^\]\s]+)\]")


def answer_llm(question: str, tools: Tools, language: str, client: Any, max_steps: int = 5) -> Iterator[dict]:
    """Yields events: {"type": "tool", ...} as tools run, then {"type": "answer", "answer": {...}}. Falls back to offline."""
    from .llm import load_prompt

    prompt = load_prompt(config.CTRACE_COPILOT_PROMPT)
    lang_line = "Answer in Bangla." if language == "bn" else "Answer in English."
    messages: list[dict] = [{"role": "user", "content": question}]
    calls: list[dict] = []
    results: list[Any] = []
    for _ in range(max_steps):
        resp = client.messages.create(model=config.CTRACE_REPORT_MODEL, max_tokens=2000, system=f"{prompt.system}\n\n{lang_line}",
                                      tools=[{**t, "strict": True} for t in TOOL_SPECS], tool_choice={"type": "auto"},
                                      output_config={"effort": "low"}, messages=messages)
        if resp.stop_reason == "refusal":
            break
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text").strip()
            cites = [{"type": k, "id": v, "label": v} for k, v in CITE_RE.findall(text)]
            clean_text = CITE_RE.sub("", text).strip()
            if not cites:
                break  # an uncited answer is not accepted
            ans = _guarded(Answer(clean_text, language, cites, calls, method=f"llm:{config.CTRACE_REPORT_MODEL}"), results)
            yield {"type": "answer", "answer": ans.as_dict()}
            return
        tool_results = []
        for u in uses:
            out = tools[u.name](**u.input)
            calls.append({"tool": u.name, "args": u.input})
            results.append(out)
            yield {"type": "tool", "tool": u.name, "args": u.input}
            tool_results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(out, default=str)[:20000]})
        messages.append({"role": "user", "content": tool_results})
    fallback = answer_offline(question, tools, language)
    fallback.method = "offline (Claude answer not accepted)"
    yield {"type": "answer", "answer": fallback.as_dict()}


def answer_llm_openai(question: str, tools: Tools, language: str, client: Any, max_steps: int = 5) -> Iterator[dict]:
    """The same loop over an OpenAI-compatible endpoint (function calling). Same citation and number checks."""
    from .llm import load_prompt
    from .llm_openai import to_openai_tools

    prompt = load_prompt(config.CTRACE_COPILOT_PROMPT)
    model = config.openai_models()[0]
    lang_line = "Answer in Bangla." if language == "bn" else "Answer in English."
    messages: list[dict] = [{"role": "system", "content": f"{prompt.system}\n\n{lang_line}"}, {"role": "user", "content": question}]
    calls: list[dict] = []
    results: list[Any] = []
    for _ in range(max_steps):
        resp = client.chat(model=model, max_tokens=8000, tools=to_openai_tools(TOOL_SPECS), tool_choice="auto", messages=messages)
        msg = resp["choices"][0]["message"]
        if msg.get("refusal"):
            break
        uses = msg.get("tool_calls") or []
        if not uses:
            text = (msg.get("content") or "").strip()
            cites = [{"type": k, "id": v, "label": v} for k, v in CITE_RE.findall(text)]
            if not cites:
                break  # an uncited answer is not accepted
            ans = _guarded(Answer(CITE_RE.sub("", text).strip(), language, cites, calls, method=f"llm:{model}"), results)
            if "number guard blocked" in ans.method:
                break  # a free model's draft used a figure the tools did not return: give the verified offline answer
            yield {"type": "answer", "answer": ans.as_dict()}
            return
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": uses})
        for u in uses:
            name = u["function"]["name"]
            try:
                args = json.loads(u["function"].get("arguments") or "{}")
                out = tools[name](**args) if name in tools else {"error": f"unknown tool {name}"}
            except (json.JSONDecodeError, TypeError) as exc:
                args, out = {}, {"error": f"bad arguments: {exc}"}
            calls.append({"tool": name, "args": args})
            results.append(out)
            yield {"type": "tool", "tool": name, "args": args}
            messages.append({"role": "tool", "tool_call_id": u["id"], "content": json.dumps(out, default=str)[:20000]})
    fallback = answer_offline(question, tools, language)
    fallback.method = "offline (LLM answer not accepted)"
    yield {"type": "answer", "answer": fallback.as_dict()}


def ask(question: str, tools: Tools, language: str | None = None, client: Any = None) -> Iterator[dict]:
    lang = language or detect_language(question)
    from .llm_openai import OpenAICompat

    if isinstance(client, OpenAICompat) or (client is None and config.llm_provider() == "openai"):
        from .llm_openai import client as openai_client
        try:
            yield from answer_llm_openai(question, tools, lang, client or openai_client(timeout_s=config.CTRACE_COPILOT_TIMEOUT_S, attempts=1))
            return
        except Exception as exc:  # a free endpoint can be rate-limited or down: answer offline instead of failing
            logging.getLogger("ctrace.copilot").warning("llm.copilot_failed", extra={"fields": {"error": f"{type(exc).__name__}: {exc}"[:400]}})
            ans = answer_offline(question, tools, lang)
            ans.method = "offline (LLM unavailable)"
            for c in ans.tool_calls:
                yield {"type": "tool", **c}
            yield {"type": "answer", "answer": ans.as_dict()}
            return
    if client is not None or config.llm_enabled():
        from .llm import client as default_client
        yield from answer_llm(question, tools, lang, client or default_client())
        return
    ans = answer_offline(question, tools, lang)
    for c in ans.tool_calls:
        yield {"type": "tool", **c}
    yield {"type": "answer", "answer": ans.as_dict()}
