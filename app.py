import os
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from requests_oauthlib import OAuth1

BASE_URL = "https://api.bricklink.com/api/store/v1"
HISTORY_FILE = Path("brick_curator_history.csv")

st.set_page_config(
    page_title="Brick Curator Pro",
    page_icon="🧱",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 0.8rem; padding-bottom: 5rem; max-width: 820px;}
    h1 {font-size: 2.0rem !important; margin-bottom: 0.2rem;}
    h2, h3 {margin-top: 1.2rem;}
    div[data-testid="stMetric"] {background: #F5F7FB; padding: 14px; border-radius: 18px; border: 1px solid #E5E7EB;}
    div.stButton > button, div.stDownloadButton > button {width: 100%; min-height: 3.35rem; border-radius: 16px; font-size: 1.08rem; font-weight: 800;}
    input, textarea, select {font-size: 16px !important;}
    .hero {background: linear-gradient(135deg, #111827 0%, #2F6FED 100%); color: white; padding: 22px; border-radius: 24px; margin-bottom: 16px; box-shadow: 0 8px 24px rgba(17,24,39,.18);}
    .hero h1 {color:white; margin:0;}
    .hero p {color:#E5E7EB; margin:.25rem 0 0 0;}
    .card {background:#F5F7FB; padding:16px; border-radius:20px; border:1px solid #E5E7EB; margin-bottom:12px;}
    .recommend {font-size:1.55rem; font-weight:900; margin:0 0 4px 0;}
    .muted {color:#6B7280; font-size:0.92rem;}
    .pill {display:inline-block; padding:6px 10px; border-radius:999px; background:#E5E7EB; color:#111827; font-weight:700; margin-right:6px; margin-top:6px;}
    .small {font-size:.88rem; color:#6B7280;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
        <h1>🧱 Brick Curator Pro</h1>
        <p>Mobile-first LEGO part-out, demand, and buy/pass analyzer for BrickLink sellers.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def get_secret(name: str, fallback: str = "") -> str:
    try:
        return st.secrets.get(name, fallback)
    except Exception:
        return os.getenv(name, fallback)


def make_auth(consumer_key: str, consumer_secret: str, token_value: str, token_secret: str):
    return OAuth1(consumer_key, consumer_secret, token_value, token_secret)


def bl_get(path: str, auth, params=None):
    response = requests.get(f"{BASE_URL}{path}", auth=auth, params=params, timeout=35)
    if response.status_code != 200:
        raise RuntimeError(f"BrickLink API error {response.status_code}: {response.text}")
    return response.json().get("data", [])


@st.cache_data(show_spinner=False, ttl=60 * 60)
def get_set_name(set_no, ck, cs, tv, ts):
    auth = make_auth(ck, cs, tv, ts)
    try:
        data = bl_get(f"/items/SET/{set_no}", auth)
        return data.get("name", set_no)
    except Exception:
        return set_no


@st.cache_data(show_spinner=False, ttl=60 * 60)
def get_set_parts_cached(set_no, break_minifigs, include_box, include_instructions, ck, cs, tv, ts):
    auth = make_auth(ck, cs, tv, ts)
    params = {
        "instruction": str(include_instructions).lower(),
        "box": str(include_box).lower(),
        "break_minifigs": str(break_minifigs).lower(),
        "break_subsets": "true",
    }
    data = bl_get(f"/items/SET/{set_no}/subsets", auth, params=params)
    lots = []
    for group in data:
        for entry in group.get("entries", []):
            item = entry.get("item", {})
            if entry.get("is_alternate"):
                continue
            qty = int(entry.get("quantity", 0) or 0)
            extra_qty = int(entry.get("extra_quantity", 0) or 0)
            lots.append({
                "item_no": item.get("no", ""),
                "item_name": item.get("name", ""),
                "item_type": item.get("type", ""),
                "color_id": entry.get("color_id"),
                "qty_in_set": qty,
                "extra_qty": extra_qty,
                "total_qty": qty + extra_qty,
            })
    if not lots:
        return pd.DataFrame()
    df = pd.DataFrame(lots)
    return df.groupby(["item_no", "item_name", "item_type", "color_id"], as_index=False, dropna=False)[["qty_in_set", "extra_qty", "total_qty"]].sum()


def apply_fast_mode_filter(parts_df, fast_mode, fast_max_lots, fast_min_qty, fast_include_parts, fast_include_minifigs, fast_include_books, fast_include_sets, fast_sort):
    """Reduce the number of BrickLink price-guide calls for quicker mobile analysis."""
    if not fast_mode or parts_df.empty:
        return parts_df.copy(), {"enabled": False, "original_lots": len(parts_df), "analyzed_lots": len(parts_df)}

    df = parts_df.copy()
    original_lots = len(df)

    allowed_types = []
    if fast_include_parts:
        allowed_types.append("PART")
    if fast_include_minifigs:
        allowed_types.append("MINIFIG")
    if fast_include_books:
        allowed_types.append("BOOK")
    if fast_include_sets:
        allowed_types.append("SET")

    if allowed_types:
        df = df[df["item_type"].isin(allowed_types)]

    # Always keep minifigs when selected, even if their set quantity is 1.
    if fast_include_minifigs:
        df = df[(df["total_qty"] >= fast_min_qty) | (df["item_type"] == "MINIFIG")]
    else:
        df = df[df["total_qty"] >= fast_min_qty]

    if fast_sort == "Highest quantity first":
        df = df.sort_values(["total_qty", "item_type", "item_no"], ascending=[False, True, True])
    elif fast_sort == "Minifigs first, then quantity":
        df["_priority"] = df["item_type"].map({"MINIFIG": 0, "PART": 1, "BOOK": 2, "SET": 3}).fillna(9)
        df = df.sort_values(["_priority", "total_qty", "item_no"], ascending=[True, False, True]).drop(columns=["_priority"])
    else:
        df = df.sort_values(["item_type", "item_no", "color_id"], ascending=[True, True, True])

    if fast_max_lots > 0:
        df = df.head(int(fast_max_lots))

    return df.reset_index(drop=True), {"enabled": True, "original_lots": original_lots, "analyzed_lots": len(df)}


@st.cache_data(show_spinner=False, ttl=60 * 60)
def get_price_guide_cached(item_type, item_no, color_id, condition, guide_type, country_code, currency_code, ck, cs, tv, ts):
    auth = make_auth(ck, cs, tv, ts)
    params = {"new_or_used": condition, "guide_type": guide_type, "currency_code": currency_code}
    if color_id is not None and not pd.isna(color_id):
        params["color_id"] = int(color_id)
    if country_code:
        params["country_code"] = country_code
    try:
        data = bl_get(f"/items/{item_type}/{item_no}/price", auth, params=params)
        return {
            "avg_price": float(data.get("avg_price") or 0),
            "qty_avg_price": float(data.get("qty_avg_price") or 0),
            "min_price": float(data.get("min_price") or 0),
            "max_price": float(data.get("max_price") or 0),
            "unit_quantity": int(data.get("unit_quantity") or 0),
            "total_quantity": int(data.get("total_quantity") or 0),
        }
    except Exception:
        return {"avg_price": 0, "qty_avg_price": 0, "min_price": 0, "max_price": 0, "unit_quantity": 0, "total_quantity": 0}



@st.cache_data(show_spinner=False, ttl=60 * 30)
def get_store_inventory_for_lot_cached(item_type, item_no, color_id, condition, ck, cs, tv, ts):
    """Return matching active store inventory lots for one item/color.

    This is intentionally optional because it adds extra BrickLink API calls.
    It uses your BrickLink store inventory and reads the remarks field, which is
    commonly used for drawer/bin locations.
    """
    auth = make_auth(ck, cs, tv, ts)
    params = {
        "item_type": item_type,
        "item_no": item_no,
        "new_or_used": condition,
        "status": "Y",
    }
    if color_id is not None and not pd.isna(color_id):
        params["color_id"] = int(color_id)

    inventory_rows = []
    page = 1

    # BrickLink usually returns all matches for a specific item/color quickly,
    # but page defensively in case a store has many duplicate lots.
    while page <= 10:
        params["page"] = page
        try:
            data = bl_get("/inventories", auth, params=params)
        except Exception:
            break

        if not data:
            break

        for inv in data:
            item = inv.get("item", {}) or {}
            inv_item_no = item.get("no", inv.get("item_no", ""))
            inv_item_type = item.get("type", inv.get("item_type", ""))
            inv_color_id = inv.get("color_id")

            # Keep this strict so similar parts/colors do not pollute the pull list.
            if str(inv_item_no) != str(item_no) or str(inv_item_type) != str(item_type):
                continue
            if color_id is not None and not pd.isna(color_id) and int(inv_color_id or -1) != int(color_id):
                continue

            inventory_rows.append({
                "inventory_id": inv.get("inventory_id", ""),
                "store_qty": int(inv.get("quantity", 0) or 0),
                "store_unit_price": float(inv.get("unit_price", 0) or 0),
                "store_condition": inv.get("new_or_used", condition),
                "drawer_remarks": inv.get("remarks", "") or "No remarks / no drawer",
                "bulk": int(inv.get("bulk", 1) or 1),
                "stockroom": inv.get("stockroom_id", ""),
            })

        # If fewer than BrickLink's typical page size came back, assume complete.
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.05)

    return inventory_rows


def add_store_inventory_matches(df, condition, ck, cs, tv, ts):
    """Add store quantity and drawer remarks to analyzed set lots."""
    if df.empty:
        return df.copy(), pd.DataFrame()

    rows = []
    pull_rows = []
    progress = st.progress(0, text="Checking your BrickLink store inventory...")
    total = len(df)

    for idx, row in df.iterrows():
        progress.progress(min(1.0, (idx + 1) / max(total, 1)), text=f"Checking store lot {idx + 1} of {total}: {row['item_no']}")
        matches = get_store_inventory_for_lot_cached(row["item_type"], row["item_no"], row["color_id"], condition, ck, cs, tv, ts)

        store_qty = sum(m.get("store_qty", 0) for m in matches)
        remarks = sorted({m.get("drawer_remarks", "No remarks / no drawer") for m in matches})
        inventory_ids = sorted({str(m.get("inventory_id", "")) for m in matches if m.get("inventory_id", "") != ""})

        out = row.to_dict()
        out.update({
            "exists_in_store": bool(matches),
            "store_lots_count": len(matches),
            "store_qty": store_qty,
            "drawer_remarks": " | ".join(remarks) if remarks else "",
            "inventory_ids": ", ".join(inventory_ids),
            "qty_to_add_from_set": row.get("total_qty", 0),
            "new_total_store_qty": store_qty + int(row.get("total_qty", 0) or 0),
        })
        rows.append(out)

        for m in matches:
            pull_rows.append({
                "drawer_remarks": m.get("drawer_remarks", "No remarks / no drawer"),
                "item_no": row["item_no"],
                "item_name": row["item_name"],
                "item_type": row["item_type"],
                "color_id": row["color_id"],
                "qty_to_add_from_set": row["total_qty"],
                "current_store_qty": m.get("store_qty", 0),
                "new_total_if_added_here": m.get("store_qty", 0) + int(row["total_qty"] or 0),
                "store_unit_price": m.get("store_unit_price", 0),
                "inventory_id": m.get("inventory_id", ""),
                "demand_label": row.get("demand_label", ""),
                "demand_score": row.get("demand_score", 0),
                "estimated_part_out_value": row.get("estimated_part_out_value", 0),
            })
        time.sleep(0.05)

    progress.empty()
    enriched = pd.DataFrame(rows)
    pull_list = pd.DataFrame(pull_rows)
    if not pull_list.empty:
        pull_list = pull_list.sort_values(["drawer_remarks", "item_type", "item_no", "color_id"]).reset_index(drop=True)
    return enriched, pull_list

def demand_label(score, sold_lots):
    if sold_lots <= 0:
        return "Low/Dead"
    if score >= 1.0:
        return "High Demand"
    if score >= 0.4:
        return "Good Demand"
    if score >= 0.1:
        return "Slow"
    return "Low/Dead"


def recommendation(roi, demand_pct, high_pct, slow_pct):
    score = 0
    score += min(45, roi / 3.0 * 45)
    score += min(30, demand_pct / 70 * 30)
    score += min(15, high_pct / 25 * 15)
    score += max(-10, -(slow_pct - 40) / 60 * 10) if slow_pct > 40 else 10
    score = max(0, min(100, round(score)))
    if score >= 80:
        label = "🔥 Strong Buy"
    elif score >= 65:
        label = "✅ Buy if price is right"
    elif score >= 50:
        label = "⚠️ Maybe / check minifigs"
    else:
        label = "❌ Pass"
    return label, score


def analyze(parts_df, set_no, buy_cost, condition, country_code, currency_code, ck, cs, tv, ts):
    rows = []
    progress = st.progress(0, text="Starting BrickLink checks...")
    total = len(parts_df)
    for idx, row in parts_df.iterrows():
        progress.progress(min(1.0, (len(rows) + 1) / max(total, 1)), text=f"Checking lot {len(rows)+1} of {total}: {row['item_no']}")
        sold = get_price_guide_cached(row["item_type"], row["item_no"], row["color_id"], condition, "sold", country_code, currency_code, ck, cs, tv, ts)
        time.sleep(0.08)
        stock = get_price_guide_cached(row["item_type"], row["item_no"], row["color_id"], condition, "stock", country_code, currency_code, ck, cs, tv, ts)
        sold_lots = sold["unit_quantity"]
        current_lots = stock["unit_quantity"]
        demand_score = sold_lots / current_lots if current_lots > 0 else float(sold_lots)
        rows.append({
            **row.to_dict(),
            "sold_qty_avg_price": sold["qty_avg_price"],
            "sold_avg_price": sold["avg_price"],
            "sold_lots_6mo": sold_lots,
            "sold_qty_6mo": sold["total_quantity"],
            "current_avg_price": stock["avg_price"],
            "current_lots": current_lots,
            "current_qty": stock["total_quantity"],
            "demand_score": round(demand_score, 3),
            "demand_label": demand_label(demand_score, sold_lots),
            "estimated_part_out_value": round(float(row["total_qty"]) * sold["qty_avg_price"], 2),
            "velocity_per_lot": round(sold["total_quantity"] / current_lots, 2) if current_lots else sold["total_quantity"],
        })
        time.sleep(0.08)
    progress.empty()
    result = pd.DataFrame(rows)
    total_value = float(result["estimated_part_out_value"].sum()) if not result.empty else 0
    total_lots = len(result)
    demanded_lots = int((result["sold_lots_6mo"] > 0).sum()) if total_lots else 0
    high_lots = int((result["demand_label"] == "High Demand").sum()) if total_lots else 0
    slow_dead = int(result["demand_label"].isin(["Slow", "Low/Dead"]).sum()) if total_lots else 0
    roi = total_value / buy_cost if buy_cost else 0
    demand_pct = demanded_lots / total_lots * 100 if total_lots else 0
    high_pct = high_lots / total_lots * 100 if total_lots else 0
    slow_pct = slow_dead / total_lots * 100 if total_lots else 0
    rec, score = recommendation(roi, demand_pct, high_pct, slow_pct)
    summary = {
        "Analyzed At": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Set Number": set_no,
        "Buy Cost": buy_cost,
        "Part-Out Value": round(total_value, 2),
        "ROI Multiple": round(roi, 2),
        "Total Lots": total_lots,
        "Lots With Demand": demanded_lots,
        "% Lots With Demand": round(demand_pct, 1),
        "High Demand Lots": high_lots,
        "Slow/Dead Lots": slow_dead,
        "Score": score,
        "Recommendation": rec,
    }
    return result, summary


def to_excel(summary, df, pull_list=None):
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        pd.DataFrame(summary.items(), columns=["Metric", "Value"]).to_excel(writer, sheet_name="Summary", index=False)
        df.sort_values(["estimated_part_out_value", "demand_score"], ascending=[False, False]).to_excel(writer, sheet_name="Lot Demand", index=False)
        df[df["demand_label"] == "High Demand"].to_excel(writer, sheet_name="High Demand", index=False)
        df[df["demand_label"].isin(["Slow", "Low/Dead"])].to_excel(writer, sheet_name="Slow Dead", index=False)
        if pull_list is not None and not pull_list.empty:
            pull_list.to_excel(writer, sheet_name="Drawer Pull List", index=False)
    return bio.getvalue()


def save_history(summary):
    row = pd.DataFrame([summary])
    if HISTORY_FILE.exists():
        old = pd.read_csv(HISTORY_FILE)
        out = pd.concat([old, row], ignore_index=True)
    else:
        out = row
    out.to_csv(HISTORY_FILE, index=False)


def load_history():
    if HISTORY_FILE.exists():
        return pd.read_csv(HISTORY_FILE)
    return pd.DataFrame()


with st.expander("🔑 BrickLink API setup", expanded=False):
    st.write("For Streamlit Cloud, add these in app Secrets. For local testing, you can paste them here temporarily.")
    col1, col2 = st.columns(2)
    with col1:
        consumer_key_input = st.text_input("Consumer Key", value="", type="password")
        token_value_input = st.text_input("Token Value", value="", type="password")
    with col2:
        consumer_secret_input = st.text_input("Consumer Secret", value="", type="password")
        token_secret_input = st.text_input("Token Secret", value="", type="password")

ck = consumer_key_input or get_secret("BL_CONSUMER_KEY")
cs = consumer_secret_input or get_secret("BL_CONSUMER_SECRET")
tv = token_value_input or get_secret("BL_TOKEN_VALUE")
ts = token_secret_input or get_secret("BL_TOKEN_SECRET")

st.markdown("### Analyze a set")
with st.form("analyze_form"):
    set_no = st.text_input("LEGO set number", placeholder="75311-1")
    buy_cost = st.number_input("Your purchase cost", min_value=0.0, value=0.0, step=1.0, format="%.2f")
    col_a, col_b = st.columns(2)
    with col_a:
        condition_text = st.radio("Condition", ["New", "Used"], horizontal=True)
        country_code = st.text_input("Country filter", value="US", help="Use US for United States only, or leave blank for worldwide.")
    with col_b:
        currency_code = st.text_input("Currency", value="USD")
        break_minifigs = st.toggle("Break minifigs into parts", value=False)
    with st.expander("Advanced options"):
        include_box = st.checkbox("Include box as a lot", value=False)
        include_instructions = st.checkbox("Include instructions as a lot", value=False)
        check_store_inventory = st.checkbox(
            "Create drawer pull list from my store inventory remarks",
            value=False,
            help="Optional and slower. When checked, the app searches your active BrickLink inventory for matching lots and uses the remarks field as drawer/bin locations."
        )

        st.markdown("#### ⚡ Fast Mode")
        fast_mode = st.toggle("Analyze fewer lots for faster results", value=True, help="Fast Mode reduces BrickLink price-guide calls. You can always turn it off for a full analysis.")
        if fast_mode:
            fast_max_lots = st.slider("Maximum lots to check", min_value=25, max_value=300, value=100, step=25)
            fast_min_qty = st.number_input("Only check lots with quantity at least", min_value=1, max_value=50, value=2, step=1, help="Minifigs are still kept when Include minifigs is turned on.")
            fast_sort = st.selectbox("Which lots should be checked first?", ["Minifigs first, then quantity", "Highest quantity first", "Item number order"], index=0)
            st.caption("Choose lot types to include in Fast Mode:")
            f1, f2 = st.columns(2)
            with f1:
                fast_include_parts = st.checkbox("Parts", value=True)
                fast_include_minifigs = st.checkbox("Minifigs", value=True)
            with f2:
                fast_include_books = st.checkbox("Instructions/books", value=False)
                fast_include_sets = st.checkbox("Boxes/sets", value=False)
        else:
            fast_max_lots = 0
            fast_min_qty = 1
            fast_sort = "Item number order"
            fast_include_parts = True
            fast_include_minifigs = True
            fast_include_books = True
            fast_include_sets = True
    submitted = st.form_submit_button("Analyze Set")

if submitted:
    if not set_no.strip():
        st.error("Enter a LEGO set number, like 75311-1.")
    elif not all([ck, cs, tv, ts]):
        st.error("Add your BrickLink API keys in the setup section or in Streamlit Secrets.")
    else:
        set_no = set_no.strip()
        condition = "N" if condition_text == "New" else "U"
        try:
            with st.spinner("Getting set inventory from BrickLink..."):
                set_name = get_set_name(set_no, ck, cs, tv, ts)
                parts = get_set_parts_cached(set_no, break_minifigs, include_box, include_instructions, ck, cs, tv, ts)
                parts_to_analyze, fast_info = apply_fast_mode_filter(
                    parts,
                    fast_mode,
                    fast_max_lots,
                    fast_min_qty,
                    fast_include_parts,
                    fast_include_minifigs,
                    fast_include_books,
                    fast_include_sets,
                    fast_sort,
                )
            if parts.empty:
                st.warning("No lots were returned. Check the set number and API access.")
            elif parts_to_analyze.empty:
                st.warning("Fast Mode filters removed every lot. Loosen the custom settings or turn Fast Mode off.")
            else:
                if fast_info.get("enabled"):
                    st.info(f"Fast Mode is checking {fast_info['analyzed_lots']} of {fast_info['original_lots']} lots. Turn it off for a full-set analysis.")
                result_df, summary = analyze(parts_to_analyze, set_no, buy_cost, condition, country_code.strip().upper(), currency_code.strip().upper(), ck, cs, tv, ts)
                pull_list_df = pd.DataFrame()
                if check_store_inventory:
                    result_df, pull_list_df = add_store_inventory_matches(result_df, condition, ck, cs, tv, ts)
                    summary["Store Inventory Check"] = "On"
                    summary["Matching Store Lots"] = int(result_df["exists_in_store"].sum()) if "exists_in_store" in result_df.columns else 0
                    summary["Drawer Pull Rows"] = len(pull_list_df)
                else:
                    summary["Store Inventory Check"] = "Off"
                    summary["Matching Store Lots"] = 0
                    summary["Drawer Pull Rows"] = 0
                summary["Set Name"] = set_name
                summary["Fast Mode"] = "On" if fast_info.get("enabled") else "Off"
                summary["Total Lots in Set"] = fast_info.get("original_lots", len(parts_to_analyze))
                summary["Analyzed Lots"] = fast_info.get("analyzed_lots", len(parts_to_analyze))
                save_history(summary)
                st.session_state["result_df"] = result_df
                st.session_state["pull_list_df"] = pull_list_df
                st.session_state["summary"] = summary
                st.session_state["set_no"] = set_no
        except Exception as e:
            st.error(str(e))

if "summary" in st.session_state and "result_df" in st.session_state:
    summary = st.session_state["summary"]
    df = st.session_state["result_df"]
    pull_list_df = st.session_state.get("pull_list_df", pd.DataFrame())
    st.markdown("### Results")
    st.markdown(f"""
        <div class="card">
            <p class="recommend">{summary['Recommendation']}</p>
            <div class="muted">Score: {summary['Score']}/100 • {summary.get('Set Name', summary['Set Number'])}</div>
            <span class="pill">ROI {summary['ROI Multiple']}×</span>
            <span class="pill">Demand {summary['% Lots With Demand']}%</span>
            <span class="pill">{summary['High Demand Lots']} hot lots</span>
            <span class="pill">Fast Mode {summary.get('Fast Mode', 'Off')}</span>
            <span class="pill">Store Inventory {summary.get('Store Inventory Check', 'Off')}</span>
        </div>
    """, unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.metric("Part-Out Value", f"${summary['Part-Out Value']:,.2f}")
    c2.metric("ROI Multiple", f"{summary['ROI Multiple']}×")
    c3, c4 = st.columns(2)
    c3.metric("Lots With Demand", f"{summary['Lots With Demand']} / {summary['Total Lots']}")
    c4.metric("Slow/Dead Lots", summary["Slow/Dead Lots"])
    if summary.get("Fast Mode") == "On":
        st.caption(f"Fast Mode analyzed {summary.get('Analyzed Lots')} of {summary.get('Total Lots in Set')} total lots. The part-out value and recommendation are based only on the analyzed lots.")

    label_counts = df["demand_label"].value_counts().reset_index()
    label_counts.columns = ["Demand Label", "Lots"]
    fig = px.bar(label_counts, x="Demand Label", y="Lots", title="Demand by Lot Count")
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### Top lots to list first")
    top_cols = ["item_no", "item_name", "color_id", "total_qty", "sold_qty_avg_price", "sold_lots_6mo", "current_lots", "demand_score", "demand_label", "estimated_part_out_value"]
    st.dataframe(df.sort_values(["demand_score", "estimated_part_out_value"], ascending=[False, False])[top_cols].head(25), use_container_width=True, hide_index=True)

    if summary.get("Store Inventory Check") == "On":
        st.markdown("### Drawer pull list")
        if pull_list_df.empty:
            st.info("No matching active store inventory lots were found for the analyzed lots, or no drawer remarks were returned.")
        else:
            st.caption("Sorted by BrickLink remarks so you can pull drawers/bins in order and add the new set quantities to existing lots.")
            pull_cols = ["drawer_remarks", "item_no", "item_name", "color_id", "qty_to_add_from_set", "current_store_qty", "new_total_if_added_here", "demand_label", "demand_score"]
            st.dataframe(pull_list_df[pull_cols], use_container_width=True, hide_index=True)
            st.download_button(
                "Download Drawer Pull List CSV",
                pull_list_df.to_csv(index=False),
                file_name=f"{summary['Set Number']}_drawer_pull_list.csv",
                mime="text/csv",
            )

    excel_bytes = to_excel(summary, df, pull_list_df)
    st.download_button("Download Excel Report", excel_bytes, file_name=f"{summary['Set Number']}_brick_curator_pro.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.markdown("### Analysis history")
hist = load_history()
if hist.empty:
    st.markdown('<p class="small">No saved analyses yet. Run a set to start building your buy list history.</p>', unsafe_allow_html=True)
else:
    show_cols = [c for c in ["Analyzed At", "Set Number", "Set Name", "Buy Cost", "Part-Out Value", "ROI Multiple", "% Lots With Demand", "Score", "Recommendation", "Store Inventory Check", "Matching Store Lots"] if c in hist.columns]
    st.dataframe(hist[show_cols].tail(20).iloc[::-1], use_container_width=True, hide_index=True)
    st.download_button("Download History CSV", hist.to_csv(index=False), file_name="brick_curator_history.csv", mime="text/csv")

st.caption("Brick Curator Pro uses BrickLink API data. Prices and demand can change quickly. Use this as a sourcing aid, not a guarantee of sales.")
