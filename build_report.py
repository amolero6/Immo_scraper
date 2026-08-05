"""
build_report.py
---------------
Generate the local HTML market report ("the website") for all populations.

    python build_report.py              # all three towns
    python build_report.py sant_cugat   # just one

Output goes to reports/:
    reports/index.html          overview with headline cards per town
    reports/<population>.html   full interactive report per town
    reports/plotly.min.js       shared chart library (written once)

Also exports the ranked excel per town ({population}_offer_candidates_400_700k.xlsx)
so the daily routine is just:  python main.py  →  python build_report.py.

Everything is self-contained and offline: open reports/index.html in any
browser (double-click), or share the whole reports/ folder with your partner.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

import analysis as an

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# How many listings from each table feed the enrichment shortlist. Covers the
# full top-40 of each ranking, deduped across the three lists (heavy overlap —
# a good home is often in both quality and leverage). This is safe to set high
# because the shortlist is generated ONLY-UNENRICHED (build_enrich_shortlist,
# only_unenriched=True) and enrich_details.py's @file mode also skips
# already-enriched: so after the first fill, each run only visits genuinely
# NEW top-40 entrants — a handful — not the whole list every time.
SHORTLIST_N_QUALITY = 40
SHORTLIST_N_STUCK = 20
SHORTLIST_N_LEVERAGE = 40

# ---------------------------------------------------------------------------
# Palette (validated reference palette, light mode)
# ---------------------------------------------------------------------------
C = {
    "blue": "#2a78d6", "aqua": "#1baf7a", "yellow": "#eda100", "green": "#008300",
    "violet": "#4a3aa7", "red": "#e34948", "magenta": "#e87ba4", "orange": "#eb6834",
    "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "axis": "#c3c2b7", "surface": "#fcfcfb", "page": "#f9f9f7",
    "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
}

PLOTLY_LAYOUT = dict(
    font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', size=13, color=C["ink2"]),
    paper_bgcolor=C["surface"], plot_bgcolor=C["surface"],
    margin=dict(l=55, r=20, t=42, b=40),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
)
AXIS = dict(gridcolor=C["grid"], linecolor=C["axis"], zerolinecolor=C["grid"])

CSS = """
:root { --ink:#0b0b0b; --ink2:#52514e; --muted:#898781; --grid:#e1e0d9;
        --surface:#fcfcfb; --page:#f9f9f7; --blue:#2a78d6; --good:#0ca30c;
        --warning:#fab219; --critical:#d03b3b; --border:rgba(11,11,11,.10); }
* { box-sizing:border-box; }
body { margin:0; background:var(--page); color:var(--ink);
       font-family:system-ui,-apple-system,"Segoe UI",sans-serif; line-height:1.45; }
.wrap { max-width:1240px; margin:0 auto; padding:24px 20px 80px; }
h1 { font-size:26px; margin:8px 0 2px; } h2 { font-size:19px; margin:38px 0 6px; }
.sub { color:var(--ink2); font-size:14px; margin-bottom:18px; }
.nav { display:flex; gap:10px; flex-wrap:wrap; margin:14px 0 6px; }
.nav a { text-decoration:none; color:var(--blue); font-weight:600; font-size:14px;
         padding:7px 14px; background:var(--surface); border:1px solid var(--border); border-radius:8px; }
.nav a.active { background:var(--blue); color:#fff; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(165px,1fr)); gap:12px; margin:16px 0; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }
.card .v { font-size:24px; font-weight:700; margin-top:2px; }
.card .l { font-size:12px; color:var(--ink2); }
.card .d { font-size:11.5px; color:var(--muted); margin-top:3px; }
.verdict { border-left:4px solid var(--blue); background:var(--surface); border-radius:8px;
           padding:12px 16px; margin:14px 0; font-size:14.5px; }
.sig { font-size:13.5px; margin:3px 0; color:var(--ink2); }
.chart { background:var(--surface); border:1px solid var(--border); border-radius:10px;
         padding:8px 6px 2px; margin:14px 0; }
.note { font-size:13px; color:var(--ink2); background:var(--surface); border:1px dashed var(--grid);
        border-radius:8px; padding:10px 14px; margin:10px 0; }
table.data { border-collapse:collapse; width:100%; font-size:12.8px; background:var(--surface);
             border:1px solid var(--border); border-radius:10px; overflow:hidden; }
table.data th { text-align:left; padding:8px 9px; background:#f2f1ee; cursor:pointer; white-space:nowrap;
                position:sticky; top:0; user-select:none; font-size:12px; color:var(--ink2); }
table.data th:hover { color:var(--ink); }
table.data td { padding:7px 9px; border-top:1px solid var(--grid); vertical-align:top; }
table.data tr:hover td { background:#f6f5f2; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
.tag { display:inline-block; font-size:11px; font-weight:600; padding:2px 8px; border-radius:10px; }
.tag.ruthless { background:#fbe3e3; color:#a02222; } .tag.firm { background:#fdeeda; color:#9a6002; }
.tag.standard { background:#e4ecf9; color:#1c5cab; } .tag.fast { background:#e2f3ea; color:#006300; }
.tag.ok { background:#e2f3ea; color:#006300; } .tag.comfortable { background:#e2f3ea; color:#006300; }
.tag.stretch { background:#fdeeda; color:#9a6002; } .tag.con_extras { background:#fdeeda; color:#9a6002; }
.tag.en_meses { background:#efe9fb; color:#4a3aa7; } .tag.fuera_de_alcance { background:#fbe3e3; color:#a02222; }
.help { border:1px solid var(--border); background:var(--surface); border-radius:10px;
        padding:4px 16px 6px; margin:12px 0; }
.help summary { font-weight:600; font-size:14px; cursor:pointer; padding:8px 0; color:var(--blue); }
.help dt { font-weight:600; font-size:13px; margin-top:10px; }
.help dd { margin:2px 0 0 0; font-size:13px; color:var(--ink2); }
.scroll { overflow-x:auto; border-radius:10px; }
details summary { cursor:pointer; color:var(--blue); font-size:12px; }
details p { font-size:12px; color:var(--ink2); margin:6px 0 0; max-width:560px; }
ul.why { font-size:12px; color:var(--ink2); margin:6px 0 0; padding-left:4px;
         max-width:520px; min-width:300px; list-style:none; }
ul.why li { margin:3px 0; }
ul.why li:last-child { margin-top:7px; font-weight:600; color:var(--ink); }
a.plink { color:var(--blue); text-decoration:none; font-weight:600; }
a.pid { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px; white-space:nowrap; }
#inspector-chart { min-height:0; }
#inspector-meta code { font-size:11px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
footer { margin-top:44px; font-size:12.5px; color:var(--muted); }
@media (max-width:700px){ .wrap{padding:14px 8px 60px;} h1{font-size:21px;} }
"""

SORT_JS = """
document.querySelectorAll("table.data").forEach(tbl => {
  tbl.querySelectorAll("th").forEach((th, idx) => {
    th.addEventListener("click", () => {
      const dir = th.dataset.dir === "asc" ? -1 : 1;
      tbl.querySelectorAll("th").forEach(h => delete h.dataset.dir);
      th.dataset.dir = dir === 1 ? "asc" : "desc";
      const rows = Array.from(tbl.tBodies[0].rows);
      rows.sort((a, b) => {
        const av = a.cells[idx].dataset.v ?? a.cells[idx].innerText;
        const bv = b.cells[idx].dataset.v ?? b.cells[idx].innerText;
        const an = parseFloat(av), bn = parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn)) return (an - bn) * dir;
        return String(av).localeCompare(String(bv)) * dir;
      });
      rows.forEach(r => tbl.tBodies[0].appendChild(r));
    });
  });
});
"""


def fig_html(fig: go.Figure, heading: str = "") -> str:
    fig.update_layout(**PLOTLY_LAYOUT)
    fig.update_xaxes(**AXIS)
    fig.update_yaxes(**AXIS)
    head = f'<div style="font-weight:600;font-size:14.5px;padding:10px 12px 0;color:{C["ink"]}">{heading}</div>' if heading else ""
    return (
        '<div class="chart">' + head
        + pio.to_html(fig, include_plotlyjs=False, full_html=False,
                      config={"displayModeBar": False, "responsive": True})
        + "</div>"
    )


def fmt_eur(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    return f"{v:,.0f} €".replace(",", ".")


def html_table(df: pd.DataFrame, spec: list[tuple], table_id: str) -> str:
    """spec: list of (label, key, kind) — kind in {text,eur,num,pct,link,bucket,verdict,flag,rationale}."""
    head = "".join(
        f'<th class="{ "num" if kind in ("eur","num","pct") else "" }">{label}</th>'
        for label, _, kind in spec
    )
    rows = []
    for _, r in df.iterrows():
        tds = []
        for label, key, kind in spec:
            v = r.get(key)
            missing = v is None or (isinstance(v, float) and np.isnan(v))
            if kind == "eur":
                tds.append(f'<td class="num" data-v="{0 if missing else v}">{fmt_eur(v)}</td>')
            elif kind == "num":
                tds.append(f'<td class="num" data-v="{0 if missing else v}">{"–" if missing else f"{v:,.0f}".replace(",", ".")}</td>')
            elif kind == "pct":
                tds.append(f'<td class="num" data-v="{0 if missing else v}">{"–" if missing else f"{v:.1f}%"}</td>')
            elif kind == "flag":
                tds.append(f'<td data-v="{0 if missing else int(v)}">{"✓" if (not missing and v) else ""}</td>')
            elif kind == "link":
                tds.append(f'<td><a class="plink" href="{v}" target="_blank" rel="noopener">ver ↗</a></td>' if not missing and v else "<td>–</td>")
            elif kind == "pid":
                txt = "" if missing else str(v)
                tds.append(
                    f'<td data-v="{txt}"><a class="plink pid" href="#inspector" data-pid="{txt}" '
                    f'title="Ver evolución de precio y eventos en el inspector">{txt}</a></td>'
                    if txt else "<td>–</td>"
                )
            elif kind == "bucket":
                txt = str(v or "")
                cls = "ruthless" if txt.startswith("ruthless") else "firm" if txt.startswith("firm") else "standard" if txt.startswith("standard") else "fast"
                short = txt.split("(")[0].strip()
                tds.append(f'<td data-v="{txt}"><span class="tag {cls}">{short}</span></td>')
            elif kind == "verdict":
                txt = str(v or "")
                cls = "en_meses" if txt.startswith("en_") else txt
                tds.append(f'<td data-v="{txt}"><span class="tag {cls}">{txt.replace("_", " ")}</span></td>')
            elif kind == "rationale":
                txt = str(v or "")
                items = "".join(f"<li>{part}</li>" for part in txt.split("; ") if part)
                tds.append(f'<td><details><summary>por qué</summary><ul class="why">{items}</ul></details></td>')
            else:
                txt = "" if missing else str(v)
                tds.append(f"<td>{txt[:110]}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return (
        f'<div class="scroll"><table class="data" id="{table_id}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        '<div class="note">Haz clic en una cabecera para ordenar. «por qué» abre la justificación de la oferta.</div>'
    )


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def chart_inventory_flows(m: pd.DataFrame, label: str) -> str:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.55, 0.45],
                        vertical_spacing=0.08,
                        subplot_titles=("Anuncios activos (banda 400–700k)", "Flujo diario"))
    fig.add_trace(go.Scatter(x=m["day"], y=m["inventory"], name="Inventario",
                             line=dict(color=C["blue"], width=2)), row=1, col=1)
    fig.add_trace(go.Bar(x=m["day"], y=m["new_listings"], name="Nuevos",
                         marker_color=C["aqua"]), row=2, col=1)
    fig.add_trace(go.Bar(x=m["day"], y=-m["delistings"], name="Retirados (confirmados)",
                         marker_color=C["red"]), row=2, col=1)
    fig.update_layout(height=430, barmode="relative")
    return fig_html(fig, f"{label} — inventario y flujos (banda 400–700k)")


def chart_market_trend(m: pd.DataFrame, label: str, days: int = 90) -> str:
    """General price trend across the wide market band (150k-1.5M), last N days.
    Dual Y-axis by explicit request: price (left, blue) vs inventory (right, aqua).
    Each axis is colored to match its line so the two are never read as sharing
    a scale, and only the price axis keeps gridlines to avoid a false crossing
    read between the two independently-scaled series."""
    recent = m[m["day"] >= (m["day"].max() - pd.Timedelta(days=days))]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=recent["day"], y=recent["median_price"], mode="lines",
                             line=dict(color=C["blue"], width=1), opacity=0.25,
                             name="Precio mediana diaria (€)", hoverinfo="skip", yaxis="y1"))
    fig.add_trace(go.Scatter(x=recent["day"], y=recent["median_price_7d"], mode="lines",
                             line=dict(color=C["blue"], width=2.6),
                             name="Precio mediana 7d (€)", yaxis="y1"))
    fig.add_trace(go.Scatter(x=recent["day"], y=recent["inventory"], mode="lines",
                             line=dict(color=C["aqua"], width=2.2, dash="dot"),
                             name="Inventario (nº anuncios)", yaxis="y2"))
    fig.update_layout(
        height=400,
        yaxis=dict(title=dict(text="Precio pedido (€)", font=dict(color=C["blue"])),
                   tickfont=dict(color=C["blue"]), gridcolor=C["grid"]),
        yaxis2=dict(title=dict(text="Inventario (nº anuncios)", font=dict(color=C["aqua"])),
                    tickfont=dict(color=C["aqua"]), overlaying="y", side="right",
                    showgrid=False, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig_html(fig, f"{label} — tendencia general del mercado (150.000–1.500.000 €, últimos {days} días)")


def chart_asking_vs_closing(m: pd.DataFrame, gap: pd.DataFrame | None) -> str:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=m["day"], y=m["median_ppsqm"], name="€/m² pedido (diario)",
                             line=dict(color=C["blue"], width=1), opacity=0.3, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=m["day"], y=m["median_ppsqm_7d"], name="€/m² pedido (mediana 7d)",
                             line=dict(color=C["blue"], width=2.6)))
    if gap is not None:
        zone_colors = {"sant_cugat": C["red"], "zip_08173": C["yellow"], "zip_08195": C["violet"]}
        for _, row in gap.iterrows():
            fig.add_hline(y=row["closing_mean_eur_m2_12m"], line_dash="dash", line_width=1.6,
                          line_color=zone_colors.get(row["zone"], C["muted"]),
                          annotation_text=f"cierre {row['zone']} ({row['closing_mean_eur_m2_12m']:,} €/m²)",
                          annotation_font_size=11)
    fig.update_layout(height=400, yaxis_title="€/m²")
    return fig_html(fig, "Precio PEDIDO (scrapeado) vs precio de CIERRE (notariado)")


def chart_pressure(m: pd.DataFrame) -> str:
    fig = make_subplots(rows=1, cols=2, subplot_titles=(
        "% del stock con ≥1 bajada de precio", "Meses de oferta (verde = mercado comprador)"))
    fig.add_trace(go.Scatter(x=m["day"], y=m["cut_breadth"] * 100, name="% con bajadas",
                             line=dict(color=C["violet"], width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=m["day"], y=m["months_of_supply"], name="Meses de oferta",
                             line=dict(color=C["orange"], width=2)), row=1, col=2)
    fig.add_hrect(y0=6, y1=12, fillcolor=C["good"], opacity=0.07, line_width=0, row=1, col=2)
    fig.add_hrect(y0=0, y1=4, fillcolor=C["critical"], opacity=0.06, line_width=0, row=1, col=2)
    # early days have tiny delisting denominators -> absurd spikes; keep scale readable
    fig.update_yaxes(range=[0, 12], row=1, col=2)
    fig.update_layout(height=330, showlegend=False)
    return fig_html(fig)


def chart_survival(km: pd.DataFrame, label: str) -> str:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=km["t"], y=km["survival"] * 100, mode="lines",
                             line=dict(color=C["blue"], width=2.4, shape="hv"),
                             name="% aún en mercado"))
    for q, lbl in [(75, "25% vendidos"), (50, "50% vendidos"), (25, "75% vendidos")]:
        hit = km[km["survival"] * 100 <= q]
        if len(hit):
            d = float(hit["t"].iloc[0])
            fig.add_vline(x=d, line_dash="dot", line_color=C["muted"], line_width=1,
                          annotation_text=f"{lbl}: {d:.0f}d", annotation_font_size=11)
    fig.update_layout(height=340, xaxis_title="Días publicado", yaxis_title="% aún en mercado",
                      showlegend=False)
    return fig_html(fig, f"{label} — supervivencia de anuncios (días hasta retirada)")


def chart_notary_cycle(baselines: dict) -> str:
    zones = [("sant_cugat", "Municipio", C["blue"]),
             ("zip_08173", "08173 (centro)", C["yellow"]),
             ("zip_08195", "08195 (Valldoreix/Mira-sol)", C["violet"])]
    fig = go.Figure()
    for key, name, color in zones:
        med = baselines[key]["annual_median_eur_m2"]
        years = [y for y in med if y.isdigit()]
        fig.add_trace(go.Bar(x=years, y=[med[y] for y in years], name=name, marker_color=color))
    fig.update_layout(height=380, barmode="group", yaxis_title="€/m² (cierre)")
    return fig_html(fig, "Precio de CIERRE mediano €/m² por año (notariado) — el ciclo real")


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

# Excelencia table: quality-forward, then the SAME negotiation columns the
# Top-40 carries — so you can see, for an excellent home, how much leverage it
# also has (usually little: that's the point) and its full offer ladder.
QUALITY_SPEC = [
    ("Título", "title", "text"), ("Barrio", "neighborhood", "text"), ("Fuente", "source", "text"),
    ("Precio pedido", "price", "eur"), ("m²", "sqm", "num"), ("Hab", "rooms", "num"), ("Baños", "bathrooms", "num"),
    ("Piscina", "has_pool", "flag"), ("A/C", "has_ac", "flag"), ("Terraza", "terrace", "flag"),
    ("Ascensor", "elevator", "flag"), ("Parking", "parking", "flag"),
    ("Insights descripción", "text_insights", "text"),
    ("Calidad", "quality_score", "text"),
    ("Valor mercado (pedido)", "hedonic_fair_price", "eur"),
    ("Sobreprecio vs mercado", "hedonic_residual_pct", "pct"),
    ("Valor cierre est.", "est_closing_value", "eur"),
    # negotiation dimension (same columns as the Top-40):
    ("Días online", "days_online_effective", "num"), ("Bajadas", "n_cuts", "num"),
    ("Desc. acum.", "cum_discount_pct", "pct"), ("Multi-agencia", "multi_listed", "flag"),
    ("Relistado", "relist_count", "flag"), ("Motivado", "kw_any_motivated", "flag"),
    ("Palanca", "leverage_score", "text"), ("Margen est.", "est_margin_pct", "pct"),
    ("① Agresiva", "offer_opening", "eur"), ("② Objetivo", "offer_target", "eur"),
    ("③ Prudente", "offer_prudent", "eur"), ("④ Tope", "offer_walkaway", "eur"),
    ("Estrategia", "bucket", "bucket"),
    ("Cuota/mes", "monthly_payment_needed", "eur"), ("Encaje", "affordability_verdict", "verdict"),
    ("Detalle", "rationale", "rationale"), ("Link", "url", "link"),
    ("ID", "property_id", "pid"),
]

STUCK_SPEC = [
    ("Título", "title", "text"), ("Barrio", "neighborhood", "text"), ("Fuente", "source", "text"),
    ("Precio pedido", "price", "eur"), ("m²", "sqm", "num"),
    ("Días online", "days_online_effective", "num"),
    ("Bajadas", "n_cuts", "num"), ("Desc. acum.", "cum_discount_pct", "pct"),
    ("Sobreprecio vs mercado", "hedonic_residual_pct", "pct"),
    ("Calidad", "quality_score", "text"),
    ("Margen est.", "est_margin_pct", "pct"),
    ("① Agresiva (ruthless)", "offer_opening", "eur"), ("② Objetivo", "offer_target", "eur"),
    ("Detalle", "rationale", "rationale"), ("Link", "url", "link"),
    ("ID", "property_id", "pid"),
]

STUCK_HELP = """
<details class="help"><summary>📖 Qué es esto y cómo trackearlo</summary><dl>
<dt>Buena casa + precio que no cede</dt><dd>Este segmento cruza dos condiciones: puntúa por encima de la mediana en calidad (tamaño/amenidades) Y lleva ≥30 días publicado con un precio &gt;10% por encima de lo que piden anuncios comparables. No es un anuncio mediocre estancado — es objetivamente bueno, pero el vendedor no ha movido precio pese a que el mercado no lo ha comprado. Ahí es donde una oferta agresiva («①» de la escalera) tiene sentido: no hay nada que perder pidiendo bajo.</dd>
<dt>Cómo se trackea sin herramientas nuevas</dt><dd>Cada vez que regeneréis el informe, las columnas «Bajadas» y «Desc. acum.» se leen en directo de la base de datos — si uno de estos empieza a bajar precio, lo veréis cambiar aquí de un día para otro mientras siga activo. No hace falta guardar nada aparte: la propia base de datos es el historial.</dd>
</dl></details>"""

SHORTLIST_HELP = f"""
<details class="help"><summary>📖 Cómo usar la shortlist de enriquecimiento</summary><dl>
<dt>Qué es</dt><dd>La cola de anuncios <b>pendientes de enriquecer</b>: cogemos el <b>top-{SHORTLIST_N_LEVERAGE} por palanca + top-{SHORTLIST_N_QUALITY} por calidad + estancados</b>, deduplicamos, y <b>quitamos los que ya se han visitado</b> (marca <code>enriched_at</code>, no la longitud de la descripción — así una agencia con descripción corta pero completa no se re-visita eternamente). Se recalcula en cada <code>build_report.py</code>.</dd>
<dt>El guardrail: solo lo nuevo</dt><dd>Como filtramos los ya enriquecidos, la lista solo contiene lo que <b>aún no habéis visitado</b>. La primera vez tras subir el límite se enriquecerá una tanda grande (todo el top-40 que falte); a partir de ahí, cada día solo aparecen los anuncios <b>nuevos</b> que entran al top-40. Cuando esté todo hecho, la lista queda vacía. Por eso podemos cubrir los 40 enteros sin re-visitar fichas ni disparar la detección de bots.</dd>
<dt>Cómo usarla</dt><dd><code>python enrich_details.py {{pop}} --property-ids @{{pop}}_enrich_shortlist.txt</code> — enriquece solo los pendientes. El modo <code>@fichero</code> además <b>vuelve a saltarse los ya enriquecidos</b> por seguridad (doble red). Si algún día queréis re-enriquecer todo el fichero, añadid <code>--force</code>. Las líneas con «#» son comentarios explicando por qué está cada uno.</dd>
</dl></details>"""

QUALITY_HELP = """
<details class="help"><summary>📖 Por qué esta tabla es distinta del Top 40 de abajo</summary><dl>
<dt>Filosofía opuesta a propósito</dt><dd>El Top 40 de candidatos premia la <b>distress</b>: días online, bajadas de precio, multi-agencia, vendedor motivado — es decir, busca dónde tenéis palanca de negociación. Un piso realmente excelente y bien tasado normalmente <b>no</b> tiene ninguna de esas señales: se vende rápido, sin bajar precio, sin necesitar tres agencias compitiendo. Si solo miraseis el Top 40, un piso excepcional recién publicado podría quedar por debajo de un chalet mediocre y estancado. Esta tabla corrige eso: puntúa tamaño, habitaciones, baños y amenidades (piscina/A·C/terraza/ascensor/parking), y NO penaliza ni premia los días online.</dd>
<dt>Por qué seguimos en 400–700k y no solo 500–600k</dt><dd>En esta zona el precio pedido corre sistemáticamente un 16–28% por encima del precio de cierre notarial — es la norma del mercado, no una señal de aviso por anuncio. Estrechar la banda a lo «razonable» descartaría buenas viviendas solo por estar tasadas como todo lo demás a su alrededor.</dd>
<dt>Sobreprecio vs mercado</dt><dd>Aquí NO comparamos contra el notariado (eso ya lo hace la sección «Pedido vs cierre» arriba) sino contra otros anuncios similares — así distinguimos «cara para lo que ofrece» de «cara porque todo el mercado está caro».</dd>
<dt>Las columnas de negociación también están aquí</dt><dd>Esta tabla muestra <b>las mismas columnas de negociación que el Top-40</b> (días online, bajadas, multi-agencia, palanca, margen estimado y la escalera de oferta ①→④), pero <b>solo como información</b>: NO afectan al orden, que se rige por «Calidad». Sirven para ver, de un piso excelente, cuánta palanca tiene además (normalmente poca — por eso está aquí y no en el Top-40) y hasta dónde podríais tensar la oferta. Con un piso así de bien posicionado, «③ Prudente» suele ser la apertura realista.</dd>
</dl></details>"""

# Top-40 table: negotiation-forward, then the SAME quality columns the
# Excelencia table carries — so you can see, for a high-leverage candidate,
# whether it's also a genuinely good home (amenities, insights, quality score).
CANDIDATE_SPEC = [
    ("Título", "title", "text"), ("Barrio", "neighborhood", "text"), ("Fuente", "source", "text"),
    ("Precio pedido", "price", "eur"), ("m²", "sqm", "num"), ("Hab", "rooms", "num"), ("Baños", "bathrooms", "num"),
    # quality dimension (same columns as Excelencia):
    ("Piscina", "has_pool", "flag"), ("A/C", "has_ac", "flag"), ("Terraza", "terrace", "flag"),
    ("Ascensor", "elevator", "flag"), ("Parking", "parking", "flag"),
    ("Insights descripción", "text_insights", "text"), ("Calidad", "quality_score", "text"),
    # negotiation dimension:
    ("Días online*", "days_online_effective", "num"), ("Bajadas", "n_cuts", "num"),
    ("Desc. acum.", "cum_discount_pct", "pct"),
    ("Multi-agencia", "multi_listed", "flag"), ("Relistado", "relist_count", "flag"),
    ("Motivado", "kw_any_motivated", "flag"),
    ("Valor mercado (pedido)", "hedonic_fair_price", "eur"),
    ("Sobreprecio vs mercado", "hedonic_residual_pct", "pct"),
    ("Valor cierre est.", "est_closing_value", "eur"),
    ("Score", "final_score", "text"),
    ("Margen est.", "est_margin_pct", "pct"),
    ("① Agresiva", "offer_opening", "eur"), ("② Objetivo", "offer_target", "eur"),
    ("③ Prudente", "offer_prudent", "eur"), ("④ Tope", "offer_walkaway", "eur"),
    ("Estrategia", "bucket", "bucket"),
    ("Cuota/mes", "monthly_payment_needed", "eur"), ("Encaje", "affordability_verdict", "verdict"),
    ("Meses ahorro", "months_to_feasible", "num"),
    ("Encaje a oferta ②", "affordability_target_verdict", "verdict"),
    ("Meses ahorro a ②", "months_to_feasible_target", "num"),
    ("Cash nec. a ②", "cash_needed_at_target", "eur"),
    ("Meses ahorro a ④", "months_to_feasible_walkaway", "num"),
    ("Cash nec. a ④", "cash_needed_at_walkaway", "eur"),
    ("Detalle", "rationale", "rationale"), ("Link", "url", "link"),
    ("ID", "property_id", "pid"),
]

CANDIDATE_COLUMNS_HELP = """
<details class="help"><summary>📖 Qué significa cada columna de la tabla de candidatos</summary><dl>
<dt>Columnas de calidad (Piscina → Calidad, Insights)</dt><dd>Esta tabla incluye <b>las mismas columnas de calidad/amenidades que la tabla Excelencia de arriba</b> (piscina, A/C, terraza, ascensor, parking, insights de la descripción y el «Calidad» score), como referencia — el orden aquí lo rige la palanca de negociación, no la calidad, pero así podéis ver de un candidato muy negociable si es <i>además</i> una buena vivienda o solo un anuncio castigado.</dd>
<dt>Días online*</dt><dd>Días desde la primera vez que vimos el anuncio. Si detectamos que un anuncio «nuevo» es un relistado (mismo piso, retirado y republicado para resetear el contador), hereda la antigüedad real.</dd>
<dt>Bajadas / Desc. acum.</dt><dd>Nº de bajadas de precio observadas y descuento acumulado desde el primer precio que vimos. Un vendedor que ya bajó y sigue sin vender tiene más margen.</dd>
<dt>Multi-agencia</dt><dd>El mismo piso está publicado por ≥2 agencias distintas → mandato no exclusivo → vendedor con prisa y agencias compitiendo. Buena palanca.</dd>
<dt>Relistado vs Reactivación (no son lo mismo)</dt><dd><b>Relistado</b>: detectamos un anuncio ACTIVO cuyas características (precio, m², habitaciones, título) coinciden con un anuncio distinto (ID de portal distinto) que se retiró hace poco — típico de agencias que borran y vuelven a publicar para resetear el contador de días. Compara <i>dos anuncios distintos</i>. <b>Reactivación</b>: el MISMO anuncio (mismo ID) desaparece y reaparece en nuestras pasadas de scraping — se cuenta sobre <i>el historial de un único anuncio</i>. Por eso puedes ver «relistado ✓» sin «reactivación»: cuando una agencia republica con ID nuevo, ese ID entra en nuestra base como anuncio nuevo, no como una reactivación de sí mismo — el ID viejo se queda inactivo para siempre y nunca genera ese evento. Una sola reactivación es poco concluyente (puede ser un fallo puntual del scraping) y no suma margen; hacen falta ≥2 para contar como señal real.</dd>
<dt>Motivado</dt><dd>El texto del anuncio contiene señales tipo «herencia», «urge», «a reformar», «negociable»…</dd>
<dt>🔑 Palanca (leverage_score) — el score que quizá no entiendes</dt><dd>Un número de <b>0 a 1</b> que resume, en una sola cifra, <b>cuánta capacidad de negociación</b> tenéis sobre ese anuncio. No es dinero ni un descuento — es una nota relativa frente al resto del stock. Se calcula ponderando cinco señales de que el vendedor está en posición débil:
<ul>
<li><b>35% — días online</b> (percentil): cuanto más lleva publicado sin venderse, más arriba.</li>
<li><b>25% — nº de bajadas de precio</b> (percentil): ya ha cedido y aún no ha vendido.</li>
<li><b>15% — multi-agencia</b>: publicado por ≥2 agencias (mandato no exclusivo).</li>
<li><b>10% — relistado</b>: republicado para ocultar su antigüedad real.</li>
<li><b>15% — texto motivado</b>: herencia/urge/negociable en la descripción.</li>
</ul>
Léelo así: <b>&gt;0,6 mucha palanca</b> (vendedor probablemente flexible), <b>0,3–0,6 media</b>, <b>&lt;0,3 poca</b> (anuncio fresco y sin señales — tendrás que pagar cerca de lo que pide). Es lo <i>contrario</i> de «Calidad»: un piso excelente y bien tasado suele tener palanca baja porque no necesita ceder. Diferencia clave con «Margen est.»: la <b>Palanca</b> es la nota 0–1 de <i>posición negociadora</i>; el <b>Margen est.</b> traduce esa posición (más el sobreprecio) a un <i>% de descuento concreto en euros</i>.</dd>
<dt>Los otros scores (Calidad, Valor, Score)</dt><dd><b>Calidad</b> (0–1): cómo de buena es la vivienda en sí (tamaño, habitaciones, baños, amenidades, bien tasada). <b>Valor</b> (0–1): cómo de barata está <i>para lo que ofrece</i> (residuo hedónico + €/m²). <b>Score</b> (final_score): la nota global que ordena el Top-40, mezcla de valor (35%) + palanca (30%) + encaje de cuota (20%) + estar en 500–600k (10%) + oportunidad de reforma (10%). Resumen: <b>Calidad</b>=¿es buen piso? · <b>Valor</b>=¿está barato? · <b>Palanca</b>=¿puedo negociar? · <b>Margen</b>=¿cuánto rebajo? · <b>Score</b>=nota global de este Top-40.</dd>
<dt>Valor mercado (pedido)</dt><dd>Lo que costaría este piso si estuviera <b>anunciado</b> al precio típico del mercado para sus características (modelo hedónico entrenado con los ~900 anuncios scrapeados). OJO: es valor entre precios PEDIDOS, no de cierre — por eso puede superar el precio del anuncio: significa que está barato <i>comparado con otros anuncios</i>.</dd>
<dt>Sobreprecio vs mercado</dt><dd>% que el precio pedido está por encima (+) o debajo (−) de ese valor de mercado pedido. Negativo = chollo relativo; positivo + muchos días online = sobrevalorado y castigado.</dd>
<dt>Valor cierre est.</dt><dd>El mismo valor, deflactado por la prima pedido-vs-cierre de la zona (notariado). Aproxima lo que pisos similares <b>escrituran</b> de verdad. Es tu ancla mental en la negociación.</dd>
<dt>Margen est.</dt><dd>Descuento negociable estimado sumando cada palanca (días online, bajadas, multi-agencia, relistado, sobreprecio…). El desglose exacto está en «por qué».</dd>
<dt>① Agresiva → ④ Tope</dt><dd>Escalera de oferta: ① ancla baja (margen completo, espera contraoferta) · ② donde los datos dicen que debería cerrar · ③ oferta con alta probabilidad de aceptación · ④ precio máximo que deberías pagar (capado a 620k).</dd>
<dt>Estrategia</dt><dd>Traducción del margen estimado a una postura negociadora:
<b>ruthless</b> (margen ≥12%: vendedor muy castigado — abre 12–18% por debajo y no tengas prisa) ·
<b>firm</b> (8–12%: abre ~8–12% por debajo, hay palancas claras) ·
<b>standard</b> (5–8%: negociación normal, abre ~5–8% por debajo) ·
<b>act fast</b> (&lt;5%: está bien de precio y volará — no juegues a lowball o lo pierdes; abre &lt;5% por debajo o a precio).</dd>
<dt>Detalle («por qué»)</dt><dd>Checklist completo de las palancas de negociación examinadas para ese anuncio:
<b>✓</b> = presente y suma margen (con su aporte en %) · <b>✗</b> = comprobada pero no aplica (con el valor real, p. ej. «solo 12 días online»).
Se revisan siempre: días online vs supervivencia de comparables, bajadas de precio (nº, acumulado y si son recientes),
multi-agencia, relistado, reactivaciones, texto de vendedor motivado, sobreprecio vs valor de mercado, €/m² vs cierre de zona
y el estado general del mercado. La suma de los ✓ es el «Margen est.». La última línea explica <b>por qué está en el top-40</b>,
que es cosa distinta del margen: se rankea por <i>valor</i> (barato para sus características) + <i>palanca</i> (señales de negociación)
+ <i>encaje</i> en tu presupuesto — por eso un piso caro «fuera de alcance» puede estar arriba si es muy buen valor y muy negociable.</dd>
<dt>Encaje / Meses ahorro</dt><dd>«Encaje» al precio PEDIDO: comfortable/ok (entra ya), con extras (entra usando el cash adicional), en N meses (ahorrando a tu ritmo), fuera de alcance (harían falta &gt;24 meses de ahorro — mejor negociar precio o tipo). «Meses ahorro» = meses a tu ritmo (tras usar extras) hasta que entre a ese precio; cuando la cuota al 80% no encaja, el cálculo asume más entrada (LTV efectivo 75%, 70%…) hasta mantener la cuota en tu tope. <b>«Encaje a oferta ②» es el que importa en los fuera de alcance</b>: si negocias hasta el precio objetivo ②, ¿entra, y con cuántos meses de ahorro? Las columnas «Cash nec. a ②/④» dan el cash total (entrada + gastos) que exigiría comprar al precio objetivo ② o al tope ④ — si la cuota al 80% no encajara, ese cash ya incluye la entrada extra para mantener la cuota en tu tope. «Meses ahorro a ④» = meses hasta poder comprarlo en el mejor caso negociado.</dd>
</dl></details>"""


# ---------------------------------------------------------------------------
# Property inspector (per-listing price evolution + entry/exit events)
# ---------------------------------------------------------------------------

INSPECTOR_HELP = """
<details class="help"><summary>📖 Qué es el inspector y qué incluye</summary><dl>
<dt>Qué hace</dt><dd>Pega un <code>property_id</code> (o haz clic en la columna «ID» de cualquier tabla) y verás la <b>vida completa del anuncio</b>: evolución de precio (escalones = bajadas), entrada al mercado (▲ verde), salida (✕ roja, si ya no está activo) y reactivaciones (★). Si el mismo piso está publicado por varias agencias (clúster) o fue <b>retirado y republicado con otro ID</b> (relistado), se dibujan TODOS sus anuncios juntos — así ves la antigüedad real, no la del contador reseteado.</dd>
<dt>Qué anuncios están disponibles</dt><dd>Los que aparecen en las tablas de este informe (top-100 por palanca, top-60 excelencia, estancados, motivados, reforma, shortlist) más sus hermanos de clúster/relistado. El desplegable del campo de búsqueda autocompleta los IDs disponibles. Para inspeccionar un anuncio fuera de estas listas, usa el notebook <code>inspect_*_visualization.ipynb</code> (celda del inspector), que trabaja contra la base de datos completa.</dd>
</dl></details>"""

INSPECT_JS = """
const INSPECT_COLORS = ['#2a78d6','#1baf7a','#eda100','#4a3aa7','#e87ba4','#eb6834'];
function inspectProp(pid){
  pid = (pid || '').trim();
  const meta = document.getElementById('inspector-meta');
  const chart = document.getElementById('inspector-chart');
  if (!pid) return;
  let key = null;
  if (INSPECT_DATA[pid]) key = pid;
  else for (const k in INSPECT_DATA){ if (INSPECT_DATA[k].members.some(m => m.id === pid)){ key = k; break; } }
  if (!key){
    meta.style.display = 'block';
    meta.innerHTML = '<b>' + pid + '</b>: no está en este informe. El inspector solo incluye los anuncios de las tablas y sus hermanos de clúster/relistado — para cualquier otro, usa el notebook de inspección contra la base de datos.';
    chart.style.display = 'none';
    return;
  }
  const e = INSPECT_DATA[key];
  const traces = [];
  e.members.forEach((m, i) => {
    const c = INSPECT_COLORS[i % INSPECT_COLORS.length];
    const x = m.history.map(h => h[0]), y = m.history.map(h => h[1]);
    if (!y.length) return;
    if (m.last_seen && m.last_seen > x[x.length-1]){ x.push(m.last_seen); y.push(y[y.length-1]); }
    traces.push({x: x, y: y, mode: 'lines+markers', line: {shape: 'hv', color: c, width: 2.2},
                 marker: {size: 5, color: c}, name: (m.source || '?') + ' · ' + m.id,
                 hovertemplate: '%{x} · %{y:,.0f} €<extra>' + m.id + '</extra>'});
    if (m.first_seen)
      traces.push({x: [m.first_seen], y: [y[0]], mode: 'markers',
                   marker: {symbol: 'triangle-up', size: 13, color: '#0ca30c'},
                   name: 'entrada', legendgroup: 'in', showlegend: i === 0,
                   hovertemplate: 'entrada %{x}<extra>' + m.id + '</extra>'});
    if (m.status !== 'active' && m.last_seen)
      traces.push({x: [m.last_seen], y: [y[y.length-1]], mode: 'markers',
                   marker: {symbol: 'x', size: 12, color: '#d03b3b'},
                   name: 'salida', legendgroup: 'out', showlegend: i === 0,
                   hovertemplate: 'salida %{x}<extra>' + m.id + '</extra>'});
    m.events.forEach(ev => {
      if (ev[1] === 'reactivated')
        traces.push({x: [ev[0]], y: [y[y.length-1]], mode: 'markers',
                     marker: {symbol: 'star', size: 13, color: '#eda100'},
                     name: 'reactivación', legendgroup: 're', showlegend: false,
                     hovertemplate: 'reactivación %{x}<extra>' + m.id + '</extra>'});
    });
  });
  chart.style.display = 'block';
  Plotly.newPlot(chart, traces, {
    font: {family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 13, color: '#52514e'},
    paper_bgcolor: '#fcfcfb', plot_bgcolor: '#fcfcfb', height: 380,
    margin: {l: 75, r: 20, t: 30, b: 40}, hovermode: 'closest',
    legend: {orientation: 'h', yanchor: 'bottom', y: 1.01, x: 0},
    xaxis: {gridcolor: '#e1e0d9', linecolor: '#c3c2b7'},
    yaxis: {gridcolor: '#e1e0d9', linecolor: '#c3c2b7', tickformat: ',.0f', ticksuffix: ' €'}
  }, {displayModeBar: false, responsive: true});
  const rows = e.members.map(m => {
    const st = m.status === 'active' ? '<span class="tag ok">activo</span>' : '<span class="tag fuera_de_alcance">inactivo</span>';
    const pr = m.price != null ? ' · ' + Math.round(m.price).toLocaleString('es-ES') + ' €' : '';
    const link = m.url ? ' · <a class="plink" href="' + m.url + '" target="_blank" rel="noopener">ver ↗</a>' : '';
    return '<div style="margin:3px 0"><code>' + m.id + '</code> ' + st + ' · ' + (m.first_seen || '?') + ' → ' + (m.last_seen || '?') + pr + link + '</div>';
  }).join('');
  meta.style.display = 'block';
  meta.innerHTML = '<b>' + e.title + '</b>'
    + (e.neighborhood ? ' · ' + e.neighborhood : '')
    + (e.sqm ? ' · ' + Math.round(e.sqm) + ' m²' : '')
    + (e.days_online_effective != null ? ' · <b>' + Math.round(e.days_online_effective) + ' días online efectivos</b>' : '')
    + (e.members.length > 1 ? ' · ' + e.members.length + ' anuncios del mismo piso (clúster/relistados)' : '')
    + '<div style="margin-top:6px">' + rows + '</div>';
}
document.getElementById('pid-go').addEventListener('click', () => inspectProp(document.getElementById('pid-input').value));
document.getElementById('pid-input').addEventListener('keydown', e => { if (e.key === 'Enter') inspectProp(e.target.value); });
document.addEventListener('click', e => {
  const a = e.target.closest('a.pid');
  if (a){ document.getElementById('pid-input').value = a.dataset.pid; inspectProp(a.dataset.pid); }
});
"""


def build_inspector_payload(feats: pd.DataFrame, data: dict, seed_ids: list) -> dict:
    """One entry per seed listing, expanded with every sibling listing of the
    same physical property: cluster members (multi-agency) plus the whole
    relist chain in both directions — so the inspector shows the property's
    real market life, not one portal counter."""
    props = data["properties"].drop_duplicates("property_id").set_index("property_id")
    hist = data["history"].copy()
    hist["date"] = pd.to_datetime(hist["date"], errors="coerce", utc=True)
    hist["price"] = pd.to_numeric(hist["price"], errors="coerce")
    hist = hist.dropna(subset=["date", "price"]).sort_values("date")
    hist_by = dict(tuple(hist.groupby("property_id")))
    ev = data["events"].copy()
    ev["event_date"] = pd.to_datetime(ev["event_date"], errors="coerce", utc=True)
    ev = ev.dropna(subset=["event_date"]).sort_values("event_date")
    ev_by = dict(tuple(ev.groupby("property_id")))

    fx = feats.drop_duplicates("property_id").set_index("property_id")
    relist_map = {}
    if "relisted_from" in fx.columns:
        relist_map = {k: v for k, v in fx["relisted_from"].dropna().items() if isinstance(v, str) and v}
    relist_children: dict = {}
    for child, parent in relist_map.items():
        relist_children.setdefault(parent, []).append(child)

    payload = {}
    for pid in dict.fromkeys(seed_ids):
        if not isinstance(pid, str) or pid not in props.index:
            continue
        members, frontier = set(), [pid]
        while frontier:
            cur = frontier.pop()
            if cur in members or cur not in props.index:
                continue
            members.add(cur)
            if cur in fx.index and "cluster_id" in fx.columns:
                cid = fx.at[cur, "cluster_id"]
                if pd.notna(cid):
                    frontier.extend(fx.index[fx["cluster_id"] == cid])
            if cur in relist_map:
                frontier.append(relist_map[cur])
            frontier.extend(relist_children.get(cur, []))

        mem_list = []
        for mid in sorted(members):
            p = props.loc[mid]
            fs, ls = p.get("first_seen"), p.get("last_seen")
            fs_s = fs.strftime("%Y-%m-%d") if pd.notna(fs) else None
            ls_s = ls.strftime("%Y-%m-%d") if pd.notna(ls) else None
            g = hist_by.get(mid)
            series = ([[d.strftime("%Y-%m-%d"), float(v)] for d, v in zip(g["date"], g["price"])]
                      if g is not None else [])
            first_price = p.get("price_first_seen")
            if pd.isna(first_price):
                first_price = p.get("price")
            if fs_s and pd.notna(first_price) and (not series or series[0][0] > fs_s):
                series.insert(0, [fs_s, float(first_price)])
            g = ev_by.get(mid)
            events = ([[d.strftime("%Y-%m-%d"), str(t)] for d, t in zip(g["event_date"], g["event_type"])]
                      if g is not None else [])
            mem_list.append({
                "id": mid,
                "source": str(p.get("source") or ""),
                "status": str(p.get("status") or ""),
                "first_seen": fs_s, "last_seen": ls_s,
                "price": float(p["price"]) if pd.notna(p.get("price")) else None,
                "url": str(p.get("url") or ""),
                "history": series, "events": events,
            })

        root = props.loc[pid]
        days = None
        if pid in fx.index and "days_online_effective" in fx.columns:
            d = fx.at[pid, "days_online_effective"]
            if pd.notna(d):
                days = round(float(d), 1)
        payload[pid] = {
            "title": str(root.get("title") or ""),
            "neighborhood": str(root.get("neighborhood") or ""),
            "sqm": float(root["sqm"]) if pd.notna(root.get("sqm")) else None,
            "days_online_effective": days,
            "members": mem_list,
        }
    return payload


def render_inspector(payload: dict) -> str:
    all_ids = sorted({m["id"] for v in payload.values() for m in v["members"]} | set(payload))
    options = "".join(f'<option value="{i}"></option>' for i in all_ids)
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        '<div class="note" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">'
        '<input id="pid-input" list="pid-list" placeholder="property_id (p. ej. idealista_111305065)…" '
        'style="flex:1;min-width:280px;padding:8px 12px;font-size:13px;border:1px solid var(--border);'
        'border-radius:8px;background:var(--surface);font-family:ui-monospace,SFMono-Regular,Menlo,monospace">'
        f'<datalist id="pid-list">{options}</datalist>'
        '<button id="pid-go" style="padding:8px 18px;font-size:13px;font-weight:600;color:#fff;'
        'background:var(--blue);border:none;border-radius:8px;cursor:pointer">Ver evolución</button>'
        f'<span style="font-size:12px;color:var(--muted)">{len(all_ids)} anuncios disponibles</span></div>'
        '<div id="inspector-meta" class="note" style="display:none"></div>'
        '<div id="inspector-chart" class="chart" style="display:none"></div>'
        f'<script>const INSPECT_DATA = {data_json};\n{INSPECT_JS}</script>'
    )


def build_town(population: str) -> dict:
    cfg = an.POPULATIONS[population]
    data = an.load_data(population)
    feats = an.build_features(data)
    hed = an.fit_hedonic(feats)
    feats = hed["features"]
    km = an.kaplan_meier(feats)
    panel = an.build_daily_panel(data["properties"], data["history"], data["events"])
    market = an.compute_market_daily(panel, feats, band=an.SEARCH_BAND)
    market_wide = an.compute_market_daily(panel, feats, band=an.MARKET_TREND_BAND)
    baselines = an.load_notariado()
    verdict = an.market_timing_verdict(market, baselines, population)
    gap = an.gap_analysis(feats, baselines, population)
    profile = an.load_profile()

    quality = an.score_quality(feats)
    quality = an.add_offer_columns(quality, km, verdict, gap, profile)
    stuck = an.score_stuck_excellence(quality, km)  # numeric quality_score, before string formatting below
    quality["quality_score"] = quality["quality_score"].map(lambda v: f"{v:.3f}")
    stuck["quality_score"] = stuck["quality_score"].map(lambda v: f"{v:.3f}")

    ranked = an.score_candidates(feats, profile)
    ranked = an.add_offer_columns(ranked, km, verdict, gap, profile)
    ranked["final_score"] = ranked["final_score"].map(lambda v: f"{v:.3f}")

    def _rank_note(r) -> str:
        parts = [
            f"valor {r['value_score']:.2f} (barato para lo que es)",
            f"palanca {r['leverage_score']:.2f} (señales de negociación)",
            f"encaje cuota {r['payment_fit']:.2f}",
        ]
        if r.get("reno_opportunity"):
            parts.append("oportunidad de reforma ✓")
        return f"POR QUÉ ESTÁ EN EL TOP (score {r['final_score']}): " + " · ".join(parts)

    ranked["rationale"] = ranked.apply(
        lambda r: f"{r.get('rationale', '')}; {_rank_note(r)}", axis=1
    )

    # excel export (same artifact as before, new band)
    excel_cols = [c for c in [
        "title", "neighborhood", "source", "price", "sqm", "rooms", "bathrooms",
        "has_pool", "has_ac", "terrace", "elevator", "parking", "text_insights", "quality_score",
        "days_online_effective",
        "n_cuts", "cum_discount_pct", "multi_listed", "relist_count", "n_reactivations",
        "kw_any_motivated", "kw_renovation", "hedonic_fair_price", "hedonic_residual_pct",
        "est_closing_value", "value_score", "leverage_score", "payment_fit", "reno_opportunity",
        "final_score", "est_margin_pct", "offer_opening", "offer_target", "offer_prudent",
        "offer_walkaway", "bucket", "monthly_payment_needed", "affordability_verdict",
        "months_to_feasible", "affordability_target_verdict", "months_to_feasible_target",
        "cash_needed_at_target", "months_to_feasible_walkaway", "cash_needed_at_walkaway",
        "monthly_payment_at_target", "rationale", "cluster_size", "n_sources", "property_id", "url"] if c in ranked.columns]
    excel_path = f"{population}_offer_candidates_400_700k.xlsx"
    ranked.head(100)[excel_cols].to_excel(excel_path, index=False)

    quality_excel_cols = [c for c in [
        "title", "neighborhood", "source", "price", "sqm", "rooms", "bathrooms",
        "has_pool", "has_ac", "terrace", "elevator", "parking", "text_insights",
        "quality_score", "hedonic_fair_price", "hedonic_residual_pct", "est_closing_value",
        "days_online_effective", "n_cuts", "cum_discount_pct", "multi_listed", "relist_count",
        "kw_any_motivated", "leverage_score", "est_margin_pct",
        "offer_opening", "offer_target", "offer_prudent", "offer_walkaway", "bucket",
        "monthly_payment_needed", "affordability_verdict", "rationale", "property_id", "url"] if c in quality.columns]
    quality_excel_path = f"{population}_excelencia_400_700k.xlsx"
    quality.head(60)[quality_excel_cols].to_excel(quality_excel_path, index=False)

    stuck_excel_cols = [c for c in [
        "title", "neighborhood", "source", "price", "sqm", "days_online_effective",
        "n_cuts", "cum_discount_pct", "hedonic_residual_pct", "quality_score",
        "est_margin_pct", "offer_opening", "offer_target", "rationale", "property_id", "url"] if c in stuck.columns]
    stuck_excel_path = f"{population}_excelencia_estancados_400_700k.xlsx"
    stuck[stuck_excel_cols].to_excel(stuck_excel_path, index=False)

    shortlist = an.build_enrich_shortlist(
        quality, stuck, ranked,
        n_quality=SHORTLIST_N_QUALITY, n_stuck=SHORTLIST_N_STUCK, n_leverage=SHORTLIST_N_LEVERAGE,
    )
    shortlist_path = an.write_enrich_shortlist(shortlist, population)

    last = market.dropna(subset=["inventory"]).iloc[-1]
    last28 = market.tail(28)
    active_band = feats[(feats.status == "active") & feats.price.between(*an.SEARCH_BAND)
                        & (feats.is_cluster_canonical == 1)]

    headline = {
        "label": cfg["label"],
        "active": len(active_band),
        "target": int(active_band.price.between(*an.TARGET_BAND).sum()),
        "ppsqm": last["median_ppsqm_7d"],
        "mos": last["months_of_supply"],
        "cut_breadth": last["cut_breadth"],
        "new28": int(last28["new_listings"].sum()),
        "del28": int(last28["delistings"].sum()),
        "buyer_score": verdict["buyer_score"],
        "verdict": verdict["verdict"],
    }

    # --- sections ---
    mos_conf = verdict["signals"].get("months_of_supply", {}).get("confianza", "ok")
    mos_note = "⚠ poca historia aún" if str(mos_conf).startswith("baja") else "&gt;6 comprador · &lt;4 vendedor"
    cards = f"""
    <div class="cards">
      <div class="card" title="Anuncios únicos (deduplicados entre portales/agencias) activos hoy con precio pedido entre 400k y 700k."><div class="l">Activas en banda búsqueda 400–700k</div><div class="v">{headline['active']}</div><div class="d">{headline['target']} en el objetivo 500–600k</div></div>
      <div class="card" title="Mediana del €/m² PEDIDO del stock activo en la banda, suavizada 7 días. No es precio de cierre."><div class="l">€/m² pedido (mediana 7d)</div><div class="v">{fmt_eur(headline['ppsqm'])}</div><div class="d">banda 400–700k</div></div>
      <div class="card" title="Inventario activo dividido por el ritmo de retiradas mensual: cuántos meses tardaría en venderse todo el stock al ritmo actual. Regla clásica: >6 meses favorece al comprador, <4 al vendedor. Con menos de ~5 meses de datos es orientativo."><div class="l">Meses de oferta</div><div class="v">{headline['mos']}</div><div class="d">{mos_note}</div></div>
      <div class="card" title="Porcentaje del stock activo cuyo precio actual es inferior al primer precio que le vimos. Si sube, los vendedores están cediendo."><div class="l">Stock con bajadas</div><div class="v">{headline['cut_breadth']:.0%}</div><div class="d">capitulación de vendedores</div></div>
      <div class="card" title="Anuncios nuevos vs retirados (confirmados tras 2 pasadas sin aparecer) en los últimos 28 días, dentro de la banda."><div class="l">Nuevos / retirados (28d)</div><div class="v">{headline['new28']} / {headline['del28']}</div><div class="d">flujo del mercado</div></div>
      <div class="card" title="Fracción de indicadores fiables que hoy favorecen al comprador (inventario subiendo, €/m² pedido bajando, más bajadas de precio…). Los indicadores con poca historia no puntúan."><div class="l">Buyer score</div><div class="v">{headline['buyer_score']}</div><div class="d">0 = vendedor · 1 = comprador</div></div>
    </div>
    <details class="help"><summary>📖 Cómo leer este informe (2 min)</summary><dl>
      <dt>Bandas de precio</dt><dd><b>Búsqueda 400–700k</b>: todo lo que analizamos (por debajo de 500k puede haber chollos a reformar; por encima de 600k los sobrevalorados pueden acabar cerrando en tu rango). <b>Objetivo 500–600k</b>, máximo absoluto 620k.</dd>
      <dt>Pedido vs cierre</dt><dd>Todo lo scrapeado son precios <b>PEDIDOS</b> (lo que el vendedor sueña). Las líneas «cierre» vienen del <b>notariado</b>: lo que de verdad se escritura. La distancia entre ambos (~16–28%) es el techo de negociación total de la zona.</dd>
      <dt>Meses de oferta</dt><dd>= inventario ÷ retiradas mensuales. Si hay 180 pisos activos y se retiran ~68/mes → 2,7 meses. Mercado rápido = vendedores fuertes. OJO: «retirado» ≠ «vendido» (hay retiradas y caducidades), y con pocos meses de datos el ratio baila — por eso este indicador se marca ⚪ y no puntúa en el buyer score hasta tener ~5 meses de historia.</dd>
      <dt>Supervivencia</dt><dd>De cada 100 anuncios publicados, cuántos siguen activos a los X días. Si un candidato lleva más días que la mayoría de su cohorte, el mercado ya rechazó su precio: palanca para ti.</dd>
      <dt>Semáforos</dt><dd>🟢 favorece al comprador · 🔴 favorece al vendedor · ⚪ sin datos suficientes (no puntúa).</dd>
    </dl></details>"""

    SIGNAL_NAMES = {
        "inventory_trend": "Inventario (tendencia 30d)",
        "asking_ppsqm_trend": "€/m² pedido (tendencia 30d)",
        "months_of_supply": "Meses de oferta",
        "cut_breadth": "Amplitud de bajadas",
        "closing_price_cycle": "Ciclo de precios de cierre (notariado)",
    }

    def _fmt_signal(name: str, sig: dict) -> str:
        if name == "inventory_trend":
            v = sig.get("valor_30d")
            return f"{v:+.1f} anuncios/mes — {sig.get('lectura', '')}" if v is not None else "sin datos suficientes"
        if name == "asking_ppsqm_trend":
            cur, var = sig.get("eur_m2_actual"), sig.get("variacion_30d")
            if cur is None:
                return "sin datos suficientes"
            return f"{fmt_eur(cur)}/m² ahora · variando {var:+.0f} €/m² cada 30 días"
        if name == "months_of_supply":
            cur, var, conf = sig.get("meses_actual"), sig.get("variacion_30d"), sig.get("confianza", "ok")
            if cur is None:
                return "sin datos suficientes"
            conf_txt = "" if conf == "ok" else f" · confianza {conf}"
            return f"{cur:.1f} meses (tendencia {var:+.2f}/mes) — {sig.get('lectura', '')}{conf_txt}"
        if name == "cut_breadth":
            cur, var = sig.get("pct_actual"), sig.get("variacion_30d")
            if cur is None:
                return "sin datos suficientes"
            return f"{cur:.1%} del stock con bajadas · variando {var*100:+.1f} puntos cada 30 días"
        if name == "closing_price_cycle":
            yoy = sig.get("variacion_anual_pct_3y", {})
            yoy_txt = " · ".join(f"{y}: {v:+.1f}%" for y, v in yoy.items())
            return f"{yoy_txt} · cierre medio 12m: {fmt_eur(sig.get('precio_medio_cierre_12m'))}/m² — {sig.get('lectura', '')}"
        return ", ".join(f"{k}: {v}" for k, v in sig.items() if k not in ("buyer_friendly", "lectura"))

    sig_lines = []
    for name, sig in verdict["signals"].items():
        bf = sig.get("buyer_friendly")
        icon = "⚪" if bf is None else ("🟢" if bf else "🔴")
        sig_lines.append(f'<div class="sig">{icon} <b>{SIGNAL_NAMES.get(name, name)}</b> — {_fmt_signal(name, sig)}</div>')
    verdict_html = f'<div class="verdict"><b>{verdict["verdict"]}</b>{"".join(sig_lines)}</div>' + """
    <details class="help"><summary>📖 Qué mide cada indicador y por qué</summary><dl>
      <dt>🟢🔴⚪ Cómo leer los semáforos</dt>
      <dd>🟢 este indicador hoy favorece comprar · 🔴 favorece esperar/vendedor · ⚪ no hay suficiente historia todavía (no puntúa en el buyer score). El «buyer score» de arriba es la fracción de indicadores 🟢 sobre los que sí puntúan.</dd>
      <dt>Inventario (tendencia 30d)</dt>
      <dd>Cuántos anuncios más (o menos) hay activos en vuestra banda de búsqueda cada mes. Si sube, hay más donde elegir y los vendedores compiten entre ellos → 🟢. Si baja, el stock se agota → 🔴.</dd>
      <dt>€/m² pedido (tendencia 30d)</dt>
      <dd>Cómo cambia el precio que PIDEN los vendedores, no lo que se cierra. Si baja, los vendedores están ajustando expectativas → 🟢. Si sube, están subiendo el listón → 🔴.</dd>
      <dt>Meses de oferta</dt>
      <dd>Inventario activo ÷ ritmo mensual de retiradas: cuántos meses tardaría en venderse todo el stock actual al ritmo de hoy. Regla general: &gt;6 meses = mercado comprador (mucha oferta, poca demanda) → 🟢; &lt;4 = mercado vendedor (se vende todo rápido) → 🔴. Con pocos meses de historial (como ahora) el ratio es poco fiable — se marca ⚪ y no cuenta en el buyer score hasta los ~150 días de datos.</dd>
      <dt>Amplitud de bajadas</dt>
      <dd>Qué porcentaje del stock activo ya ha bajado de precio al menos una vez. Si ese porcentaje crece, cada vez más vendedores están cediendo → 🟢.</dd>
      <dt>Ciclo de precios de cierre (notariado)</dt>
      <dd>Contexto de fondo, no cambia mes a mes: cómo han evolucionado los precios REALMENTE escriturados (notariado) en los últimos 3 años, y el precio medio de cierre de los últimos 12 meses. Sant Cugat lleva una década prácticamente solo subiendo (única caída relevante: -1,7% en 2023, recuperada al año siguiente) — por eso este indicador se marca 🔴 de forma estructural: esperando una bajada de mercado generalizada históricamente has salido perdiendo. La palanca real está en encontrar el anuncio concreto mal precificado o estancado, no en esperar a que baje el mercado entero.</dd>
    </dl></details>"""

    scen = an.scenario_table([450_000, 500_000, 550_000, 575_000, 600_000, 620_000, 650_000], profile)
    afford_spec = [
        ("Precio compra", "price", "eur"),
        ("Cash necesario (80% LTV)", "cash_80", "eur"), ("Cuota al 80%", "cuota_80", "eur"),
        ("Cash necesario (90% LTV)", "cash_90", "eur"), ("Cuota al 90%", "cuota_90", "eur"),
        ("Meses ahorro (sin extras)", "months_no_extras", "num"),
        ("Camino más fácil / qué falta", "camino", "text"),
        ("Veredicto (80%)", "verdict", "verdict"),
    ]
    afford_html = html_table(scen, afford_spec, "afford")
    cap = an.max_affordable_price(profile)
    cap_x = an.max_affordable_price(profile, include_extras=True)
    cap_sx = an.max_affordable_price(profile, payment=profile.stretch_payment, include_extras=True)
    typical_margin = 0.06  # typical negotiated discount on a leveraged listing
    budget_html = f"""
    <div class="note">
      <b>Tu configuración</b> (edítala en <code>budget_config.json</code> y regenera el informe):
      ahorros <b>{fmt_eur(profile.savings)}</b> · cash extra si hiciera falta <b>{fmt_eur(profile.extra_cash_if_needed)}</b>
      · ahorro <b>{fmt_eur(profile.monthly_savings)}/mes</b> · tipo <b>{profile.annual_rate_pct}%</b> a {profile.term_years} años
      · cuota objetivo {fmt_eur(profile.comfortable_payment)} / máx {fmt_eur(profile.max_payment)} / excepcional {fmt_eur(profile.stretch_payment)}.
    </div>
    <div class="cards">
      <div class="card" title="Con tus ahorros actuales y cuota máxima."><div class="l">Precio máx. HOY (solo ahorros)</div><div class="v">{fmt_eur(cap['binding_max_price'])}</div><div class="d">a {fmt_eur(cap['payment_used'])}/mes</div></div>
      <div class="card" title="Añadiendo el cash extra (p.ej. donación) solo si hiciera falta."><div class="l">Precio máx. con extras</div><div class="v">{fmt_eur(cap_x['binding_max_price'])}</div><div class="d">usando {fmt_eur(cap_x['savings_used'])} de cash</div></div>
      <div class="card" title="Escenario límite: cuota excepcional + todo el cash disponible."><div class="l">Techo absoluto</div><div class="v">{fmt_eur(cap_sx['binding_max_price'])}</div><div class="d">a {fmt_eur(profile.stretch_payment)}/mes (excepcional)</div></div>
      <div class="card" title="Si negocias el descuento típico (~6%) sobre el precio pedido, puedes mirar anuncios hasta este precio PEDIDO."><div class="l">Precio PEDIDO máx. a mirar</div><div class="v">{fmt_eur(cap_x['binding_max_price'] / (1 - typical_margin))}</div><div class="d">asumiendo ~{typical_margin:.0%} de negociación</div></div>
    </div>
    <details class="help"><summary>📖 Por qué el cash manda a partir de ~{fmt_eur(cap['binding_max_price'])} (y por qué MÁS cash siempre ayuda)</summary><dl>
      <dt>Hay dos límites independientes</dt>
      <dd><b>1) La cuota</b>: con {fmt_eur(profile.max_payment)}/mes al {profile.annual_rate_pct}% a {profile.term_years} años, el banco te presta como máximo ≈ <b>{fmt_eur(cap['max_loan'])}</b>.<br>
      <b>2) El cash</b>: el banco financia máximo el 80% del precio → tú pones el 20% de entrada + ~11,5% de gastos (ITP+notaría) = <b>~31,5% del precio en cash</b>.</dd>
      <dt>Cuál de los dos te frena</dt>
      <dd>Con {fmt_eur(profile.savings)} de cash: límite por cash = {fmt_eur(profile.savings)} ÷ 0,315 ≈ <b>{fmt_eur(cap['max_price_by_ltv_and_savings'])}</b>. A ese precio la hipoteca (80%) sale a ~{fmt_eur(an.affordability(cap['max_price_by_ltv_and_savings'], profile)['monthly_payment_at_80ltv'])}/mes — <i>por debajo</i> de tu tope. Es decir: la cuota aún te sobra; lo que se agota antes es la entrada. Por eso decimos que «manda el cash».</dd>
      <dt>Más cash = más techo Y menos cuota</dt>
      <dd>Cada +10k de cash suben tu techo ≈ +32k de precio (10k ÷ 0,315). Y si compras por debajo de tu techo, todo el cash que pongas de más reduce la hipoteca y la cuota — la columna «Cuota/mes» de la tabla ya asume que metéis todo el cash disponible. El cash deja de ser el límite cuando el techo por cash supera el techo por cuota (con ~200k de cash, ambos se cruzan ≈ 590k).</dd>
      <dt>Las columnas de meses</dt>
      <dd>«Meses ahorro (sin extras)»: meses a tu ritmo de {fmt_eur(profile.monthly_savings)}/mes hasta poder comprar a ese precio. En los precios donde la cuota al 80% LTV superaría tu tope, el cálculo asume que sigues ahorrando hasta que la hipoteca necesaria mantenga la cuota ≤ {fmt_eur(profile.stretch_payment)} — es decir, <b>bajar el LTV efectivo al 75%, 70%…</b> con más entrada. Todo precio es alcanzable ahorrando; la pregunta es cuántos meses (y si &gt;24 meses, lo marcamos «fuera de alcance» porque para esta búsqueda equivale a no llegar: mejor negociar precio o tipo).</dd>
      <dt>¿Y una hipoteca al 90%?</dt>
      <dd>Con buen perfil de ingresos es posible, pero ojo al efecto: financiar el 90% <b>no sube tu techo</b> — la hipoteca máxima que permite tu cuota ({fmt_eur(profile.max_payment)}/mes → ≈{fmt_eur(an.max_loan_for_payment(profile.max_payment, profile.annual_rate_pct, profile.term_years))}) dividida entre 0,9 da un precio máximo de ≈{fmt_eur(an.max_loan_for_payment(profile.max_payment, profile.annual_rate_pct, profile.term_years)/0.9)}, MENOS que tu techo al 80% con extras. Su ventaja real es otra: <b>por debajo de ese precio te deja comprar guardando ~10% del precio en el bolsillo</b> (colchón para reforma/muebles). Compara las columnas «Cash necesario» 80% vs 90% en la tabla. Contras: los bancos suelen cobrar un tipo algo mayor por encima del 80% y exigen perfiles fuertes — negociadlo con vuestro 26% de esfuerzo, que es bueno.</dd>
      <dt>La palanca del tipo</dt>
      <dd>Cuando la cuota es el bloqueo (precios altos), cada −0,25% de tipo baja la cuota ~60 €/mes por cada 500k de hipoteca. La columna «Camino más fácil» calcula el tipo exacto que desbloquearía cada precio.</dd>
    </dl></details>"""

    top40 = ranked.head(40)
    spec = [(l, k, t) for (l, k, t) in CANDIDATE_SPEC if k in top40.columns]
    candidates_html = html_table(top40, spec, "cands")

    hot = ranked[(ranked["multi_listed"] == 1) | (ranked["kw_any_motivated"] == 1) | (ranked["relist_count"] >= 1)].head(20)
    hot_html = html_table(hot, spec, "hot") if len(hot) else '<div class="note">Sin señales de vendedor motivado ahora mismo.</div>'

    reno = ranked[ranked["reno_opportunity"] == 1].head(15)
    reno_html = html_table(reno, spec, "reno") if len(reno) else '<div class="note">No hay oportunidades de reforma (400–500k) en este momento.</div>'

    # inspector: every listing shown in any table (or its excel), plus cluster/relist siblings
    seed_ids: list = []
    for df in (ranked.head(100), quality.head(60), stuck, hot, reno, shortlist):
        if isinstance(df, pd.DataFrame) and "property_id" in df.columns:
            seed_ids.extend(df["property_id"].dropna().tolist())
    inspector_section = render_inspector(build_inspector_payload(feats, data, seed_ids))

    gap_html = ""
    if gap is not None:
        gap_spec = [
            ("Zona", "zone", "text"), ("Cierre €/m² (12m)", "closing_mean_eur_m2_12m", "num"),
            ("Pedido €/m² (banda)", "asking_median_eur_m2_band", "num"),
            ("Prima pedido vs cierre", "asking_premium_vs_closing_pct", "pct"),
            ("Importe medio cierre", "closing_mean_amount_eur_12m", "eur"),
            ("Compraventas 12m", "transactions_12m", "num"),
        ]
        gap_html = html_table(gap, gap_spec, "gap")

    sections = [
        f"<h1>{cfg['label']}</h1>",
        f'<div class="sub">Informe generado el {datetime.now():%d/%m/%Y %H:%M} · banda de búsqueda <b>400–700k</b> · objetivo <b>500–600k</b> (máx absoluto 620k) · precios scrapeados = PEDIDOS · líneas de cierre = notariado (may 2025–abr 2026)</div>',
        cards,
        "<h2>¿Es buen momento para comprar?</h2>", verdict_html,
        "<h2>Tendencia general del mercado</h2>",
        chart_market_trend(market_wide, cfg["label"]),
        '<div class="note">Banda amplia (150.000–1.500.000 €), sin filtrar por vuestro presupuesto — sirve para ver '
        'hacia dónde va el mercado en general, no solo vuestra franja de búsqueda. La línea fina es la mediana diaria '
        '(ruidosa: pocos anuncios cambian de precio cada día); la gruesa es la mediana suavizada a 7 días, la que '
        'importa para ver tendencia. Si sube, el mercado en su conjunto se encarece; si el inventario de la derecha '
        'crece, hay más oferta entrando de la que se retira.</div>',
        "<h2>Inventario y flujos en vuestra banda de búsqueda</h2>",
        chart_inventory_flows(market, cfg["label"]),
        "<h2>Pedido vs cierre</h2>",
        chart_asking_vs_closing(market, gap),
        gap_html,
        '<div class="note">La prima pedido-vs-cierre es el techo del margen total (parte es composición del stock). Un anuncio ≥25% por encima del cierre de zona y con semanas en mercado está objetivamente sobrevalorado.</div>' if gap is not None else "",
        "<h2>Presión vendedora</h2>", chart_pressure(market),
        '<div class="note"><b>Cómo leer:</b> izquierda — % del stock activo que ya bajó de precio al menos una vez: '
        'si la curva sube, cada vez más vendedores ceden (bueno para ti). Derecha — meses que tardaría en '
        'venderse todo el inventario al ritmo actual de retiradas (inventario ÷ retiradas/mes): zona roja &lt;4 = mercado '
        'de vendedores (se vende rápido), zona verde &gt;6 = mercado de compradores. El pico inicial es un artefacto '
        'de los primeros días de datos (denominador diminuto), y «retirado» ≠ «vendido» — con ~5 meses de historia '
        'este ratio será fiable; hasta entonces se marca ⚪ y no puntúa en el buyer score.</div>',
        "<h2>Velocidad del mercado</h2>", chart_survival(km, cfg["label"]),
        chart_notary_cycle(an.load_notariado()) if population == "sant_cugat" else "",
        "<h2>Tu presupuesto</h2>",
        budget_html,
        afford_html,
        '<h2 id="inspector">🔍 Inspector de anuncios — evolución de precio y eventos</h2>',
        INSPECTOR_HELP,
        inspector_section,
        f"<h2>🏆 Excelencia — lo mejor disponible ahora (de {len(quality)} en 400–700k, deduplicado)</h2>",
        QUALITY_HELP,
        '<div class="note">Excel completo (top 60): <code>' + quality_excel_path + "</code></div>",
        html_table(quality.head(25), [(l, k, t) for l, k, t in QUALITY_SPEC if k in quality.columns], "quality"),
        f"<h2>🎯 Excelentes pero estancados — candidatos a oferta ruthless ({len(stuck)})</h2>",
        STUCK_HELP,
        '<div class="note">Excel completo: <code>' + stuck_excel_path + "</code></div>",
        (html_table(stuck, [(l, k, t) for l, k, t in STUCK_SPEC if k in stuck.columns], "stuck")
         if len(stuck) else '<div class="note">Ahora mismo nada bueno lleva ≥30 días sin bajar precio y &gt;10% por encima de mercado — buena señal de que lo bueno se vende rápido.</div>'),
        f"<h2>📋 Shortlist de enriquecimiento — {len(shortlist)} pendientes</h2>",
        SHORTLIST_HELP.replace("{pop}", population),
        (f'<div class="note">Fichero: <code>{shortlist_path.name}</code> · <b>{len(shortlist)} anuncios PENDIENTES de enriquecer</b> '
         f'(los ya enriquecidos se omiten automáticamente). Del top-{SHORTLIST_N_LEVERAGE} por palanca + top-{SHORTLIST_N_QUALITY} por calidad + estancados.</div>'
         if len(shortlist) else
         '<div class="note">✅ Todo el top-40 (calidad y palanca) ya está enriquecido — no queda nada pendiente. Cuando entren anuncios nuevos al top-40, aparecerán aquí.</div>'),
        (html_table(shortlist, [("Título","title","text"),("Precio","price","eur"),("Motivo(s)","reasons","text"),("ID","property_id","pid")], "shortlist")
         if len(shortlist) else ""),
        f"<h2>Top 40 candidatos por palanca de negociación (de {len(ranked)} en banda de búsqueda, deduplicados)</h2>",
        CANDIDATE_COLUMNS_HELP,
        '<div class="note">Excel completo (top 100): <code>' + excel_path + "</code></div>",
        candidates_html,
        "<h2>Vendedores motivados / multi-agencia / relistados</h2>", hot_html,
        "<h2>Oportunidades de reforma (400–500k + obra)</h2>", reno_html,
        "<footer>Datos: scraping propio (idealista, yaencontre, agencias locales) + Portal Estadístico del Notariado. "
        "Retirada ≠ vendido (incluye retiradas y caducidades). Cohorte anterior al inicio del scraping: antigüedad = cota inferior.</footer>",
    ]
    return {"headline": headline, "html": "\n".join(s for s in sections if s), "population": population}


def render_page(title: str, body: str, nav_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="plotly.min.js"></script>
<style>{CSS}</style></head>
<body><div class="wrap">{nav_html}{body}</div>
<script>{SORT_JS}</script></body></html>"""


def render_index(towns: list[dict]) -> str:
    cards = []
    for t in towns:
        h = t["headline"]
        cards.append(f"""
        <a href="{t['population']}.html" style="text-decoration:none;color:inherit">
        <div class="card" style="min-height:150px">
          <div style="font-size:17px;font-weight:700;color:var(--blue)">{h['label']} →</div>
          <div class="d" style="margin:6px 0">{h['verdict']}</div>
          <div class="v" style="font-size:20px">{h['active']} activos · {fmt_eur(h['ppsqm'])}/m²</div>
          <div class="d">buyer score {h['buyer_score']} · {h['cut_breadth']:.0%} con bajadas · {h['mos']} meses de oferta</div>
        </div></a>""")
    body = (
        "<h1>Informe inmobiliario — búsqueda de casa 🏠</h1>"
        f'<div class="sub">Generado el {datetime.now():%d/%m/%Y %H:%M} · banda 400–700k · objetivo 500–600k (máx 620k)</div>'
        f'<div class="cards" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">{"".join(cards)}</div>'
        '<div class="note">Flujo diario: <code>python main.py</code> → <code>python build_report.py</code> → abrir este index. '
        'Semanal: <code>python enrich_details.py &lt;pueblo&gt;</code> para descripciones completas (señales de vendedor motivado).</div>'
    )
    return render_page("Informe inmobiliario", body, "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("population", nargs="?", choices=list(an.POPULATIONS) + ["all"], default="all")
    args = parser.parse_args()
    pops = list(an.POPULATIONS) if args.population == "all" else [args.population]

    REPORTS_DIR.mkdir(exist_ok=True)
    plotly_js = REPORTS_DIR / "plotly.min.js"
    if not plotly_js.exists():
        from plotly.offline import get_plotlyjs
        plotly_js.write_text(get_plotlyjs(), encoding="utf-8")

    towns = []
    for pop in pops:
        print(f"Building {pop} …", flush=True)
        town = build_town(pop)
        towns.append(town)

    nav_links = '<div class="nav"><a href="index.html">Inicio</a>' + "".join(
        f'<a href="{t["population"]}.html">{an.POPULATIONS[t["population"]]["label"]}</a>' for t in towns
    ) + "</div>"

    for t in towns:
        page = render_page(t["headline"]["label"], t["html"], nav_links)
        (REPORTS_DIR / f"{t['population']}.html").write_text(page, encoding="utf-8")

    if args.population == "all":
        (REPORTS_DIR / "index.html").write_text(render_index(towns), encoding="utf-8")

    print(f"\nDone → open {REPORTS_DIR / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
