# app.py
# -*- coding: utf-8 -*-
import io
import os
import re
import zipfile
from datetime import datetime
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
)
from xml.sax.saxutils import escape

# -----------------------------------------------------------
# App config / constants
# -----------------------------------------------------------
YEAR_STR_DEFAULT = "2026–2027"  # Default year label
CARDS_PER_PAGE_DEFAULT = 0      # 0 = continuous; e.g., 4 to add header on new page every 4 cards
TEXT_ON_PRIMARY = colors.white

st.set_page_config(page_title="Service Plan PDF Generator", layout="centered")
st.title("Service Plan PDF Generator")
st.caption("Upload Actions, Service Details, Establishment (CSV/XLSX), and Budget. Pick services to generate per‑service PDFs.")

# -----------------------------------------------------------
# Styles
# -----------------------------------------------------------
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    name="HeaderTitle", parent=styles["Heading1"], textColor=TEXT_ON_PRIMARY,
    fontSize=20, leading=34, spaceAfter=2, fontName="Helvetica-Bold"
))
styles.add(ParagraphStyle(
    name="HeaderSubTitle", parent=styles["Normal"], textColor=TEXT_ON_PRIMARY,
    fontSize=12, leading=14, spaceAfter=0, fontName="Helvetica"
))
styles.add(ParagraphStyle(
    name="FieldLabel", parent=styles["Normal"], fontName="Helvetica-Bold", textColor=colors.black
))
styles.add(ParagraphStyle(
    name="FieldValue", parent=styles["Normal"], fontName="Helvetica", textColor=colors.black
))
styles.add(ParagraphStyle(
    name="CardHeaderText", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12, textColor=TEXT_ON_PRIMARY
))

# -----------------------------------------------------------
# Column expectations
# -----------------------------------------------------------
EXPECTED_ACTIONS = {
    "service": ["Service", "Service Name", "Service name"],
    "sub_service": ["Sub Service", "Sub-Service", "SubService", "Subservice"],
    "action_name": ["Action Name", "Action"],
    "action_desc": ["Action Description", "Description", "Details"],
    "person": ["Person Responsible", "Owner", "Lead"],
    "action_type": ["Action Type", "Type"]
}
EXPECTED_DETAILS = {
    "service_name": ["Service Name", "Service", "Service name"],
    "service_lead": ["Service Lead", "Lead"],
    "manager": ["Manager"],
    "director": ["Director"],
    "what_we_do": ["What we do"],
    "what_we_produce":["What we produce"],
    "who_for": ["Who we do it for"],
    "community": ["What the community has told us"],
    "main_costs": ["Our main costs"],
    "income": ["Income revenue"],
    "op_budget": ["Annual Operating Budget"],
    "cw_budget": ["Capital Works Budget"],
    "workforce": ["Total Workforce"],
    "assets": ["What we own"],
    "alignment": ["Alignment to the Council Plan"],
    "done": ["What we have done"],
    "working_on": ["What we are working on"],
    "challenges": ["Our Challenges"],
    "opportunities": ["Our Opportunities"],
    "legislation": ["Legislation, policies, frameworks, and contracts", "Legislation, policies, frameworks, and contractsnow"],
}
EXPECTED_ESTAB = {
    "position_number": ["Position Number"],
    "position_title": ["Position Title"],
    "position_fte": ["Position FTE", "FTE"],
    "position_start": ["Position Start"],
    "position_end": ["Position End"],
    "position_type": ["Position Type"],
    "directorate": ["Directorate"],
    "directorate_desc":["Directorate Desc"],
    "service_unit": ["Service Unit", "Service", "Service Name"],
    "service_unit_desc":["Service Unit Desc", "Service Desc"],
    "team": ["Team"],
    "team_desc": ["Team Desc"],
    "position_class": ["Position Classification", "Classification", "Band"],
}
EXPECTED_BUDGET = {
    "cost_centre_desc": ["Cost Centre Description"],
    "natural_account_desc": ["Natural Account Description"],
    "budget_2425": ["2024/25 Full Year Budget", "2024/25 Budget"],
    "forecast_2425": ["2024/25 Forecast"],
    "budget_2526": ["2025/26 Final Budget"],
    "budget_2627": ["2026/27 Final Budget"],
    "budget_2728": ["2027/28 Final Budget"],
    "budget_2829": ["2028/29 Final Budget"],
    "directorate_desc": ["Directorate Description", "Direcorate Description", "Directorate"],
    "service_unit_desc": ["Service Unit Description"],
    "team_desc": ["Team Description"],
    "account_group_desc": ["Account Group Description"],
    "account_type_desc": ["Account Type Description"],
}

# -----------------------------------------------------------
# Helpers
# -----------------------------------------------------------
def esc_text(s: str) -> str:
    """Escape &, <, > and convert newlines to <br/> for safe Paragraph rendering."""
    return escape(str(s or "")).replace("\n", "<br/>")

def safe_filename(name: str, max_len: int = 120) -> str:
    """Make a filename-safe version of a service name."""
    if not name:
        name = "Unknown Service"
    name = re.sub(r'[\<\>:"/\\|\n\?\*\x00-\x1F]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name

_seen_filenames = {}
def unique_filename(base_name: str) -> str:
    """Avoid collisions by appending (2), (3), ... if needed."""
    count = _seen_filenames.get(base_name, 0) + 1
    _seen_filenames[base_name] = count
    return base_name if count == 1 else f"{base_name} ({count})"

def normalize_service_key(s: str) -> str:
    return (str(s or "")
            .replace("\u00A0", " ")  # non-breaking space
            .replace("\t", " ")
            .strip()
            .lower())

def strip_service_code(s: str) -> str:
    """
    Remove leading service codes like 'S100 - ' or 'ABC123 — ' from a service name.
    Keeps the descriptive name (e.g., "CEO's Office Administration").
    """
    text = str(s or "").replace("\u00A0", " ").replace("\t", " ").strip()
    text = re.sub(r"^\s*[A-Za-z0-9]+(?:[A-Za-z0-9]*)\s*[–—-]\s*", "", text)
    return text.strip()

def read_table(file):
    """Read uploaded CSV or Excel into a DataFrame."""
    filename = file.name.lower()
    if filename.endswith(".csv"):
        return pd.read_csv(file, encoding="utf-8-sig")
    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        return pd.read_excel(file, sheet_name=0, engine="openpyxl")
    else:
        raise ValueError(f"Unsupported file type: {file.name}. Use .csv or .xlsx/.xls")

def read_budget_table(file):
    """Read Budget file starting from the 4th row (skip first 3 rows)."""
    filename = file.name.lower()
    if filename.endswith(".csv"):
        return pd.read_csv(file, skiprows=3, encoding="utf-8-sig")
    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        return pd.read_excel(file, sheet_name=0, skiprows=3, engine="openpyxl")
    else:
        raise ValueError(f"Unsupported file type: {file.name}. Use .csv or .xlsx/.xls")

def build_column_map(df, expected_map):
    """Map expected logical keys to actual columns in df (case/space-insensitive)."""
    df.rename(columns=lambda c: str(c).strip(), inplace=True)
    cols = list(df.columns)
    lower_cols = {c.strip().lower(): c for c in cols}
    col_map, missing = {}, []
    for key, variants in expected_map.items():
        found = None
        for v in variants:
            lc = v.strip().lower()
            if lc in lower_cols:
                found = lower_cols[lc]
                break
        if not found:
            missing.append(f"{key} (any of: {', '.join(variants)})")
        else:
            col_map[key] = found
    return col_map, missing
def get_downloads_dir() -> str:
    """Return ~/Downloads; create it if missing."""
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(downloads, exist_ok=True)
    return downloads

def hex_to_reportlab_color(hex_str: str) -> colors.Color:
    """
    Convert '#RRGGBB' to reportlab Color. Falls back to a safe default if invalid.
    """
    try:
        hs = hex_str.strip()
        if not hs.startswith("#") or len(hs) != 7:
            return colors.HexColor("#4aab6d")
        return colors.HexColor(hs)
    except Exception:
        return colors.HexColor("#4aab6d")

# -----------------------------------------------------------
# UI header builders (use single PRIMARY_HEADER everywhere)
# -----------------------------------------------------------
def build_header_generic(title_html: str, subtitle_html: str, bg_color, logo_bytes: bytes | None):
    title_left = [
        Paragraph(title_html, styles["HeaderTitle"]),
        Paragraph(subtitle_html, styles["HeaderSubTitle"]),
    ]
    try:
        logo_img = Image(io.BytesIO(logo_bytes), width=120, height=60) if logo_bytes else ""
    except Exception:
        logo_img = ""
    header_table = Table([[title_left, logo_img]], colWidths=[None, 140])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",(0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return header_table

def build_header_details(service_name: str, logo_bytes: bytes | None, year_label: str, header_color):
    return build_header_generic(
        f"<b>Service Details</b>: <b>{esc_text(service_name)}</b>",
        f"Overview \n {esc_text(year_label)}",
        header_color,
        logo_bytes
    )

def build_header_workforce(service_name: str, logo_bytes: bytes | None, year_label: str, header_color):
    return build_header_generic(
        f"<b>Workforce</b>: <b>{esc_text(service_name)}</b>",
        f"Band mix \n {esc_text(year_label)}",
        header_color,
        logo_bytes
    )

def build_header_actions(service_name: str, logo_bytes: bytes | None, year_label: str, header_color):
    return build_header_generic(
        f"<b>Service Action Plan</b>: <b>{esc_text(service_name)}</b>",
        f"Actions for next year \n {esc_text(year_label)}",
        header_color,
        logo_bytes
    )

def build_header_budget(service_name: str, logo_bytes: bytes | None, year_label: str, header_color):
    return build_header_generic(
        f"<b>Budget Dashboard</b>: <b>{esc_text(service_name)}</b>",
        "Out-years: 2026/27, 2027/28, 2028/29",
        header_color,
        logo_bytes
    )

# -----------------------------------------------------------
# Cards & chart builders
# -----------------------------------------------------------
def create_action_card(service, sub_service, action_name, action_desc, person, action_type, header_color):
    header_text = Paragraph(f"<b>{esc_text(sub_service)}</b>", styles["CardHeaderText"])
    rows = [
        [Paragraph("<b>Action Name:</b>", styles["FieldLabel"]), Paragraph(esc_text(action_name), styles["FieldValue"])],
        [Paragraph("<b>Action Description:</b>", styles["FieldLabel"]),Paragraph(esc_text(action_desc), styles["FieldValue"])],
        [Paragraph("<b>Person Responsible:</b>", styles["FieldLabel"]),Paragraph(esc_text(person), styles["FieldValue"])],
        [Paragraph("<b>Action Type:</b>", styles["FieldLabel"]), Paragraph(esc_text(action_type), styles["FieldValue"])],
    ]
    data = [[header_text, ""]] + rows
    table = Table(data, colWidths=[160, None])
    table.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), header_color),         # uniform color
        ("TEXTCOLOR", (0, 0), (-1, 0), TEXT_ON_PRIMARY),
        ("LEFTPADDING", (0, 0), (-1, 0), 10),
        ("RIGHTPADDING",(0, 0), (-1, 0), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING",(0, 0), (-1, 0), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 1), (-1, -1), 0.25, colors.Color(0.85, 0.85, 0.85)),
        ("LEFTPADDING", (0, 1), (-1, -1), 10),
        ("RIGHTPADDING",(0, 1), (-1, -1), 10),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 1), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table

def service_details_card(row, col_map, header_color):
    def v(key):
        return esc_text(row[col_map[key]]) if key in col_map else ""
    sections = [
        ("Service Details", []),
        ("Leadership", [
            ("Service Lead", "service_lead"),
            ("Manager", "manager"),
            ("Director", "director"),
        ]),
        ("Purpose & Audience", [
            ("What we do", "what_we_do"),
            ("What we produce", "what_we_produce"),
            ("Who we do it for", "who_for"),
        ]),
        ("Community Insights", [
            ("What the community has told us", "community"),
        ]),
        ("Financials", [
            ("Our main costs", "main_costs"),
            ("Income revenue", "income"),
            ("Annual Operating Budget", "op_budget"),
            ("Capital Works Budget", "cw_budget"),
        ]),
        ("Workforce & Assets", [
            ("Total Workforce", "workforce"),
            ("What we own", "assets"),
        ]),
        ("Alignment", [
            ("Alignment to the Council Plan", "alignment"),
        ]),
        ("Status", [
            ("What we have done", "done"),
            ("What we are working on", "working_on"),
        ]),
        ("Challenges & Opportunities", [
            ("Our Challenges", "challenges"),
            ("Our Opportunities", "opportunities"),
        ]),
        ("Legislation / Policies / Frameworks / Contracts", [
            ("Legislation / Policies / Frameworks / Contracts", "legislation"),
        ]),
    ]
    data = []
    section_rows = []
    for section_title, fields in sections:
        data.append([Paragraph(f"<b>{esc_text(section_title)}</b>", styles["CardHeaderText"]), ""])
        section_rows.append(len(data) - 1)
        for label, key in fields:
            data.append([
                Paragraph(f"<b>{esc_text(label)}</b>", styles["FieldLabel"]),
                Paragraph(v(key), styles["FieldValue"])
            ])
    table = Table(data, colWidths=[170, None])
    style_cmds = [
        ("SPAN", (0, 0), (1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), header_color),         # uniform color
        ("TEXTCOLOR", (0, 0), (-1, 0), TEXT_ON_PRIMARY),
        ("LEFTPADDING", (0, 0), (-1, 0), 10),
        ("RIGHTPADDING",(0, 0), (-1, 0), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING",(0, 0), (-1, 0), 8),
        ("GRID", (0, 1), (-1, -1), 0.25, colors.Color(0.85, 0.85, 0.85)),
        ("LEFTPADDING", (0, 1), (-1, -1), 10),
        ("RIGHTPADDING",(0, 1), (-1, -1), 10),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 1), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for idx in section_rows[1:]:
        style_cmds.extend([
            ("SPAN", (0, idx), (1, idx)),
            ("BACKGROUND", (0, idx), (-1, idx), header_color),  # uniform color
            ("LEFTPADDING", (0, idx), (-1, idx), 10),
            ("RIGHTPADDING",(0, idx), (-1, idx), 10),
            ("TOPPADDING", (0, idx), (-1, idx), 6),
            ("BOTTOMPADDING",(0, idx), (-1, idx), 6),
        ])
    table.setStyle(TableStyle(style_cmds))
    return table

def extract_band_number(class_str: str):
    if not class_str:
        return None
    m = re.findall(r"\d+", str(class_str))
    if m:
        try:
            return int(m[0])
        except ValueError:
            return None
    return None

def build_workforce_band_chart(service_name: str, estab_df: pd.DataFrame, estab_map: dict, metric: str):
    candidates = []
    if "service_unit" in estab_map: candidates.append(estab_map["service_unit"])
    if "service_unit_desc" in estab_map: candidates.append(estab_map["service_unit_desc"])
    if not candidates:
        return None, Paragraph("No matching service columns found in Establishment file.", styles["FieldValue"])
    key_service = normalize_service_key(service_name)
    mask = False
    for col in candidates:
        mask = mask | (estab_df[col].astype(str).str.lower().str.strip() == key_service)
    service_rows = estab_df.loc[mask].copy()
    if service_rows.empty:
        return None, Paragraph("No workforce records found for this service.", styles["FieldValue"])

    fte_col = estab_map.get("position_fte")
    if fte_col and fte_col in service_rows.columns:
        service_rows[fte_col] = pd.to_numeric(service_rows[fte_col], errors="coerce").fillna(0.0)
    else:
        fte_col = "_fte"
        service_rows[fte_col] = 0.0

    class_col = estab_map.get("position_class")
    service_rows["_band"] = service_rows[class_col].apply(extract_band_number)

    if metric.lower() == "count":
        src_col = estab_map.get("position_number", class_col)
        agg = service_rows.groupby("_band")[src_col].count()
        ylabel = "Positions (count)"
    else:
        agg = service_rows.groupby("_band")[fte_col].sum()
        ylabel = "FTE"
    agg = agg.dropna().sort_index()
    if agg.empty:
        return None, Paragraph("No band-level data available for this service.", styles["FieldValue"])

    total_positions = len(service_rows)
    total_fte = float(service_rows[fte_col].sum())

    # NOTE: Graph colors remain independent (as requested)
    fig, ax = plt.subplots(figsize=(8.0, 3.8))
    bands = list(agg.index)
    values = list(agg.values)
    bar_color = "#ffda33"
    ax.bar(bands, values, color=bar_color)
    ax.set_title(f"Band mix — {service_name}", fontsize=12, pad=8)
    ax.set_xlabel("Band")
    ax.set_ylabel(ylabel)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    ax.set_xticks(bands)
    ax.tick_params(axis='both', labelsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    chart_img = Image(buf, width=500, height=240)

    summary_data = [
        [Paragraph("<b>Total positions</b>", styles["FieldLabel"]), Paragraph(f"{int(total_positions)}", styles["FieldValue"])],
        [Paragraph("<b>Total FTE</b>", styles["FieldLabel"]), Paragraph(f"{total_fte:.2f}", styles["FieldValue"])],
        [Paragraph("<b>Metric</b>", styles["FieldLabel"]), Paragraph("FTE by band" if metric.lower()=="fte" else "Positions count by band", styles["FieldValue"])],
    ]
    summary_table = Table(summary_data, colWidths=[150, None])
    summary_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.Color(0.85, 0.85, 0.85)),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",(0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return chart_img, summary_table

def build_budget_chart(service_name: str, budget_df: pd.DataFrame, budget_map: dict):
    if budget_df is None or not budget_map:
        return None, Paragraph("No budget file uploaded.", styles["FieldValue"])

    svc_col = budget_map.get("service_unit_desc")
    if not svc_col or svc_col not in budget_df.columns:
        return None, Paragraph("Budget file is missing 'Service Unit Description' column.", styles["FieldValue"])

    df = budget_df.copy()
    df["_svc_clean"] = df[svc_col].astype(str).apply(strip_service_code).str.lower().str.strip()
    key = strip_service_code(service_name).lower().strip()
    rows = df.loc[df["_svc_clean"] == key].copy()
    if rows.empty:
        return None, Paragraph("No budget rows found for this service.", styles["FieldValue"])

    c2627 = budget_map.get("budget_2627")
    c2728 = budget_map.get("budget_2728")
    c2829 = budget_map.get("budget_2829")
    for c in [c2627, c2728, c2829]:
        rows[c] = pd.to_numeric(rows[c], errors="coerce")

    totals = [
        float(rows[c2627].sum()),
        float(rows[c2728].sum()),
        float(rows[c2829].sum()),
    ]
    labels = ["2026/27 Final Budget", "2027/28 Final Budget", "2028/29 Final Budget"]

    # NOTE: Graph colors remain independent (as requested)
    fig, ax = plt.subplots(figsize=(8.0, 3.8))
    colors_bar = ["#4a90e2", "#4aab6d", "#ff7f50"]
    ax.bar(labels, totals, color=colors_bar)
    ax.set_title(f"Budget (Out-years) — {strip_service_code(service_name)}", fontsize=12, pad=8)
    ax.set_ylabel("Amount")
    ax.set_xlabel("Year")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    ax.tick_params(axis='x', rotation=0, labelsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    chart_img = Image(buf, width=500, height=240)

    def fmt_currency(x: float) -> str:
        try:
            return f"${x:,.0f}"
        except Exception:
            return str(x)

    summary_data = [
        [Paragraph("<b>2026/27 Final Budget</b>", styles["FieldLabel"]), Paragraph(fmt_currency(totals[0]), styles["FieldValue"])],
        [Paragraph("<b>2027/28 Final Budget</b>", styles["FieldLabel"]), Paragraph(fmt_currency(totals[1]), styles["FieldValue"])],
        [Paragraph("<b>2028/29 Final Budget</b>", styles["FieldLabel"]), Paragraph(fmt_currency(totals[2]), styles["FieldValue"])],
    ]
    summary_table = Table(summary_data, colWidths=[200, None])
    summary_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.Color(0.85, 0.85, 0.85)),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",(0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return chart_img, summary_table

# -----------------------------------------------------------
# UI: uploads
# -----------------------------------------------------------
col1, col2 = st.columns(2)
with col1:
    actions_file = st.file_uploader("Upload Actions file (CSV/XLSX)", type=["csv","xlsx","xls"], accept_multiple_files=False)
    details_file = st.file_uploader("Upload Service Details file (CSV/XLSX)", type=["csv","xlsx","xls"], accept_multiple_files=False)
with col2:
    estab_file = st.file_uploader("Upload Establishment file (XLSX/CSV)", type=["csv","xlsx","xls"], accept_multiple_files=False)
    logo_file  = st.file_uploader("Optional: Upload Logo (PNG/JPG)", type=["png","jpg","jpeg"], accept_multiple_files=False)

# Budget uploader
st.subheader("Budget Dashboard (Preview)")
budget_file = st.file_uploader("Upload Budget file (CSV/XLSX) — reads from 4th row", type=["csv","xlsx","xls"], accept_multiple_files=False)

st.divider()

# ---- Single header color for all places (except graphs) ----
header_hex = st.color_picker("Header color (applies everywhere except charts)", value="#4aab6d")
PRIMARY_HEADER = hex_to_reportlab_color(header_hex)

metric_choice = st.radio("Workforce metric", options=["FTE by band", "Positions count by band"], index=0)
metric = "fte" if metric_choice.startswith("FTE") else "count"
cards_per_page = st.number_input("Cards per page (0 = continuous)", min_value=0, max_value=50, value=CARDS_PER_PAGE_DEFAULT, step=1)
year_label = st.text_input("Year label for headers", value=YEAR_STR_DEFAULT)
colA, colB = st.columns(2)
with colA:
    save_to_downloads = st.checkbox("Also save to local Downloads", value=True)
with colB:
    save_individual_pdfs = st.checkbox("Save individual PDFs (not just ZIP)", value=False)

# -----------------------------------------------------------
# Load data & show service selector (only after core files uploaded)
# -----------------------------------------------------------
service_options_display = []
service_key_series = None
actions_df = details_df = estab_df = budget_df = None
actions_map = details_map = estab_map = budget_map = None
details_lookup = {}

core_files_ready = actions_file and details_file and estab_file

if core_files_ready:
    try:
        actions_df = read_table(actions_file).fillna("")
        details_df = read_table(details_file).fillna("")
        estab_df   = read_table(estab_file).fillna("")
    except Exception as e:
        st.error(f"Failed to read uploaded files: {e}")
        st.stop()

    actions_map, actions_missing = build_column_map(actions_df, EXPECTED_ACTIONS)
    details_map, details_missing = build_column_map(details_df, EXPECTED_DETAILS)
    estab_map,   estab_missing   = build_column_map(estab_df, EXPECTED_ESTAB)

    # Optional budget file
    budget_missing = []
    if budget_file:
        try:
            budget_df = read_budget_table(budget_file).fillna("")
            budget_map, budget_missing = build_column_map(budget_df, EXPECTED_BUDGET)
        except Exception as e:
            st.error(f"Failed to read budget file: {e}")
            budget_df = None
            budget_map = None

    if actions_missing or details_missing or estab_missing or budget_missing:
        st.error("Some required columns were not found. Please fix the source files.")
        with st.expander("Missing columns detail"):
            if actions_missing:
                st.write("**Actions file:**")
                st.code(" - " + "\n - ".join(actions_missing))
                st.write(f"Found columns: {', '.join(map(str, actions_df.columns))}")
            if details_missing:
                st.write("**Service Details file:**")
                st.code(" - " + "\n - ".join(details_missing))
                st.write(f"Found columns: {', '.join(map(str, details_df.columns))}")
            if estab_missing:
                st.write("**Establishment file:**")
                st.code(" - " + "\n - ".join(estab_missing))
                st.write(f"Found columns: {', '.join(map(str, estab_df.columns))}")
            if budget_file and budget_missing:
                st.write("**Budget file:**")
                st.code(" - " + "\n - ".join(budget_missing))
                st.write(f"Found columns: {', '.join(map(str, budget_df.columns))}")
        if actions_missing or details_missing or estab_missing:
            st.stop()

    # Build normalized service keys and display names from actions
    service_series_raw = actions_df[actions_map["service"]].astype(str)
    service_series_norm = (
        service_series_raw
        .str.replace("\u00A0", " ", regex=False)
        .str.replace("\t", " ", regex=False)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .replace("", "Unknown Service")
    )
    service_key_series = service_series_norm.str.lower()

    key_to_display = {}
    for raw, key in zip(service_series_raw, service_key_series):
        if key not in key_to_display:
            key_to_display[key] = raw if raw.strip() else "Unknown Service"

    service_options_display = sorted(key_to_display.values(), key=lambda s: s.lower())

    for _, r in details_df.iterrows():
        key = normalize_service_key(r[details_map["service_name"]])
        if key:
            details_lookup[key] = r

    st.subheader("Choose service(s) to generate")
    selected_services = st.multiselect(
        "Select one or more services",
        options=service_options_display,
        default=[],
        help="Only the selected services will be turned into PDFs."
    )

    # Budget Preview (only shows when budget uploaded and a single service selected)
    if budget_df is not None and budget_map and selected_services:
        preview_service = selected_services[0]
        svc_col = budget_map.get("service_unit_desc")
        if svc_col in budget_df.columns:
            df_prev = budget_df.copy()
            df_prev["_svc_clean"] = df_prev[svc_col].astype(str).apply(strip_service_code).str.lower().str.strip()
            match_key = strip_service_code(preview_service).lower().strip()
            rows_prev = df_prev.loc[df_prev["_svc_clean"] == match_key].copy()

            if not rows_prev.empty:
                for c in [budget_map.get("budget_2627"), budget_map.get("budget_2728"), budget_map.get("budget_2829")]:
                    rows_prev[c] = pd.to_numeric(rows_prev[c], errors="coerce")

                values = [
                    float(rows_prev[budget_map["budget_2627"]].sum()),
                    float(rows_prev[budget_map["budget_2728"]].sum()),
                    float(rows_prev[budget_map["budget_2829"]].sum()),
                ]
                labels = ["2026/27 Final Budget", "2027/28 Final Budget", "2028/29 Final Budget"]
                fig, ax = plt.subplots(figsize=(6, 4))
                # NOTE: Graph colors remain independent (as requested)
                ax.bar(labels, values, color=["#4a90e2", "#4aab6d", "#ff7f50"])
                ax.set_title(f"Budget (Preview): {strip_service_code(preview_service)}")
                ax.set_ylabel("Amount")
                ax.grid(axis="y", linestyle="--", alpha=0.5)
                st.pyplot(fig)
            else:
                st.info("No budget rows found for the selected service.")
        else:
            st.info("Budget file is missing 'Service Unit Description' column.")
else:
    selected_services = []

# -----------------------------------------------------------
# Generate on click
# -----------------------------------------------------------
generate_btn = st.button("Generate PDFs")
if generate_btn:
    if not (actions_df is not None and details_df is not None and estab_df is not None):
        st.error("Please upload Actions, Details, and Establishment files first.")
        st.stop()
    if not selected_services:
        st.warning("Please select at least one service to generate.")
        st.stop()

    logo_bytes = logo_file.read() if logo_file else None

    display_to_key = {}
    service_series_raw = actions_df[actions_map["service"]].astype(str)
    service_series_norm = (
        service_series_raw
        .str.replace("\u00A0", " ", regex=False)
        .str.replace("\t", " ", regex=False)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .replace("", "Unknown Service")
    )
    key_series = service_series_norm.str.lower()
    for raw, key in zip(service_series_raw, key_series):
        disp = raw if raw.strip() else "Unknown Service"
        if disp not in display_to_key:
            display_to_key[disp] = key

    selected_keys = [display_to_key[s] for s in selected_services if s in display_to_key]

    zip_buffer = io.BytesIO()
    generated_count = 0
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for key in selected_keys:
            mask = (key_series == key)
            service_df_actions = actions_df.loc[mask].copy()
            display_service = (
                service_series_raw.loc[mask].iloc[0]
                if not service_df_actions.empty and service_series_raw.loc[mask].iloc[0].strip()
                else "Unknown Service"
            )
            safe_service = safe_filename(display_service)

            elements = []

            # --- Page 1: Service Details cover
            elements.append(build_header_details(display_service, logo_bytes, year_label, PRIMARY_HEADER))
            elements.append(Spacer(1, 8))
            details_row = details_lookup.get(key)
            if details_row is not None:
                elements.append(service_details_card(details_row, details_map, PRIMARY_HEADER))
            else:
                elements.append(Paragraph("No details found for this service.", styles["FieldValue"]))
            elements.append(PageBreak())

            # --- Page 2: Workforce
            elements.append(build_header_workforce(display_service, logo_bytes, year_label, PRIMARY_HEADER))
            elements.append(Spacer(1, 8))
            chart_img, summary_table = build_workforce_band_chart(display_service, estab_df, estab_map, metric)
            if chart_img is not None:
                elements.append(chart_img)
                elements.append(Spacer(1, 6))
                elements.append(summary_table)
            else:
                elements.append(summary_table)
            elements.append(PageBreak())

            # --- Page 3: Budget Dashboard (if budget available)
            if budget_df is not None and budget_map:
                elements.append(build_header_budget(display_service, logo_bytes, year_label, PRIMARY_HEADER))
                elements.append(Spacer(1, 8))
                b_img, b_table = build_budget_chart(display_service, budget_df, budget_map)
                if b_img is not None:
                    elements.append(b_img)
                    elements.append(Spacer(1, 6))
                elements.append(b_table)
                elements.append(PageBreak())

            # --- Actions pages
            elements.append(build_header_actions(display_service, logo_bytes, year_label, PRIMARY_HEADER))
            elements.append(Spacer(1, 8))
            card_count = 0
            for _, r in service_df_actions.iterrows():
                elements.append(
                    create_action_card(
                        r.get(actions_map["service"], ""),
                        r.get(actions_map["sub_service"], ""),
                        r.get(actions_map["action_name"], ""),
                        r.get(actions_map["action_desc"], ""),
                        r.get(actions_map["person"], ""),
                        r.get(actions_map["action_type"], ""),
                        PRIMARY_HEADER  # uniform color on sub-header
                    )
                )
                elements.append(Spacer(1, 6))
                card_count += 1
                if CARDS_PER_PAGE_DEFAULT and cards_per_page and card_count % cards_per_page == 0:
                    elements.append(PageBreak())
                    elements.append(build_header_actions(display_service, logo_bytes, year_label, PRIMARY_HEADER))
                    elements.append(Spacer(1, 8))

            pdf_buf = io.BytesIO()
            doc = SimpleDocTemplate(
                pdf_buf, pagesize=A4,
                leftMargin=10*mm, rightMargin=10*mm,
                topMargin=10*mm, bottomMargin=10*mm
            )
            doc.build(elements)
            pdf_bytes = pdf_buf.getvalue()
            pdf_buf.close()

            out_name = unique_filename(f"Service Plan - {safe_service}.pdf")
            zf.writestr(out_name, pdf_bytes)

            if save_to_downloads and save_individual_pdfs:
                downloads_dir = get_downloads_dir()
                pdf_out_path = os.path.join(downloads_dir, out_name)
                with open(pdf_out_path, "wb") as f:
                    f.write(pdf_bytes)
            generated_count += 1
    zip_buffer.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"Service_Plans_{ts}.zip"

    if save_to_downloads:
        downloads_dir = get_downloads_dir()
        zip_out_path = os.path.join(downloads_dir, zip_name)
        with open(zip_out_path, "wb") as f:
            f.write(zip_buffer.getvalue())
        st.info(f"ZIP saved to: {zip_out_path}")

    st.success(f"Generated {generated_count} PDF(s) for the selected services.")
    st.download_button(
        label="Download ZIP of selected service PDFs",
        data=zip_buffer,
        file_name=zip_name,
        mime="application/zip"
    )
