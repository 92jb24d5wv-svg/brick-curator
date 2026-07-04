import os
import re
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup
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

        if len(data) < 100:
            break
        page += 1
        time.sleep(0.05)

    return inventory_rows


def add_store_inventory_matches(df, condition, ck, cs, tv, ts):
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


def detect_set_number_from_text(text):
    match = re.search(r"\b\d{5}\b", text)
    return f"{match.group(0)}-1" if match else None


def detect_price_from_text(text):
    prices = re.findall(r"\$\s?(\d+(?:\.\d{2})?)", text)
    prices = [float(p) for p in prices if float(p) > 5]
    return min(prices) if prices else None


def scan_product_link(url):
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(response.text, "lxml")
        text = soup.get_text(" ", strip=True)

        return {
            "url": url,
            "title": soup.title.string.strip() if soup.title and soup.title.string else "Unknown Product",
            "set_no": detect_set_number_from_text(text + " " + url),
            "price": detect_price_from_text(text),
            "error": "",
        }
    except Exception as e:
        return {
            "url": url,
            "title": "Scan failed",
            "set_no": None,
            "price": None,
            "error": str(e),
        }



@st.cache_data(show_spinner=False, ttl=60 * 60)
def get_store_inventory_all_cached(condition_filter, ck, cs, tv, ts):
    """Download active BrickLink inventory for Opportunity Finder."""
    auth = make_auth(ck, cs, tv, ts)
    rows = []
    page = 1

    while page <= 100:
        params = {"status": "Y", "page": page}
        if condition_filter in ["N", "U"]:
            params["new_or_used"] = condition_filter

        data = bl_get("/inventories", auth, params=params)
        if not data:
            break

        for inv in data:
            item = inv.get("item", {}) or {}
            rows.append({
                "inventory_id": inv.get("inventory_id", ""),
                "item_no": item.get("no", inv.get("item_no", "")),
                "item_name": item.get("name", ""),
                "item_type": item.get("type", inv.get("item_type", "")),
                "color_id": inv.get("color_id"),
                "condition": inv.get("new_or_used", ""),
                "store_qty": int(inv.get("quantity", 0) or 0),
                "unit_price": float(inv.get("unit_price", 0) or 0),
                "drawer_remarks": inv.get("remarks", "") or "No remarks / no drawer",
            })

        if len(data) < 100:
            break

        page += 1
        time.sleep(0.05)

    return pd.DataFrame(rows)


def target_stock_from_demand(demand_label_text, sold_qty_6mo):
    if demand_label_text == "High Demand":
        return max(25, int(sold_qty_6mo * 1.5))
    if demand_label_text == "Good Demand":
        return max(15, int(sold_qty_6mo * 1.0))
    if demand_label_text == "Slow":
        return max(5, int(sold_qty_6mo * 0.5))
    return 0


def build_opportunity_gaps_from_store(store_df, condition, country_code, currency_code, max_lots, min_sold_lots, max_owned_qty, ck, cs, tv, ts):
    """Find low-stock inventory lots that have BrickLink sold demand."""
    if store_df.empty:
        return pd.DataFrame()

    grouped = (
        store_df
        .groupby(["item_no", "item_name", "item_type", "color_id"], as_index=False, dropna=False)
        .agg({
            "store_qty": "sum",
            "unit_price": "mean",
            "drawer_remarks": lambda x: " | ".join(sorted(set(str(v) for v in x if str(v).strip())))
        })
    )

    grouped = grouped[grouped["item_type"].isin(["PART", "MINIFIG"])]
    grouped = grouped[grouped["store_qty"] <= int(max_owned_qty)]
    grouped = grouped.sort_values(["store_qty", "item_type", "item_no"], ascending=[True, True, True]).head(int(max_lots))

    rows = []
    progress = st.progress(0, text="Finding inventory gaps...")
    total = len(grouped)

    for idx, row in grouped.reset_index(drop=True).iterrows():
        progress.progress(min(1.0, (idx + 1) / max(total, 1)), text=f"Checking demand {idx + 1} of {total}: {row['item_no']}")

        sold = get_price_guide_cached(row["item_type"], row["item_no"], row["color_id"], condition, "sold", country_code, currency_code, ck, cs, tv, ts)
        time.sleep(0.08)
        stock = get_price_guide_cached(row["item_type"], row["item_no"], row["color_id"], condition, "stock", country_code, currency_code, ck, cs, tv, ts)

        sold_lots = sold["unit_quantity"]
        sold_qty = sold["total_quantity"]
        current_lots = stock["unit_quantity"]

        if sold_lots < min_sold_lots:
            continue

        demand_score_raw = sold_lots / current_lots if current_lots > 0 else float(sold_lots)
        label = demand_label(demand_score_raw, sold_lots)
        target_stock = target_stock_from_demand(label, sold_qty)
        store_qty = int(row["store_qty"] or 0)
        restock_qty = max(0, target_stock - store_qty)

        if restock_qty <= 0:
            continue

        opportunity_score = 0
        opportunity_score += min(40, restock_qty / max(target_stock, 1) * 40)
        opportunity_score += min(30, demand_score_raw / 1.5 * 30)
        opportunity_score += min(20, sold_lots / 25 * 20)
        opportunity_score += 10 if row["item_type"] == "MINIFIG" else 0
        opportunity_score = round(max(0, min(100, opportunity_score)))

        rows.append({
            "Opportunity Score": opportunity_score,
            "Item": row["item_no"],
            "Name": row["item_name"],
            "Type": row["item_type"],
            "Color": row["color_id"],
            "You Own": store_qty,
            "Target Stock": target_stock,
            "Suggested Restock": restock_qty,
            "Demand": label,
            "Sold Lots 6mo": sold_lots,
            "Sold Qty 6mo": sold_qty,
            "Current Seller Lots": current_lots,
            "Avg Sold Price": sold["qty_avg_price"],
            "Est. Restock Value": round(restock_qty * sold["qty_avg_price"], 2),
            "Drawer Remarks": row["drawer_remarks"],
        })

        time.sleep(0.08)

    progress.empty()

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values("Opportunity Score", ascending=False).reset_index(drop=True)


def score_set_against_gaps(parts_df, gaps_df, set_no, set_name, buy_cost=0):
    """Score one LEGO set against the user's current inventory gaps."""
    if parts_df.empty or gaps_df.empty:
        return None

    gap_lookup = {}
    for _, gap in gaps_df.iterrows():
        key = (str(gap["Item"]), str(gap["Type"]), str(gap["Color"]))
        gap_lookup[key] = gap

    matched = []

    for _, part in parts_df.iterrows():
        key = (str(part["item_no"]), str(part["item_type"]), str(part["color_id"]))
        if key not in gap_lookup:
            continue

        gap = gap_lookup[key]
        qty_from_set = int(part["total_qty"] or 0)
        useful_qty = min(qty_from_set, int(gap["Suggested Restock"] or 0))

        if useful_qty <= 0:
            continue

        matched.append({
            "Set": set_no,
            "Set Name": set_name,
            "Item": part["item_no"],
            "Name": part["item_name"],
            "Type": part["item_type"],
            "Color": part["color_id"],
            "Qty From Set": qty_from_set,
            "Useful Qty": useful_qty,
            "Gap Score": gap["Opportunity Score"],
            "Demand": gap["Demand"],
            "Estimated Value Filled": round(useful_qty * float(gap["Avg Sold Price"] or 0), 2),
        })

    if not matched:
        return {
            "summary": {
                "Set": set_no,
                "Set Name": set_name,
                "Buy Cost": buy_cost,
                "Opportunity Score": 0,
                "Gap Lots Filled": 0,
                "Useful Qty Filled": 0,
                "Estimated Gap Value Filled": 0,
                "Recommendation": "❌ Low fit",
            },
            "matches": pd.DataFrame()
        }

    match_df = pd.DataFrame(matched)
    useful_qty = int(match_df["Useful Qty"].sum())
    gap_lots = int((match_df["Useful Qty"] > 0).sum())
    weighted_score = float((match_df["Useful Qty"] * match_df["Gap Score"]).sum())
    estimated_gap_value = float(match_df["Estimated Value Filled"].sum())

    score = round(min(100, (weighted_score / max(useful_qty, 1)) * min(1.0, gap_lots / 25 + 0.25)))

    if score >= 80:
        rec = "🔥 Strong inventory fit"
    elif score >= 65:
        rec = "✅ Good inventory fit"
    elif score >= 45:
        rec = "⚠️ Some useful restock"
    else:
        rec = "❌ Low fit"

    return {
        "summary": {
            "Set": set_no,
            "Set Name": set_name,
            "Buy Cost": buy_cost,
            "Opportunity Score": score,
            "Gap Lots Filled": gap_lots,
            "Useful Qty Filled": useful_qty,
            "Estimated Gap Value Filled": round(estimated_gap_value, 2),
            "Recommendation": rec,
        },
        "matches": match_df.sort_values(["Gap Score", "Useful Qty"], ascending=[False, False])
    }




def safe_money(value):
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "$0.00"


def ai_recommendation_from_history(hist):
    if hist is None or hist.empty:
        return "No set-analysis history yet. Run a few Set Analyzer reports first so Brick Curator AI can compare your past opportunities."

    recent = hist.tail(20).copy()

    lines = []
    if "Recommendation" in recent.columns:
        strong = recent[recent["Recommendation"].astype(str).str.contains("Strong Buy", case=False, na=False)]
        buys = recent[recent["Recommendation"].astype(str).str.contains("Buy", case=False, na=False)]
        passes = recent[recent["Recommendation"].astype(str).str.contains("Pass", case=False, na=False)]

        if not strong.empty:
            best = strong.sort_values("Score", ascending=False).iloc[0] if "Score" in strong.columns else strong.iloc[-1]
            lines.append(f"Your best recent set-analysis opportunity is {best.get('Set Number', 'Unknown Set')} with a {best.get('Recommendation', 'Strong Buy')} rating.")
        elif not buys.empty:
            best = buys.sort_values("Score", ascending=False).iloc[0] if "Score" in buys.columns else buys.iloc[-1]
            lines.append(f"Your best recent candidate is {best.get('Set Number', 'Unknown Set')} with a {best.get('Recommendation', 'Buy')} rating.")
        else:
            lines.append("Your recent set analyses do not show a clear buy yet.")

        if len(passes) >= max(2, len(recent) * 0.4):
            lines.append("A lot of recent sets are showing weak scores, so be selective and wait for better discounts.")

    if "ROI Multiple" in recent.columns:
        roi_numeric = pd.to_numeric(recent["ROI Multiple"], errors="coerce")
        avg_roi = roi_numeric.mean()
        max_roi = roi_numeric.max()
        if pd.notna(avg_roi):
            lines.append(f"Your recent average ROI is about {avg_roi:.2f}×, with the best recent ROI around {max_roi:.2f}×.")

    if "% Lots With Demand" in recent.columns:
        demand_numeric = pd.to_numeric(recent["% Lots With Demand"], errors="coerce")
        avg_demand = demand_numeric.mean()
        if pd.notna(avg_demand):
            if avg_demand >= 60:
                lines.append("Demand quality looks healthy across recent analyses.")
            elif avg_demand >= 35:
                lines.append("Demand quality is mixed. Prioritize minifig-heavy sets or sets with obvious high-velocity parts.")
            else:
                lines.append("Demand quality looks weak. Avoid buying purely on part-out value until demand improves.")

    return " ".join(lines)


def ai_recommendation_from_deals(deals_df):
    if deals_df is None or deals_df.empty:
        return "No Deal Scanner results are loaded yet."

    df = deals_df.copy()
    df["ROI_num"] = pd.to_numeric(df.get("ROI"), errors="coerce")
    df["Profit_num"] = pd.to_numeric(df.get("Estimated Profit"), errors="coerce")
    valid = df.dropna(subset=["ROI_num"])

    if valid.empty:
        return "Deal Scanner has results, but none have a usable ROI yet. Use manual price fallback if retailer price detection failed."

    best = valid.sort_values(["ROI_num", "Profit_num"], ascending=[False, False]).iloc[0]
    rec = f"Best current scanned deal: {best.get('Set', 'Unknown Set')} at {safe_money(best.get('Website Price', 0))}, ROI {best.get('ROI', 0)}×, estimated profit {safe_money(best.get('Estimated Profit', 0))}."

    strong = valid[valid["ROI_num"] >= 2.5]
    if not strong.empty:
        rec += f" You have {len(strong)} scanned deal(s) at or above 2.5× ROI."
    else:
        rec += " None of the scanned deals are at the 2.5× ROI target yet."

    return rec


def ai_recommendation_from_gaps(gaps_df):
    if gaps_df is None or gaps_df.empty:
        return "No Opportunity Finder gap results are loaded yet."

    df = gaps_df.copy()
    df["Score_num"] = pd.to_numeric(df.get("Opportunity Score"), errors="coerce")
    top = df.sort_values("Score_num", ascending=False).head(5)

    if top.empty:
        return "Opportunity Finder did not find strong inventory gaps with the current filters."

    top_parts = ", ".join([f"{r['Item']} ({r['Demand']})" for _, r in top.iterrows()])
    total_value = pd.to_numeric(df.get("Est. Restock Value"), errors="coerce").sum()

    return f"Your top inventory gaps are: {top_parts}. Estimated useful restock value across found gaps is {safe_money(total_value)}."


def build_ai_action_plan(hist, deals_df, gaps_df, set_scores_df):
    actions = []

    if deals_df is not None and not deals_df.empty:
        d = deals_df.copy()
        d["ROI_num"] = pd.to_numeric(d.get("ROI"), errors="coerce")
        d["Profit_num"] = pd.to_numeric(d.get("Estimated Profit"), errors="coerce")
        good_deals = d[(d["ROI_num"] >= 2.5) & (d["Profit_num"] > 0)]
        if not good_deals.empty:
            best = good_deals.sort_values(["ROI_num", "Profit_num"], ascending=[False, False]).iloc[0]
            actions.append(f"Buy/check availability for {best.get('Set', 'the top scanned set')} first. It has the strongest deal score in your current scan.")
        else:
            actions.append("Do not rush the currently scanned deals. Wait for a better price or use manual price fallback if price detection was wrong.")

    if set_scores_df is not None and not set_scores_df.empty:
        s = set_scores_df.copy()
        s["Score_num"] = pd.to_numeric(s.get("Opportunity Score"), errors="coerce")
        best_fit = s.sort_values("Score_num", ascending=False).iloc[0]
        if best_fit.get("Opportunity Score", 0) >= 65:
            actions.append(f"Prioritize {best_fit.get('Set', 'the top candidate set')} because it fits your current inventory gaps well.")
        else:
            actions.append("The candidate sets only weakly match your inventory gaps. Test more set numbers before buying.")

    if gaps_df is not None and not gaps_df.empty:
        g = gaps_df.copy()
        g["Score_num"] = pd.to_numeric(g.get("Opportunity Score"), errors="coerce")
        top_gap = g.sort_values("Score_num", ascending=False).iloc[0]
        actions.append(f"Restock or source more of {top_gap.get('Item', 'your highest-gap item')} because it is your highest Opportunity Finder gap.")

    if hist is not None and not hist.empty and "ROI Multiple" in hist.columns:
        recent = hist.tail(20).copy()
        avg_roi = pd.to_numeric(recent["ROI Multiple"], errors="coerce").mean()
        if pd.notna(avg_roi) and avg_roi < 2.0:
            actions.append("Raise your buy threshold. Your recent average ROI is under 2.0×.")
        elif pd.notna(avg_roi) and avg_roi >= 2.5:
            actions.append("Your recent ROI quality is strong. Focus on processing speed and listing high-demand lots first.")

    if not actions:
        actions.append("Run Set Analyzer, Deal Scanner, and Opportunity Finder first. Brick Curator AI gets smarter after those tools create data.")

    return actions[:5]


def build_ai_briefing(hist, deals_df, gaps_df, set_scores_df):
    return {
        "history": ai_recommendation_from_history(hist),
        "deals": ai_recommendation_from_deals(deals_df),
        "gaps": ai_recommendation_from_gaps(gaps_df),
        "actions": build_ai_action_plan(hist, deals_df, gaps_df, set_scores_df),
    }



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

tool = st.radio(
    "Choose tool",
    ["Set Analyzer", "Deal Scanner", "Opportunity Finder", "Brick Curator AI"],
    horizontal=True
)

if tool == "Set Analyzer":
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


if tool == "Deal Scanner":
    st.markdown("### 🔎 Deal Scanner")
    st.write("Paste LEGO product links. The app will detect the set number, estimate the website price, pull BrickLink part-out value, and rank the deals.")

    deal_links = st.text_area(
        "Paste product links, one per line",
        placeholder="https://www.walmart.com/...\nhttps://www.target.com/...\nhttps://www.amazon.com/..."
    )

    col1, col2 = st.columns(2)
    with col1:
        deal_condition_text = st.radio("Deal condition", ["New", "Used"], horizontal=True, key="deal_condition")
        deal_country_code = st.text_input("Country filter", value="US", key="deal_country")
    with col2:
        deal_currency_code = st.text_input("Currency", value="USD", key="deal_currency")
        manual_price = st.number_input("Manual price fallback", min_value=0.0, value=0.0, step=1.0)

    with st.expander("Deal Scanner speed settings"):
        deal_fast_max_lots = st.slider("Maximum lots to check per set", 25, 300, 100, 25)
        deal_fast_min_qty = st.number_input("Only check lots with quantity at least", 1, 50, 2, 1)
        deal_break_minifigs = st.checkbox("Break minifigs into parts", value=False)
        st.caption("Some retailer pages block automatic price reading. Use manual price fallback when needed.")

    if st.button("Scan Deals"):
        if not all([ck, cs, tv, ts]):
            st.error("Add your BrickLink API keys first.")
        else:
            urls = [u.strip() for u in deal_links.splitlines() if u.strip()]
            if not urls:
                st.warning("Paste at least one product link.")
            else:
                deal_rows = []
                condition = "N" if deal_condition_text == "New" else "U"

                for url in urls:
                    scanned = scan_product_link(url)
                    set_no = scanned["set_no"]
                    website_price = scanned["price"] or manual_price

                    if not set_no:
                        deal_rows.append({
                            "Set": "Not detected",
                            "Product": scanned["title"],
                            "Website Price": website_price if website_price else None,
                            "Part-Out Value": None,
                            "ROI": None,
                            "Estimated Profit": None,
                            "Score": None,
                            "Recommendation": "Needs set number",
                            "Analyzed Lots": None,
                            "Total Lots": None,
                            "Link": url,
                        })
                        continue

                    if not website_price:
                        deal_rows.append({
                            "Set": set_no,
                            "Product": scanned["title"],
                            "Website Price": None,
                            "Part-Out Value": None,
                            "ROI": None,
                            "Estimated Profit": None,
                            "Score": None,
                            "Recommendation": "Needs price",
                            "Analyzed Lots": None,
                            "Total Lots": None,
                            "Link": url,
                        })
                        continue

                    try:
                        set_name = get_set_name(set_no, ck, cs, tv, ts)

                        with st.spinner(f"Analyzing {set_no}..."):
                            parts = get_set_parts_cached(
                                set_no,
                                deal_break_minifigs,
                                False,
                                False,
                                ck,
                                cs,
                                tv,
                                ts
                            )

                            parts_to_analyze, fast_info = apply_fast_mode_filter(
                                parts,
                                True,
                                deal_fast_max_lots,
                                deal_fast_min_qty,
                                True,
                                True,
                                False,
                                False,
                                "Minifigs first, then quantity",
                            )

                            if parts.empty:
                                raise RuntimeError("No lots returned from BrickLink.")
                            if parts_to_analyze.empty:
                                raise RuntimeError("No lots found after filtering.")

                            result_df, summary = analyze(
                                parts_to_analyze,
                                set_no,
                                website_price,
                                condition,
                                deal_country_code.strip().upper(),
                                deal_currency_code.strip().upper(),
                                ck,
                                cs,
                                tv,
                                ts
                            )

                        part_out_value = summary["Part-Out Value"]
                        roi = summary["ROI Multiple"]

                        estimated_fees = part_out_value * 0.13
                        supplies = 3
                        estimated_profit = part_out_value - website_price - estimated_fees - supplies

                        deal_rows.append({
                            "Set": set_no,
                            "Product": set_name,
                            "Website Price": round(float(website_price), 2),
                            "Part-Out Value": part_out_value,
                            "ROI": roi,
                            "Estimated Profit": round(estimated_profit, 2),
                            "Score": summary["Score"],
                            "Recommendation": summary["Recommendation"],
                            "Analyzed Lots": fast_info.get("analyzed_lots"),
                            "Total Lots": fast_info.get("original_lots"),
                            "Link": url,
                        })

                    except Exception as e:
                        deal_rows.append({
                            "Set": set_no,
                            "Product": scanned["title"],
                            "Website Price": round(float(website_price), 2) if website_price else None,
                            "Part-Out Value": None,
                            "ROI": None,
                            "Estimated Profit": None,
                            "Score": None,
                            "Recommendation": f"Scan failed: {e}",
                            "Analyzed Lots": None,
                            "Total Lots": None,
                            "Link": url,
                        })

                deals_df = pd.DataFrame(deal_rows)

                if not deals_df.empty:
                    deals_df = deals_df.sort_values("ROI", ascending=False, na_position="last")
                    st.session_state["deals_df"] = deals_df

    if "deals_df" in st.session_state:
        deals_df = st.session_state["deals_df"]
        st.markdown("### Best Deals")
        st.dataframe(deals_df, use_container_width=True, hide_index=True)

        st.download_button(
            "Download Deal Scan CSV",
            deals_df.to_csv(index=False),
            file_name="brick_curator_deal_scan.csv",
            mime="text/csv",
        )


if tool == "Opportunity Finder":
    st.markdown("### 🧭 Opportunity Finder")
    st.write("Version 1 finds low-stock, high-demand lots in your own BrickLink store and can score candidate sets against those gaps.")

    col1, col2 = st.columns(2)
    with col1:
        opp_condition_text = st.radio("Inventory condition", ["New", "Used", "Both"], horizontal=True, key="opp_condition")
        opp_country_code = st.text_input("Country filter", value="US", key="opp_country")
        opp_max_owned_qty = st.number_input("Only check lots where I own this many or fewer", min_value=0, max_value=500, value=10, step=1)
    with col2:
        opp_currency_code = st.text_input("Currency", value="USD", key="opp_currency")
        opp_max_lots = st.slider("Max low-stock lots to check", 25, 300, 100, 25)
        opp_min_sold_lots = st.number_input("Minimum sold lots in 6 months", min_value=0, max_value=100, value=2, step=1)

    with st.expander("Optional: score sets against your gaps"):
        candidate_sets_text = st.text_area(
            "Candidate set numbers, one per line",
            placeholder="75372-1\n60431-1\n31154-1"
        )
        candidate_buy_cost = st.number_input(
            "Optional buy cost for each candidate set",
            min_value=0.0,
            value=0.0,
            step=1.0,
            key="opp_buy_cost"
        )

    if st.button("Find Inventory Gaps"):
        if not all([ck, cs, tv, ts]):
            st.error("Add your BrickLink API keys first.")
        else:
            condition_filter = "N" if opp_condition_text == "New" else "U" if opp_condition_text == "Used" else "B"

            try:
                with st.spinner("Downloading your active BrickLink inventory..."):
                    store_df = get_store_inventory_all_cached(condition_filter, ck, cs, tv, ts)

                if store_df.empty:
                    st.warning("No active store inventory was found.")
                else:
                    pricing_condition = "N" if condition_filter == "B" else condition_filter
                    if condition_filter == "B":
                        st.info("Both conditions selected. This first version uses New price-guide data for demand scoring.")

                    gaps_df = build_opportunity_gaps_from_store(
                        store_df,
                        pricing_condition,
                        opp_country_code.strip().upper(),
                        opp_currency_code.strip().upper(),
                        opp_max_lots,
                        opp_min_sold_lots,
                        opp_max_owned_qty,
                        ck,
                        cs,
                        tv,
                        ts
                    )

                    st.session_state["opportunity_gaps_df"] = gaps_df

                    candidate_sets = [s.strip() for s in candidate_sets_text.splitlines() if s.strip()]
                    set_summaries = []
                    all_matches = []

                    if candidate_sets and not gaps_df.empty:
                        for raw_set in candidate_sets:
                            candidate_set_no = raw_set if "-" in raw_set else f"{raw_set}-1"
                            set_name = get_set_name(candidate_set_no, ck, cs, tv, ts)
                            parts = get_set_parts_cached(candidate_set_no, False, False, False, ck, cs, tv, ts)
                            scored = score_set_against_gaps(parts, gaps_df, candidate_set_no, set_name, candidate_buy_cost)

                            if scored:
                                set_summaries.append(scored["summary"])
                                if not scored["matches"].empty:
                                    all_matches.append(scored["matches"])

                    st.session_state["opportunity_set_scores_df"] = (
                        pd.DataFrame(set_summaries).sort_values("Opportunity Score", ascending=False)
                        if set_summaries else pd.DataFrame()
                    )
                    st.session_state["opportunity_matches_df"] = (
                        pd.concat(all_matches, ignore_index=True)
                        if all_matches else pd.DataFrame()
                    )

            except Exception as e:
                st.error(str(e))

    if "opportunity_gaps_df" in st.session_state:
        gaps_df = st.session_state["opportunity_gaps_df"]

        st.markdown("### Highest-value inventory gaps")
        if gaps_df.empty:
            st.info("No strong gaps found with the current filters. Try increasing max lots checked, increasing the owned-quantity limit, or lowering minimum sold lots.")
        else:
            c1, c2 = st.columns(2)
            c1.metric("Gap Lots Found", len(gaps_df))
            c2.metric("Estimated Restock Value", f"${gaps_df['Est. Restock Value'].sum():,.2f}")

            display_cols = [
                "Opportunity Score", "Item", "Name", "Type", "Color", "You Own",
                "Target Stock", "Suggested Restock", "Demand", "Sold Lots 6mo",
                "Sold Qty 6mo", "Current Seller Lots", "Avg Sold Price", "Drawer Remarks"
            ]

            st.dataframe(gaps_df[display_cols].head(100), use_container_width=True, hide_index=True)

            st.download_button(
                "Download Inventory Gaps CSV",
                gaps_df.to_csv(index=False),
                file_name="brick_curator_inventory_gaps.csv",
                mime="text/csv",
            )

    if "opportunity_set_scores_df" in st.session_state:
        set_scores_df = st.session_state["opportunity_set_scores_df"]
        matches_df = st.session_state.get("opportunity_matches_df", pd.DataFrame())

        if not set_scores_df.empty:
            st.markdown("### Best sets for your inventory gaps")
            st.dataframe(set_scores_df, use_container_width=True, hide_index=True)

            st.download_button(
                "Download Set Opportunity Scores CSV",
                set_scores_df.to_csv(index=False),
                file_name="brick_curator_set_opportunity_scores.csv",
                mime="text/csv",
            )

            if not matches_df.empty:
                st.markdown("### Matching parts from scored sets")
                st.dataframe(matches_df, use_container_width=True, hide_index=True)

                st.download_button(
                    "Download Set Gap Matches CSV",
                    matches_df.to_csv(index=False),
                    file_name="brick_curator_set_gap_matches.csv",
                    mime="text/csv",
                )




if tool == "Brick Curator AI":
    st.markdown("### 🤖 Brick Curator AI")
    st.write("Your store assistant. It summarizes your analyses, deal scans, and inventory gaps into a simple action plan.")

    hist = load_history()
    deals_df = st.session_state.get("deals_df", pd.DataFrame())
    gaps_df = st.session_state.get("opportunity_gaps_df", pd.DataFrame())
    set_scores_df = st.session_state.get("opportunity_set_scores_df", pd.DataFrame())

    if st.button("Generate Brick Curator AI Briefing"):
        briefing = build_ai_briefing(hist, deals_df, gaps_df, set_scores_df)
        st.session_state["ai_briefing"] = briefing

    if "ai_briefing" in st.session_state:
        briefing = st.session_state["ai_briefing"]

        st.markdown(
            f"""
            <div class="card">
                <p class="recommend">Today's Brick Curator Briefing</p>
                <div class="muted">Based on your saved set history plus any Deal Scanner or Opportunity Finder results currently loaded.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("#### 📦 Set Analysis Read")
        st.write(briefing["history"])

        st.markdown("#### 🔎 Deal Scanner Read")
        st.write(briefing["deals"])

        st.markdown("#### 🧭 Inventory Gap Read")
        st.write(briefing["gaps"])

        st.markdown("#### ✅ Recommended Next Actions")
        for i, action in enumerate(briefing["actions"], start=1):
            st.write(f"{i}. {action}")

        ai_text = "Brick Curator AI Briefing\n\n"
        ai_text += "Set Analysis Read:\n" + briefing["history"] + "\n\n"
        ai_text += "Deal Scanner Read:\n" + briefing["deals"] + "\n\n"
        ai_text += "Inventory Gap Read:\n" + briefing["gaps"] + "\n\n"
        ai_text += "Recommended Next Actions:\n"
        for i, action in enumerate(briefing["actions"], start=1):
            ai_text += f"{i}. {action}\n"

        st.download_button(
            "Download AI Briefing TXT",
            ai_text,
            file_name="brick_curator_ai_briefing.txt",
            mime="text/plain",
        )

    with st.expander("How to get better AI recommendations"):
        st.write(
            "Run the tools in this order: 1) Deal Scanner for current deals, 2) Opportunity Finder for your store gaps, "
            "3) Set Analyzer for any serious candidate, then come back here and generate the briefing."
        )



st.caption("Brick Curator Pro uses BrickLink API data. Prices and demand can change quickly. Use this as a sourcing aid, not a guarantee of sales.")
