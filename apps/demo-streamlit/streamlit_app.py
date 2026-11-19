"""C-Trace demo app (offline hackathon fallback).  Run:  make demo-streamlit"""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ctrace import BRAND, TAGLINE, config
from ctrace.extraction import extract_document
from ctrace.pipeline import run
from ctrace.reduction import prove_reduction
from ctrace.report import FORMATS, build_facts, generate, to_pdf
from ctrace.verify import Verifier

TEAL, AMBER, RED, SLATE = "#0F766E", "#D97706", "#DC2626", "#475569"
SOURCE_COLORS = {"natural_gas": "#0F766E", "grid_electricity": "#38BDF8", "diesel": "#F59E0B"}
LABEL = {"natural_gas": "Natural gas", "grid_electricity": "Grid electricity", "diesel": "Diesel"}
CONF_COLOR = {"High": TEAL, "Medium": AMBER, "Low": RED}

st.set_page_config(page_title=f"{BRAND} · {TAGLINE}", page_icon="🟢", layout="wide")
st.markdown(f"""
<style>
  .ct-brand {{ font-size: 2.1rem; font-weight: 800; color: {TEAL}; letter-spacing: -0.02em; margin-bottom: 0; }}
  .ct-tag {{ color: {SLATE}; margin-top: -0.3rem; }}
  .ct-pill {{ display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.8rem; font-weight: 600; color: white; }}
  div[data-testid="stMetricValue"] {{ color: {TEAL}; }}
</style>""", unsafe_allow_html=True)


@st.cache_resource
def load_verifier() -> Verifier | None:
    p = config.MODELS_DIR / "ctrace_verifier.joblib"
    return Verifier.load(p) if p.exists() else None


@st.cache_data
def public_data():
    elec = pd.read_csv(config.PUBLIC_DIR / "bd_electricity_owid_ember.csv")
    em = pd.read_csv(config.PUBLIC_DIR / "bd_emissions_owid.csv")
    return elec, em


def extract_all(files: dict[str, bytes]) -> pd.DataFrame:
    rows = []
    prog = st.progress(0.0, text="C-Trace is reading your documents…")
    for i, (name, data) in enumerate(files.items()):
        mt = "application/pdf" if name.lower().endswith(".pdf") else f"image/{'jpeg' if name.lower().endswith(('jpg', 'jpeg')) else 'png'}"
        rows.append(extract_document(data, name, mt).as_dict())
        prog.progress((i + 1) / len(files), text=f"Read {name}")
    prog.empty()
    return pd.DataFrame(rows)


def load_demo():
    files = {p.name: p.read_bytes() for p in sorted(config.BILLS_DIR.glob("*.pdf"))}
    st.session_state.files = files
    st.session_state.records = extract_all(files)
    st.session_state.production = pd.read_csv(config.SAMPLES_DIR / "production.csv")
    st.session_state.profile = json.loads((config.SAMPLES_DIR / "factory_profile.json").read_text())
    st.session_state.accepted = set()


ss = st.session_state
ss.setdefault("files", {})
ss.setdefault("records", None)
ss.setdefault("production", None)
ss.setdefault("profile", None)
ss.setdefault("accepted", set())
verifier = load_verifier()

# ------------------------------------------------------------------ header & sidebar
c1, c2 = st.columns([3, 2])
with c1:
    st.markdown(f'<p class="ct-brand">{BRAND}</p><p class="ct-tag">{TAGLINE} Verifiable carbon MRV for factories — from paper bills to buyer-ready, bank-ready reports.</p>', unsafe_allow_html=True)
with c2:
    st.write("")
    mode = f"LLM extraction ON · {config.CTRACE_EXTRACTION_MODEL}" if config.llm_enabled() else "Offline mode · rules parser (set ANTHROPIC_API_KEY for vision + Bangla)"
    st.caption(f"⚙️ {mode}")
    st.caption("🧠 C-Trace Verifier v1 loaded" if verifier else "⚠️ Model not trained — run `make train`")

with st.sidebar:
    st.markdown(f"### {BRAND}")
    if st.button("▶ Load demo factory (65 sample bills)", type="primary"):
        load_demo()
    st.caption("Fictional factory: Nodi Knit Composite Ltd. Boiler retrofit Jan 2026, 600 kWp solar Apr 2026. Two planted data errors.")
    st.divider()
    up = st.file_uploader("Or upload your own bills (PDF, JPG, PNG)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
    prod_up = st.file_uploader("Production records CSV (period, pieces)", type=["csv"])
    if st.button("Read uploaded documents") and up:
        ss.files = {f.name: f.getvalue() for f in up}
        ss.records = extract_all(ss.files)
        ss.production = pd.read_csv(prod_up) if prod_up else ss.production
        ss.profile = ss.profile or json.loads((config.SAMPLES_DIR / "factory_profile.json").read_text())
        ss.accepted = set()
    if ss.profile:
        with st.expander("Factory profile"):
            ss.profile["name"] = st.text_input("Name", ss.profile["name"])
            ss.profile["factory_type"] = st.selectbox("Type", ["knit_composite", "woven", "denim_wash", "sweater"],
                                                      index=["knit_composite", "woven", "denim_wash", "sweater"].index(ss.profile["factory_type"]))
            ss.profile["workers"] = st.number_input("Workers", value=int(ss.profile["workers"]), step=100)
            ss.profile["has_wet_processing"] = int(st.checkbox("Wet processing (dyeing / washing)", bool(ss.profile["has_wet_processing"])))
            ss.profile["captive_gas_gen"] = int(st.checkbox("Captive gas generator", bool(ss.profile.get("captive_gas_gen", 0))))
            st.json({"solar": ss.profile.get("solar"), "interventions": ss.profile.get("interventions")})

tabs = st.tabs(["① Extract & review", "② Emissions", "③ Verify", "④ Prove reduction", "⑤ Reports", "Model & public data"])

if ss.records is None:
    with tabs[0]:
        st.info("Click **Load demo factory** in the sidebar, or upload bills. C-Trace reads each document, you confirm the values, and every tonne stays traceable to its source.")
    with tabs[5]:
        elec, em = public_data()
        st.subheader("Bangladesh context (public data: Our World in Data / Ember)")
        st.line_chart(em.set_index("year")[["co2"]], height=250)
    st.stop()

# ------------------------------------------------------------------ ① extract & review
records = ss.records.copy()
with tabs[0]:
    st.subheader("What C-Trace read from your documents")
    st.caption("Edit any value before confirming. The model only reads; C-Trace's deterministic engine does the maths.")
    cols = ["doc_name", "activity", "period", "quantity", "unit", "amount_bdt", "bill_no", "supplier", "extraction_method", "extraction_confidence"]
    edited = st.data_editor(records[cols], num_rows="fixed", hide_index=True, height=320, key="editor",
                            disabled=["doc_name", "extraction_method", "extraction_confidence", "unit"])
    records[cols] = edited
    with_issues = records[records.issues.map(len) > 0]
    if len(with_issues):
        st.markdown("**Document checks**")
        for _, r in with_issues.iterrows():
            st.warning(f"**{r.doc_name}** — " + " · ".join(r.issues))
    pre = run(records, ss.production, ss.profile, None)
    if pre["suggestions"]:
        st.markdown("**C-Trace correction suggestions** (needs a human to accept)")
        for s in pre["suggestions"]:
            key = f"{s['doc_name']}"
            colA, colB = st.columns([4, 1])
            colA.error(f"**{s['doc_name']}**: printed {s['printed_quantity']:,.0f} → suggested **{s['suggested_quantity']:,.0f}**. {s['explanation']}")
            if colB.checkbox("Accept", key=f"acc_{key}", value=key in ss.accepted):
                ss.accepted.add(key)
            else:
                ss.accepted.discard(key)
        for s in pre["suggestions"]:
            if s["doc_name"] in ss.accepted:
                i = records.index[records.doc_name == s["doc_name"]]
                records.loc[i, "quantity"] = s["suggested_quantity"]
                records.loc[i, "issues"] = records.loc[i, "issues"].map(lambda notes: [x for x in notes if "tariff" not in x.lower()] + ["Quantity corrected by reviewer (amount ÷ factory median rate); keep the corrected bill on file"])
                records.loc[i, "extraction_method"] = records.loc[i, "extraction_method"] + "+reviewer"

out = run(records, ss.production, ss.profile, verifier)
ledger, summary, verification = out["ledger"], out["summary"], out["verification"]

# ------------------------------------------------------------------ ② emissions
with tabs[1]:
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Scope 1 + 2", f"{summary.total_t.sum():,.0f} tCO2e")
    k2.metric("Scope 1 (gas, diesel)", f"{summary.scope1_t.sum():,.0f} t")
    k3.metric("Scope 2 (grid, location-based)", f"{summary.scope2_t.sum():,.0f} t")
    if "kgco2e_per_piece" in summary:
        k4.metric("Intensity", f"{summary.total_t.sum() * 1000 / summary.pieces.sum():.3f} kg/piece")
    fig = go.Figure()
    for a in ("natural_gas", "grid_electricity", "diesel"):
        if a in summary:
            fig.add_bar(x=summary.period, y=summary[a], name=LABEL[a], marker_color=SOURCE_COLORS[a])
    if "kgco2e_per_piece" in summary:
        fig.add_scatter(x=summary.period, y=summary.kgco2e_per_piece, name="kgCO2e / piece", yaxis="y2",
                        line=dict(color="#111827", width=2))
    for n, iv in enumerate(ss.profile.get("interventions", [])):
        fig.add_vline(x=iv["from"], line_dash="dot", line_color=SLATE)
        fig.add_annotation(x=iv["from"], y=1.10 - 0.06 * (n % 2), yref="paper", text=iv["measure"], showarrow=False,
                           xanchor="left", font=dict(size=10, color=SLATE))
    fig.update_layout(barmode="stack", height=420, yaxis_title="tCO2e", legend=dict(orientation="h", y=-0.15),
                      yaxis2=dict(overlaying="y", side="right", title="kgCO2e / piece", showgrid=False), margin=dict(t=40))
    st.plotly_chart(fig)
    st.subheader("Trace ledger — every tonne to its source")
    sel = st.selectbox("Pick a month to trace", summary.period.tolist(), index=len(summary) - 1)
    rows = ledger[ledger.period == sel]
    st.dataframe(rows[["trace_id", "activity", "quantity", "unit", "calculation", "tco2e", "ef_source", "doc_name", "doc_sha256"]], hide_index=True)
    for _, r in rows.iterrows():
        if r.doc_name in ss.files:
            st.download_button(f"Open source document: {r.doc_name}", ss.files[r.doc_name], file_name=r.doc_name, key=f"dl_{r.trace_id}")

# ------------------------------------------------------------------ ③ verify
with tabs[2]:
    if verification is None:
        st.warning("Train the model first: `make train`")
    else:
        counts = verification.confidence.value_counts()
        a, b, c = st.columns(3)
        a.metric("High confidence months", int(counts.get("High", 0)))
        b.metric("Medium", int(counts.get("Medium", 0)))
        c.metric("Low — human review", int(counts.get("Low", 0)))
        st.markdown(" ".join(f'<span class="ct-pill" style="background:{CONF_COLOR[r.confidence]}">{r.period}</span>'
                             for r in verification.itertuples()), unsafe_allow_html=True)
        st.caption("Expected use comes from the C-Trace Verifier: gradient-boosted models trained on a fleet of factories, calibrated to this factory's own history within each operating period.")
        carrier = st.radio("Energy carrier", ["gas_m3", "grid_kwh", "diesel_l"], horizontal=True,
                           format_func=lambda t: {"gas_m3": "Natural gas (m³)", "grid_kwh": "Grid electricity (kWh)", "diesel_l": "Diesel (L)"}[t])
        f2 = go.Figure()
        f2.add_scatter(x=verification.period, y=verification[f"expected_{carrier}"], name="Expected (model)", line=dict(color=SLATE, dash="dash"))
        f2.add_scatter(x=verification.period, y=verification[f"reported_{carrier}"], name="Reported (bills)", mode="lines+markers",
                       line=dict(color=TEAL), marker=dict(size=10, color=[CONF_COLOR[x] for x in verification.confidence]))
        f2.update_layout(height=360, legend=dict(orientation="h", y=-0.2), margin=dict(t=20))
        st.plotly_chart(f2)
        flagged = verification[verification.confidence != "High"]
        if len(flagged):
            st.subheader("Why these months were flagged")
            for _, r in flagged.iterrows():
                (st.error if r.confidence == "Low" else st.warning)(f"**{r.period} · {r.confidence}** — " + " · ".join(r.reasons))
        else:
            st.success("Every month is consistent with production, weather and assets.")
        st.caption("A flag means *check this document*, not *this factory is cheating*.")

# ------------------------------------------------------------------ ④ prove reduction
with tabs[3]:
    ivs = ss.profile.get("interventions", [])
    default_start = ivs[0]["from"] if ivs else summary.period.iloc[len(summary) // 2]
    periods_list = summary.period.tolist()
    cA, cB = st.columns(2)
    rep_start = cA.selectbox("Reporting period starts (first month after the project)", periods_list, index=periods_list.index(default_start))
    base_end = periods_list[max(periods_list.index(rep_start) - 1, 0)]
    cB.metric("Baseline period", f"{periods_list[0]} → {base_end}")
    monthly = out["monthly"].dropna(subset=["pieces"])
    base_rows = monthly[monthly.period <= base_end]
    if len(base_rows) < 6:
        st.warning("Need at least 6 baseline months.")
    else:
        low_months = set(verification[verification.confidence == "Low"].period) if verification is not None else set()
        bad_base = sorted(low_months & set(base_rows.period))
        if bad_base:
            st.warning(f"Baseline contains Low-confidence months {bad_base}. Accept or fix them in ① first — C-Trace excludes them from the baseline fit.")
        fit_df = monthly[~monthly.period.isin(bad_base)]
        curves, results = prove_reduction(fit_df, base_end, rep_start)
        ss.reductions = results
        med_tariff = (records.amount_bdt / records.quantity).groupby(records.activity).median()
        cols = st.columns(len(results))
        for col, r in zip(cols, results, strict=True):
            colname = "gas_m3" if r.activity == "natural_gas" else "grid_kwh"
            unit = "m³" if r.activity == "natural_gas" else "kWh"
            with col:
                st.markdown(f"#### {LABEL[r.activity]}")
                st.metric("Avoided emissions", f"{r.avoided_tco2e:,.0f} tCO2e",
                          f"90% range {r.avoided_tco2e_low:,.0f} – {r.avoided_tco2e_high:,.0f}")
                st.markdown(f'<span class="ct-pill" style="background:{TEAL if r.verified else AMBER}">{"VERIFIED" if r.verified else "NOT STATISTICALLY SIGNIFICANT"}</span>', unsafe_allow_html=True)
                st.caption(f"Avoided {r.avoided:,.0f} {unit} ({r.avoided / r.predicted:.1%}) ≈ BDT {r.avoided * med_tariff.get(r.activity, 0):,.0f} saved · "
                           f"baseline R² {r.r2} · CV(RMSE) {r.cv_rmse:.1%} (ASHRAE G14 monthly guide ≤ 15%)")
                f3 = go.Figure()
                f3.add_scatter(x=curves.period, y=curves[f"baseline_{colname}"], name="Adjusted baseline (no project)", line=dict(color=SLATE, dash="dash"))
                f3.add_scatter(x=fit_df.period, y=fit_df[colname], name="Actual", line=dict(color=SOURCE_COLORS[r.activity]))
                f3.add_vrect(x0=rep_start, x1=periods_list[-1], fillcolor=TEAL, opacity=0.07, line_width=0)
                f3.update_layout(height=320, legend=dict(orientation="h", y=-0.25), margin=dict(t=10))
                st.plotly_chart(f3, key=f"mv_{r.activity}")
        st.caption("Method: IPMVP Option C-style regression of baseline use on production and cooling degree days; savings = adjusted baseline − actual, with a 90% prediction interval. 'Verified' only when the lower bound is above zero.")

# ------------------------------------------------------------------ ⑤ reports
with tabs[4]:
    fmt = st.radio("Report for", list(FORMATS), horizontal=True, format_func=lambda k: FORMATS[k]["title"])
    pl = summary.period.tolist()
    r1, r2 = st.select_slider("Period", options=pl, value=(pl[0], pl[-1]))
    if st.button("Generate C-Trace report", type="primary"):
        facts = build_facts(ss.profile, summary, ledger, verification, ss.get("reductions"), r1, r2)
        with st.spinner("Retrieving guidance and writing the report…"):
            report = generate(fmt, facts)
        st.markdown(f"### {report['title']}")
        st.write(report["narrative"])
        st.caption(f"Narrative: {report['method']} · guidance retrieved: " + "; ".join(f"{g['title']}" for g in report["guidance"]))
        st.download_button("⬇ Download PDF", to_pdf(report, ledger), file_name=f"C-Trace_{fmt}_{r1}_{r2}.pdf", mime="application/pdf")
        st.download_button("⬇ Download data (JSON)", json.dumps(report["facts"], indent=2, default=str), file_name=f"C-Trace_{fmt}_{r1}_{r2}.json")

# ------------------------------------------------------------------ model & public data
with tabs[5]:
    card = config.MODELS_DIR / "MODEL_CARD.md"
    if card.exists():
        st.markdown(card.read_text())
    elec, em = public_data()
    st.subheader("Public data used")
    g1, g2 = st.columns(2)
    with g1:
        st.caption("Bangladesh grid carbon intensity, gCO2e/kWh (Ember via Our World in Data) — C-Trace's Scope 2 factor")
        st.line_chart(elec.set_index("year")[["carbon_intensity_elec"]].dropna(), height=250)
    with g2:
        st.caption("Bangladesh CO2 emissions, Mt (Our World in Data)")
        st.line_chart(em.set_index("year")[["co2"]], height=250)
