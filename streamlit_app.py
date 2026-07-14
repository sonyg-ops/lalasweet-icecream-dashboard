# -*- coding: utf-8 -*-
"""
라라스윗 광고 대시보드
Streamlit + Plotly | 데이터 소스: Google Sheets (통합RD_원본)
"""
import html as _html
import re
import time
import uuid
import requests
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import gspread
from google.oauth2.service_account import Credentials
from datetime import date, timedelta
# --- 페이지 설정 ---
st.set_page_config(
    page_title="라라스윗 빙과 광고 대시보드",
    page_icon="🍦",
    layout="wide",
    initial_sidebar_state="expanded",
)
# --- 커스텀 CSS ---
st.markdown("""
<style>
    .block-container { padding-top: 3rem; padding-bottom: 1rem; max-width: 1400px; }
    [data-testid="stMetricValue"] { font-size: 1.75rem; font-weight: 600; }
    [data-testid="stMetricLabel"] { font-size: 0.8rem; color: #888; }
    [data-testid="stMetricDelta"] { font-size: 0.85rem; }
    [data-testid="stSidebar"] { background: #fafafa; }
    [data-testid="stSidebar"] h2 { font-size: 0.95rem !important; margin-bottom: 0.2rem; }
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] .stMarkdown p { font-size: 0.75rem !important; }
    [data-testid="stSidebar"] [data-testid="stMultiSelect"] { font-size: 0.75rem; }
    [data-testid="stSidebar"] button { font-size: 0.75rem !important; }
    [data-testid="stSidebar"] .stCaption { font-size: 0.68rem !important; }
    div[data-testid="stTabs"] button { font-size: 0.9rem; font-weight: 500; }
    .stDataFrame { border-radius: 8px; overflow: hidden; }
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)
# --- 브랜드 컬러 ---
PALETTE = ["#F4845F", "#7BAFD4", "#82C9A7", "#B5A8E0",
           "#F7B97A", "#85C1B2", "#F49AC2", "#A8D5BA"]
BAR_PALETTE = ["#F4845F", "#7BAFD4", "#F7B97A", "#82C9A7",
               "#F49AC2", "#B5A8E0", "#E8A87C", "#85C1B2"]
TOTAL_BG   = "#FFF0E6"
TOTAL_FG   = "#B84A00"
TOTAL_FONT = "bold"
# --- 5P구성 기준일 ---
PC_BEFORE_START = pd.Timestamp("2026-06-01")
PC_BEFORE_END   = pd.Timestamp("2026-06-16")
PC_AFTER_START  = pd.Timestamp("2026-06-17")
PC_AFTER_END    = pd.Timestamp("2026-06-30")
# --- 단쉐 스킴 기준일 ---
SK_SCHEME1_START = pd.Timestamp("2026-06-24")
SK_SCHEME1_END   = pd.Timestamp("2026-06-26")
SK_SCHEME2_START = pd.Timestamp("2026-06-27")
SK_SCHEME2_END   = pd.Timestamp("2026-06-30")
# --- 소재 유형 우선순위 ---
CREATIVE_TYPES = [
    "맛페인포인트.5P소구",
    "메시지검증.5P소구",
    "맛페인포인트",
    "5P소구",
]
# --- 제품군 ↔ 제품코드 매핑 ---
PRODUCT_GROUPS = {
    "파인트":   ["P혼", "P망", "P요", "P복", "P바", "P초", "P말", "P오", "P우", "P치", "P애", "P고"],
    "제로바":   ["ZB혼", "ZB오", "ZB포", "ZB자", "ZB귤", "ZB파"],
    "요거트바": ["BA딸", "BA복", "BA블", "BA망", "BA혼"],
    "저당바":   ["BA메", "BA팥"],
    "저당콘":   ["CO바"],
    "초코바":   ["C혼", "C바", "C초", "C말", "C쿠", "C딸"],
    "쭈쭈바":   ["JJ혼", "JJ소", "JJ초"],
    "모나카":   ["M혼", "M우", "M초", "M팥", "M옥", "M고"],
    "빵샌드":   ["B혼", "B우", "B고"],
    "넛티바":   ["NT바", "NT초", "NT혼"],
    "아사이볼 요거베리": ["YB사"],
    "젤라또바 (말차)":   ["JB말"],
    "피스타치오": ["JB피"],
    "선데":     ["SD초"],
    "호두 꼬숩바": ["BA호"],
    "미니생초코 (바닐라)": ["MB바"],
    "옥수수 듬뿍바": ["BA옥"],
    "듬뿍바":   ["DB딸", "DB키", "DB피", "DB혼", "DB베"],
    "쫀득바":   ["JD멜", "JD망"],
    "스틱바종류 전체 혼합": ["스혼"],
}
PRODUCT_GROUP = {code: grp for grp, codes in PRODUCT_GROUPS.items() for code in codes}
# =============================================================
# 헬퍼 함수
# =============================================================
def _esc(v) -> str:
    return _html.escape(str(v))
COL_WIDTHS = {
    "광고비": 120, "CPA": 100, "CPC": 95, "CVR": 80, "CTR": 80,
    "노출": 115, "링크 클릭": 95, "구매": 85, "소재 링크": 240,
}
def _col_width(i: int, name) -> int:
    if i == 0:
        return 190            # 첫 열(소재명 등)은 넓게 — 길면 말줄임 표시
    return COL_WIDTHS.get(str(name), 95)
def render_pinned_total_table(df: pd.DataFrame, link_col: str = None) -> None:
    tid = "tbl_" + uuid.uuid4().hex[:8]
    cols = [c for c in df.columns if c != link_col]   # link_col은 표시 안 하고 링크용으로만 사용
    first_col = cols[0]
    data  = df[df[first_col] != "총합계"].reset_index(drop=True)
    total = df[df[first_col] == "총합계"]
    th = ("position:relative; padding:0; text-align:left; background:#f0f2f6;"
          "border-bottom:2px solid #ddd; font-size:0.82rem; overflow:hidden;")
    hd = ("padding:7px 10px; overflow:hidden; text-overflow:ellipsis;"
          "white-space:nowrap; cursor:pointer; user-select:none;")
    rz = "position:absolute; top:0; right:0; width:6px; height:100%; cursor:col-resize;"
    td = ("padding:6px 10px; border-bottom:1px solid #eee; font-size:0.82rem;"
          "white-space:nowrap; overflow:hidden; text-overflow:ellipsis;")
    tf = (f"padding:6px 10px; font-size:0.82rem; white-space:nowrap;"
          f"overflow:hidden; text-overflow:ellipsis;"
          f"background:{TOTAL_BG}; color:{TOTAL_FG}; font-weight:{TOTAL_FONT};"
          f"border-top:2px solid #ddd;")
    widths  = [_col_width(i, c) for i, c in enumerate(cols)]
    total_w = sum(widths)
    colgroup = "<colgroup>" + "".join(f'<col style="width:{w}px">' for w in widths) + "</colgroup>"
    hdr = "".join(
        f'<th data-order="" data-name="{_esc(col)}" style="{th}">'
        f'<div style="{hd}" onclick="sortTbl(\'{tid}\',{i})">'
        f'{_esc(col)} <span style="color:#bbb;font-size:0.7rem">&#x21C5;</span></div>'
        f'<div style="{rz}" data-col="{i}" class="rz"></div></th>'
        for i, col in enumerate(cols)
    )
    def _cell(row, col, style):
        v = row[col]
        if link_col and col == first_col:
            url = str(row.get(link_col, "") or "").strip()
            if url:
                return (f'<td title="{_esc(v)}" style="{style}">'
                        f'<a href="{_esc(url)}" target="_blank" rel="noopener" '
                        f'style="color:#1a73e8;text-decoration:none;">{_esc(v)} &#128279;</a></td>')
        return f'<td title="{_esc(v)}" style="{style}">{_esc(v)}</td>'
    bdy = "".join(
        "<tr>" + "".join(_cell(row, col, td) for col in cols) + "</tr>"
        for _, row in data.iterrows()
    )
    ftr = ("".join(
        "<tr>" + "".join(_cell(row, col, tf) for col in cols) + "</tr>"
        for _, row in total.iterrows()
    ) if not total.empty else "")
    js = (
        "function sortTbl(tid,col){"
        "var tbl=document.getElementById(tid);"
        "var tbody=tbl.querySelector('tbody');"
        "var ths=tbl.querySelectorAll('thead th');"
        "var asc=ths[col].dataset.order!=='asc';"
        "ths.forEach(function(h){h.dataset.order='';h.querySelector('span').innerHTML='&#x21C5;';});"
        "ths[col].dataset.order=asc?'asc':'desc';"
        "ths[col].querySelector('span').innerHTML=asc?'&#x2191;':'&#x2193;';"
        "var rows=Array.from(tbody.querySelectorAll('tr'));"
        "rows.sort(function(a,b){"
        "var va=a.cells[col].textContent.replace(/[\\u20a9%,\\s]/g,'');"
        "var vb=b.cells[col].textContent.replace(/[\\u20a9%,\\s]/g,'');"
        # 문자열 전체가 순수 숫자일 때만 숫자 정렬 (날짜 '2026-07-14'가 2026으로 오판되는 것 방지)
        "var num=/^-?\\d+(\\.\\d+)?$/;"
        "if(num.test(va)&&num.test(vb)){var na=parseFloat(va),nb=parseFloat(vb);return asc?na-nb:nb-na;}"
        "return asc?va.localeCompare(vb,'ko'):vb.localeCompare(va,'ko');"
        "});"
        "rows.forEach(function(r){tbody.appendChild(r);});"
        "}"
        # 열 너비 드래그 조절
        "function initRz(tid){"
        "var t=document.getElementById(tid);"
        "var cols=t.querySelectorAll('colgroup col');"
        "var cur=-1,sx=0,sw=0,stw=0;"
        "function mv(e){if(cur<0)return;"
        "var w=Math.max(40,sw+(e.pageX-sx));"
        "cols[cur].style.width=w+'px';t.style.width=(stw-sw+w)+'px';}"
        "function up(){cur=-1;document.body.style.userSelect='';"
        "document.removeEventListener('mousemove',mv);document.removeEventListener('mouseup',up);}"
        "t.querySelectorAll('th .rz').forEach(function(h){"
        "h.addEventListener('mousedown',function(e){e.preventDefault();e.stopPropagation();"
        "cur=parseInt(h.getAttribute('data-col'));sx=e.pageX;"
        "sw=parseInt(cols[cur].style.width);stw=parseInt(t.style.width);"
        "document.body.style.userSelect='none';"
        "document.addEventListener('mousemove',mv);document.addEventListener('mouseup',up);});});"
        "}"
        # 표 전체를 TSV로 클립보드 복사 (엑셀/시트에 붙여넣기)
        "function copyTbl(tid){"
        "var t=document.getElementById(tid);var L=[];var H=[];"
        "t.querySelectorAll('thead th').forEach(function(th){H.push((th.getAttribute('data-name')||'').trim());});"
        "L.push(H.join('\\t'));"
        "t.querySelectorAll('tbody tr').forEach(function(tr){var c=[];"
        "tr.querySelectorAll('td').forEach(function(td){c.push(td.textContent.trim());});L.push(c.join('\\t'));});"
        "t.querySelectorAll('tfoot tr').forEach(function(tr){var c=[];"
        "tr.querySelectorAll('td').forEach(function(td){c.push(td.textContent.trim());});L.push(c.join('\\t'));});"
        "var tsv=L.join('\\n');"
        "var ta=document.createElement('textarea');ta.value=tsv;"
        "ta.style.position='fixed';ta.style.top='-1000px';ta.style.opacity='0';"
        "document.body.appendChild(ta);ta.focus();ta.select();"
        "var ok=false;try{ok=document.execCommand('copy');}catch(e){}"
        "document.body.removeChild(ta);"
        "var b=document.getElementById(tid+'_cpy');var o=b.innerHTML;"
        "b.innerHTML=ok?'✅ 복사됨':'⚠ 복사 실패';"
        "setTimeout(function(){b.innerHTML=o;},1500);"
        "}"
    )
    btn_style = ("font-size:0.72rem; padding:3px 10px; border:1px solid #d0d0d0;"
                 "border-radius:6px; background:#fff; color:#555; cursor:pointer;")
    html = (
        f'<div style="display:flex; justify-content:flex-end; margin-bottom:6px;">'
        f'<button id="{tid}_cpy" onclick="copyTbl(\'{tid}\')" style="{btn_style}" '
        f'title="표 전체를 복사해 엑셀·구글시트에 붙여넣을 수 있어요">&#128203; 복사</button></div>'
        '<div style="overflow-x:auto; border-radius:8px; border:1px solid #e0e0e0;">'
        f'<table id="{tid}" style="table-layout:fixed; width:{total_w}px; border-collapse:collapse;">'
        f'{colgroup}'
        f'<thead><tr>{hdr}</tr></thead>'
        f'<tbody>{bdy}</tbody>'
        f'<tfoot>{ftr}</tfoot>'
        f'</table></div>'
        f'<script>{js} initRz("{tid}");</script>'
    )
    height = max(150, 52 + len(data) * 34 + (38 if not total.empty else 0)) + 42
    components.html(html, height=height, scrolling=False)
def _page_more(sk: str, page_size: int) -> None:
    st.session_state[sk] = st.session_state.get(sk, page_size) + page_size
def _page_less(sk: str, page_size: int) -> None:
    st.session_state[sk] = page_size
# 필터 그룹별 선택 초기화 콜백 (버튼 on_click, 초기화할 key 목록을 받음)
def _reset_keys(keys) -> None:
    for k in keys:
        st.session_state[k] = []
def render_table_paged(df: pd.DataFrame, key: str, page_size: int = 10, link_col: str = None) -> None:
    """상위 page_size개(+총합계)만 보여주고 '더보기'로 10개씩 펼침.
    총합계 행은 펼침과 무관하게 항상 전체 기준으로 표시."""
    first_col = next((c for c in df.columns if c != link_col), df.columns[0])
    data  = df[df[first_col] != "총합계"].reset_index(drop=True)
    total = df[df[first_col] == "총합계"]
    n = len(data)
    if n <= page_size:
        render_pinned_total_table(df, link_col=link_col)
        return
    sk = f"shown_{key}"
    shown = min(st.session_state.get(sk, page_size), n)
    view = pd.concat([data.head(shown), total], ignore_index=True)
    render_pinned_total_table(view, link_col=link_col)
    remaining = n - shown
    c1, c2, c3 = st.columns([1.6, 1.2, 5], vertical_alignment="center")
    if remaining > 0:
        c1.button(f"더보기 (+{min(page_size, remaining)})", key=f"more_{key}",
                  on_click=_page_more, args=(sk, page_size))
    if shown > page_size:
        c2.button("접기", key=f"less_{key}", on_click=_page_less, args=(sk, page_size))
    c3.caption(f"상위 {shown} / 전체 {n}개 표시")
def build_summary_table(data: pd.DataFrame, group_col: str, label_fn=None) -> pd.DataFrame:
    data = data.copy()
    if "ThruPlay" not in data.columns:
        data["ThruPlay"] = 0
    grp = (
        data.groupby(group_col)
        .agg(광고비=("광고비 (KRW)", "sum"), 노출=("노출", "sum"),
             링크클릭=("클릭", "sum"), 구매=("전환수", "sum"),
             ThruPlay=("ThruPlay", "sum"))
        .reset_index()
    )
    grp["CTR"] = (grp["링크클릭"] / grp["노출"].replace(0, float("nan")) * 100).fillna(0)
    grp["CPC"] = (grp["광고비"] / grp["링크클릭"].replace(0, float("nan"))).fillna(0)
    grp["CVR"] = (grp["구매"] / grp["링크클릭"].replace(0, float("nan")) * 100).fillna(0)
    grp["CPA"] = (grp["광고비"] / grp["구매"].replace(0, float("nan"))).fillna(0)
    grp["결과당비용"] = (grp["광고비"] / grp["ThruPlay"].replace(0, float("nan"))).fillna(0)
    tot = grp[["광고비", "노출", "링크클릭", "구매", "ThruPlay"]].sum()
    grp = pd.concat([grp, pd.DataFrame([{
        group_col:  "총합계",
        "광고비":   tot["광고비"],
        "노출":     tot["노출"],
        "링크클릭": tot["링크클릭"],
        "구매":     tot["구매"],
        "ThruPlay": tot["ThruPlay"],
        "CTR": tot["링크클릭"] / tot["노출"] * 100 if tot["노출"] > 0 else 0,
        "CPC": tot["광고비"] / tot["링크클릭"] if tot["링크클릭"] > 0 else 0,
        "CVR": tot["구매"] / tot["링크클릭"] * 100 if tot["링크클릭"] > 0 else 0,
        "CPA": tot["광고비"] / tot["구매"] if tot["구매"] > 0 else 0,
        "결과당비용": tot["광고비"] / tot["ThruPlay"] if tot["ThruPlay"] > 0 else 0,
    }])], ignore_index=True)
    if label_fn:
        grp[group_col] = grp[group_col].apply(lambda x: label_fn(x) if x != "총합계" else x)
    return grp
def sort_summary_by_spend(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """총합계 행은 맨 아래에 유지하고, 나머지는 광고비 큰 순으로 정렬."""
    total = df[df[group_col] == "총합계"]
    data  = df[df[group_col] != "총합계"].sort_values("광고비", ascending=False)
    return pd.concat([data, total], ignore_index=True)
def filter_min_spend(df: pd.DataFrame, min_spend: float) -> pd.DataFrame:
    """합계 광고비가 min_spend 미만인 항목 행을 제거. 총합계 행은 전체 기준으로 유지.
    (숫자 광고비 컬럼 기준이므로 style_summary 전에 호출해야 함.)"""
    if not min_spend or min_spend <= 0:
        return df
    fc = df.columns[0]
    keep = (df[fc] == "총합계") | (df["광고비"] >= min_spend)
    return df[keep].reset_index(drop=True)
def style_summary(df: pd.DataFrame, first_col: str) -> pd.DataFrame:
    s = df.copy()
    s["광고비"]   = s["광고비"].apply(lambda x: f"₩{int(x):,}")
    s["노출"]     = s["노출"].apply(lambda x: f"{int(x):,}")
    s["링크클릭"] = s["링크클릭"].apply(lambda x: f"{int(x):,}")
    s["구매"]     = s["구매"].apply(lambda x: f"{int(x):,}")
    s["CTR"]     = s["CTR"].apply(lambda x: f"{x:.2f}%")
    s["CPC"]     = s["CPC"].apply(lambda x: f"{int(x):,}")
    s["CVR"]     = s["CVR"].apply(lambda x: f"{x:.2f}%")
    s["CPA"]     = s["CPA"].apply(lambda x: f"{int(x):,}")
    s = s.rename(columns={"링크클릭": "링크 클릭"})
    order = [first_col, "광고비", "CPA", "CPC", "CVR"]
    return s[[c for c in order if c in s.columns]]
def style_awareness(df: pd.DataFrame, first_col: str) -> pd.DataFrame:
    """인지광고 표: 구분 · 광고비 · ThruPlay · 결과당비용"""
    s = df.copy()
    s["광고비"]     = s["광고비"].apply(lambda x: f"₩{int(x):,}")
    s["ThruPlay"]  = s["ThruPlay"].apply(lambda x: f"{int(x):,}")
    s["결과당비용"] = s["결과당비용"].apply(lambda x: f"₩{int(x):,}")
    order = [first_col, "광고비", "ThruPlay", "결과당비용"]
    return s[[c for c in order if c in s.columns]]
def perf_row(label: str, d: pd.DataFrame, key_col: str = "구분") -> dict:
    s = d["광고비 (KRW)"].sum()
    i = d["노출"].sum()
    c = d["클릭"].sum()
    v = d["전환수"].sum()
    return {
        key_col:     label,
        "광고비":    f"₩{int(s):,}",
        "노출":      f"{int(i):,}",
        "링크 클릭": f"{int(c):,}",
        "구매":      f"{int(v):,}",
        "CTR":      f"{c/i*100:.2f}%" if i > 0 else "0.00%",
        "CPC":      f"{int(s/c):,}" if c > 0 else "0",
        "CVR":      f"{v/c*100:.2f}%" if c > 0 else "0.00%",
        "CPA":      f"{int(s/v):,}" if v > 0 else "0",
    }
def daily_table(d: pd.DataFrame) -> pd.DataFrame:
    grp = (
        d.groupby(d["날짜"].dt.date)
        .agg(spend=("광고비 (KRW)", "sum"), imp=("노출", "sum"),
             clk=("클릭", "sum"), conv=("전환수", "sum"))
        .reset_index().rename(columns={"날짜": "date"})
        .sort_values("date")
    )
    grp["CTR"] = (grp["clk"] / grp["imp"].replace(0, float("nan")) * 100).fillna(0)
    grp["CPC"] = (grp["spend"] / grp["clk"].replace(0, float("nan"))).fillna(0)
    grp["CVR"] = (grp["conv"] / grp["clk"].replace(0, float("nan")) * 100).fillna(0)
    grp["CPA"] = (grp["spend"] / grp["conv"].replace(0, float("nan"))).fillna(0)
    tbl = pd.DataFrame({
        "일":        grp["date"].astype(str),
        "광고비":    grp["spend"].apply(lambda x: f"₩{int(x):,}"),
        "노출":      grp["imp"].apply(lambda x: f"{int(x):,}"),
        "링크 클릭": grp["clk"].apply(lambda x: f"{int(x):,}"),
        "구매":      grp["conv"].apply(lambda x: f"{int(x):,}"),
        "CTR":      grp["CTR"].apply(lambda x: f"{x:.2f}%"),
        "CPC":      grp["CPC"].apply(lambda x: f"{int(x):,}"),
        "CVR":      grp["CVR"].apply(lambda x: f"{x:.2f}%"),
        "CPA":      grp["CPA"].apply(lambda x: f"{int(x):,}"),
    })
    ts, ti, tc, tv = grp["spend"].sum(), grp["imp"].sum(), grp["clk"].sum(), grp["conv"].sum()
    total = pd.DataFrame([{
        "일":        "총합계",
        "광고비":    f"₩{int(ts):,}",
        "노출":      f"{int(ti):,}",
        "링크 클릭": f"{int(tc):,}",
        "구매":      f"{int(tv):,}",
        "CTR":      f"{tc/ti*100:.2f}%" if ti > 0 else "0.00%",
        "CPC":      f"{int(ts/tc):,}" if tc > 0 else "0",
        "CVR":      f"{tv/tc*100:.2f}%" if tc > 0 else "0.00%",
        "CPA":      f"{int(ts/tv):,}" if tv > 0 else "0",
    }])
    out = pd.concat([tbl, total], ignore_index=True)
    order = ["일", "광고비", "CPA", "CPC", "CVR"]
    return out[order]
def daily_table_aw(d: pd.DataFrame) -> pd.DataFrame:
    """인지광고 일별 표: 일 · 광고비 · ThruPlay · 결과당비용"""
    grp = (
        d.groupby(d["날짜"].dt.date)
        .agg(spend=("광고비 (KRW)", "sum"), thru=("ThruPlay", "sum"))
        .reset_index().rename(columns={"날짜": "date"})
        .sort_values("date")
    )
    grp["cpr"] = (grp["spend"] / grp["thru"].replace(0, float("nan"))).fillna(0)
    tbl = pd.DataFrame({
        "일":        grp["date"].astype(str),
        "광고비":    grp["spend"].apply(lambda x: f"₩{int(x):,}"),
        "ThruPlay":  grp["thru"].apply(lambda x: f"{int(x):,}"),
        "결과당비용": grp["cpr"].apply(lambda x: f"₩{int(x):,}"),
    })
    ts, tt = grp["spend"].sum(), grp["thru"].sum()
    total = pd.DataFrame([{
        "일":        "총합계",
        "광고비":    f"₩{int(ts):,}",
        "ThruPlay":  f"{int(tt):,}",
        "결과당비용": f"₩{int(ts/tt):,}" if tt > 0 else "0",
    }])
    return pd.concat([tbl, total], ignore_index=True)[["일", "광고비", "ThruPlay", "결과당비용"]]
def valid_opts(df: pd.DataFrame, col: str) -> list:
    grp = df.groupby(col)["노출"].sum()
    return sorted([str(v) for v, imp in grp.items()
                   if str(v).strip() != "" and imp > 0])
_HANGUL_NAME = re.compile(r"^[가-힣]{2,4}$")
_NON_NAME = {"브랜드", "콘텐츠", "확장"}  # 한글이지만 사람 이름 아님(파싱 어긋남에서 온 카테고리 단어)
def person_name_opts(df: pd.DataFrame, col: str) -> list:
    # 담당자 드롭다운: 한글 2~4자 사람 이름만 남기고 날짜·영문·'- 사본'·'~팀'·카테고리 단어 등 노이즈 제거
    return [o for o in valid_opts(df, col)
            if _HANGUL_NAME.match(o) and not o.endswith("팀") and o not in _NON_NAME]
def week_label(ws) -> str:
    if ws == "총합계":
        return ws
    return f"{ws.strftime('%m/%d')}~{(ws + timedelta(days=6)).strftime('%m/%d')}"
def classify_creative(ad_name: str):
    for t in CREATIVE_TYPES:
        if t in str(ad_name):
            return t
    return None
def calc_kpi(d: pd.DataFrame) -> dict:
    spend = d["광고비 (KRW)"].sum()
    imp   = d["노출"].sum()
    clk   = d["클릭"].sum()
    conv  = d["전환수"].sum()
    thru  = d["ThruPlay"].sum() if "ThruPlay" in d.columns else 0
    return dict(spend=spend, imp=imp, clk=clk, conv=conv, thru=thru,
                ctr=clk / imp * 100 if imp > 0 else 0,
                cpa=spend / conv if conv > 0 else 0,
                cpr=spend / thru if thru > 0 else 0)   # 결과당비용 = ThruPlay당 비용
def fmt_krw(v: float) -> str:
    return f"₩{int(v):,}"
def fmt_num(v: float) -> str:
    return f"{int(v):,}"
def render_kpi(k: dict) -> None:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("💰 광고비", fmt_krw(k["spend"]))
    c2.metric("👁 노출",   fmt_num(k["imp"]))
    c3.metric("🖱 클릭",   fmt_num(k["clk"]))
    c4.metric("🛒 전환수", fmt_num(k["conv"]))
    c5.metric("📈 CTR",    f"{k['ctr']:.2f}%")
    c6.metric("🎯 CPA",    fmt_krw(k["cpa"]))
def render_kpi_aw(k: dict) -> None:
    """인지광고 KPI: 광고비 · 노출 · ThruPlay · 결과당비용(ThruPlay당 비용)"""
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 광고비",     fmt_krw(k["spend"]))
    c2.metric("👁 노출",       fmt_num(k["imp"]))
    c3.metric("▶ ThruPlay",   fmt_num(k["thru"]))
    c4.metric("💵 결과당비용", fmt_krw(k["cpr"]))
# =============================================================
# 데이터 업데이트 (GitHub Actions 트리거)
# =============================================================
GH_OWNER         = "sonyg-ops"
GH_REPO          = "lalasweet-icecream-dashboard"
REFRESH_WORKFLOW = "refresh.yml"

def _gh_headers():
    return {
        "Authorization": f"Bearer {st.secrets['github_token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def trigger_refresh(mode):
    """refresh.yml 워크플로우 실행 요청. 성공 시 (True, ''), 실패 시 (False, 사유)"""
    url = (f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}"
           f"/actions/workflows/{REFRESH_WORKFLOW}/dispatches")
    try:
        r = requests.post(url, headers=_gh_headers(),
                          json={"ref": "main", "inputs": {"mode": mode}}, timeout=30)
    except Exception as e:
        return False, f"요청 실패: {e}"
    if r.status_code == 204:
        return True, ""
    return False, f"HTTP {r.status_code}: {r.text[:300]}"

def latest_refresh_run():
    """refresh.yml의 가장 최근 실행 정보 (없으면 None)"""
    url = (f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}"
           f"/actions/workflows/{REFRESH_WORKFLOW}/runs")
    try:
        r = requests.get(url, headers=_gh_headers(),
                         params={"per_page": 1}, timeout=30)
        runs = r.json().get("workflow_runs", [])
        return runs[0] if runs else None
    except Exception:
        return None

@st.cache_data(ttl=300, show_spinner=False)
def last_collection_time():
    """가장 최근 수집 성공 시각 (정기 수집·버튼 업데이트·백필 포함, 5분 캐시)"""
    if "github_token" not in st.secrets:
        return None
    latest = None
    for wf in ["daily_report.yml", "refresh.yml", "backfill.yml"]:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}"
                f"/actions/workflows/{wf}/runs",
                headers=_gh_headers(),
                params={"status": "success", "per_page": 1}, timeout=15)
            runs = r.json().get("workflow_runs", [])
            if runs:
                t = pd.to_datetime(runs[0]["updated_at"])
                if latest is None or t > latest:
                    latest = t
        except Exception:
            continue
    return latest

def _data_freshness_line():
    """'데이터 기준일 · 마지막 수집' 표시 문자열"""
    try:
        d = df["날짜"].max().date()
    except Exception:
        return ""
    today_kst = pd.Timestamp.now(tz="Asia/Seoul").date()
    diff = (today_kst - d).days
    if diff == 0:
        base = f"{d.month}/{d.day} (오늘)"
    elif diff == 1:
        base = f"{d.month}/{d.day} (어제)"
    else:
        base = str(d)
    line = f"🕐 데이터 기준일 **{base}**"
    t = last_collection_time()
    if t is not None:
        t_kst = t.tz_convert("Asia/Seoul")
        day = "오늘 " if t_kst.date() == today_kst else f"{t_kst.month}/{t_kst.day} "
        line += f" · 마지막 수집 **{day}{t_kst.strftime('%H:%M')}**"
    return line

def _start_refresh(mode, label):
    ok, err = trigger_refresh(mode)
    if ok:
        st.session_state["refresh_active"] = True
        st.session_state["refresh_started"] = time.time()
        st.session_state["refresh_label"] = label
        st.session_state.pop("refresh_msg", None)
    else:
        st.session_state["refresh_msg"] = f"❌ 실행 요청 실패: {err}"
    st.rerun()

def _render_refresh_status():
    """진행 상태 표시. 진행 중이면 fragment로 10초마다 자동 갱신되고,
    완료 감지 시 캐시를 지우고 전체 대시보드를 자동 반영한다."""
    if not st.session_state.get("refresh_active"):
        msg = st.session_state.get("refresh_msg")
        if msg:
            st.markdown(msg)
        line = _data_freshness_line()
        if line:
            st.caption(line)
        return
    started = st.session_state.get("refresh_started", time.time())
    label   = st.session_state.get("refresh_label", "")
    elapsed = int(time.time() - started)
    run = latest_refresh_run()
    run_is_current = False
    if run is not None:
        try:
            created = pd.to_datetime(run.get("created_at")).timestamp()
            run_is_current = created >= started - 60
        except Exception:
            run_is_current = False
    if run_is_current and run.get("status") == "completed":
        st.session_state["refresh_active"] = False
        if run.get("conclusion") == "success":
            st.cache_data.clear()
            done_at = pd.Timestamp.now(tz="Asia/Seoul").strftime("%H:%M")
            st.session_state["refresh_msg"] = f"✅ {label} 업데이트 완료 — 대시보드에 반영됨 ({done_at})"
        else:
            st.session_state["refresh_msg"] = (f"❌ {label} 업데이트 실패 — "
                                               f"[Actions 로그 확인]({run.get('html_url')})")
        st.rerun(scope="app")
    elif elapsed > 900:
        st.session_state["refresh_active"] = False
        st.session_state["refresh_msg"] = ("⏱ 15분이 지나도 완료되지 않아 자동 확인을 중단했어요. "
                                           "GitHub Actions에서 상태를 확인해주세요.")
        st.rerun(scope="app")
    else:
        st.markdown(f"⏳ **{label} 업데이트 진행 중** ({elapsed // 60}분 {elapsed % 60}초 경과) "
                    f"— 완료되면 자동 반영됩니다")

def render_update_buttons():
    if "github_token" not in st.secrets:
        st.caption("⚙️ 업데이트 버튼을 사용하려면 Streamlit secrets에 `github_token`을 추가해주세요.")
        return
    active = st.session_state.get("refresh_active", False)
    c1, c2, c3 = st.columns([1.0, 1.25, 5.5], gap="small", vertical_alignment="center")
    if c1.button("📥 전일자 업데이트", disabled=active,
                 help="어제 데이터를 다시 수집해 최신 수치로 교체합니다 (약 2~4분 소요)"):
        _start_refresh("yesterday", "전일자")
    if c2.button("⚡ 실시간 업데이트 (오늘)", disabled=active,
                 help="오늘 데이터를 수집합니다. 당일 수치는 잠정치이며 계속 변합니다 (약 2~4분 소요)"):
        _start_refresh("today", "실시간")
    with c3:
        if active:
            st.fragment(_render_refresh_status, run_every=10)()
        else:
            _render_refresh_status()
# =============================================================
# 데이터 로드
# =============================================================
@st.cache_data(ttl=3600, show_spinner="데이터 불러오는 중...")
def load_data() -> pd.DataFrame:
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(st.secrets["spreadsheet_id"]).worksheet("통합RD_원본")
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
    for col in ["광고비 (KRW)", "노출", "클릭", "전환수", "CTR (%)", "CPA (KRW)",
                "CPC (KRW)", "영상조회 3초+", "ThruPlay", "결과당비용"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df = df.dropna(subset=["날짜"])
    df["연"] = df["날짜"].dt.year.astype(str)
    df["월"] = df["날짜"].dt.month.astype(str).str.zfill(2)
    df["일"] = df["날짜"].dt.day.astype(str).str.zfill(2)
    if "제품코드" in df.columns:
        df["제품군"] = df["제품코드"].astype(str).str.strip().map(PRODUCT_GROUP).fillna("(기타)")
    # 광고목적: 없거나 빈 값(과거 데이터·틱톡 등)은 전환으로 간주
    if "광고목적" in df.columns:
        df["광고목적"] = (df["광고목적"].astype(str).str.strip()
                          .replace({"": "전환", "nan": "전환", "None": "전환", "<NA>": "전환"}))
    else:
        df["광고목적"] = "전환"
    for _c in ["ThruPlay", "결과당비용"]:
        if _c not in df.columns:
            df[_c] = 0
    return df.sort_values("날짜")


@st.cache_data(ttl=3600, show_spinner="카페24 데이터 불러오는 중...")
def load_cafe24_data():
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(st.secrets["spreadsheet_id"])

    # 요약 시트
    try:
        ws_s = ss.worksheet("카페24_팝콘_요약")
        df_s = pd.DataFrame(ws_s.get_all_records())
        if not df_s.empty:
            df_s["날짜"] = pd.to_datetime(df_s["날짜"], errors="coerce")
            df_s["팝콘_주문수"] = pd.to_numeric(df_s["팝콘_주문수"], errors="coerce").fillna(0)
            df_s["팝콘_실매출"] = pd.to_numeric(df_s["팝콘_실매출"], errors="coerce").fillna(0)
            df_s = df_s.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)
    except Exception:
        df_s = pd.DataFrame()

    # 옵션별 시트
    try:
        ws_o = ss.worksheet("카페24_팝콘_옵션별")
        df_o = pd.DataFrame(ws_o.get_all_records())
        if not df_o.empty:
            df_o["날짜"] = pd.to_datetime(df_o["날짜"], errors="coerce")
            # 개입 수 컬럼 숫자 변환
            qty_cols = [c for c in df_o.columns if re.match(r'^\d+개$', c)]
            for c in qty_cols:
                df_o[c] = pd.to_numeric(df_o[c], errors="coerce").fillna(0)
            df_o = df_o.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)
    except Exception:
        df_o = pd.DataFrame()

    return df_s, df_o


try:
    df = load_data()
except Exception as e:
    st.error(f"❌ 데이터를 불러오지 못했어요: `{e}`")
    st.info("👉 `.streamlit/secrets.toml` 설정을 확인해주세요.")
    st.stop()

if df.empty:
    st.warning("시트에 데이터가 없어요.")
    st.stop()

max_date = df["날짜"].max().strftime("%Y-%m-%d")
# =============================================================
# 사이드바
# =============================================================
with st.sidebar:
    st.markdown("## 🍦 라라스윗 빙과 전환광고")
    st.markdown("---")
    _cur_year  = date.today().year
    _cur_month = f"{date.today().month}월"
    st.markdown("**📅 연도**")
    year_opts = sorted(df["날짜"].dt.year.unique().tolist(), reverse=True)
    sel_years = st.multiselect("연도", year_opts,
                               default=[_cur_year] if _cur_year in year_opts else [],
                               placeholder="전체", label_visibility="collapsed")
    st.markdown("**📅 월**")
    avail_months = sorted(df["날짜"].dt.month.unique().tolist())
    month_labels = [f"{m}월" for m in avail_months]
    sel_months = st.multiselect("월", month_labels,
                                default=[_cur_month] if _cur_month in month_labels else [],
                                placeholder="전체", label_visibility="collapsed")
    st.markdown("**📅 주** (월~일)")
    _wk_all = (df["날짜"] - pd.to_timedelta(df["날짜"].dt.weekday, unit="D")).dt.normalize()
    avail_weeks = sorted(_wk_all.dt.date.unique().tolist(), reverse=True)
    def _wk_label(ws):
        return f"{ws.year} {ws.strftime('%m/%d')}~{(ws + timedelta(days=6)).strftime('%m/%d')}"
    sel_weeks = st.multiselect("주", avail_weeks, format_func=_wk_label,
                               placeholder="전체", label_visibility="collapsed")
    st.markdown("**📅 일** (최신순)")
    avail_dates = sorted(df["날짜"].dt.strftime("%Y-%m-%d").unique().tolist(), reverse=True)
    sel_dates = st.multiselect("일", avail_dates, placeholder="전체",
                               label_visibility="collapsed")
    # 위에서 고른 기간(연/월/일)으로 아래 포맷·연출·Meta 구조 드롭다운을 좁힌다
    df_scope = df
    if sel_years:
        df_scope = df_scope[df_scope["날짜"].dt.year.isin(sel_years)]
    if sel_months:
        _scope_mnums = [int(m.replace("월", "")) for m in sel_months]
        df_scope = df_scope[df_scope["날짜"].dt.month.isin(_scope_mnums)]
    if sel_weeks:
        _scope_wk = (df_scope["날짜"] - pd.to_timedelta(df_scope["날짜"].dt.weekday, unit="D")).dt.normalize().dt.date
        df_scope = df_scope[_scope_wk.isin(sel_weeks)]
    if sel_dates:
        df_scope = df_scope[df_scope["날짜"].dt.strftime("%Y-%m-%d").isin(sel_dates)]
    st.caption("↓ 포맷·연출·Meta 구조는 위에서 고른 기간·광고유형 기준으로 좁혀져요")
    st.markdown("**📺 매체**")
    sel_media = st.multiselect("매체", valid_opts(df, "매체"),
                               placeholder="전체", label_visibility="collapsed")
    st.markdown("**🎬 광고유형**")
    sel_adtype = st.multiselect("광고유형",
                                [o for o in valid_opts(df, "영상/이미지 구분") if o in ("I", "V")],
                                placeholder="전체", label_visibility="collapsed")
    # 광고유형 선택도 아래 포맷·연출·Meta 구조 드롭다운에 반영
    if sel_adtype:
        df_scope = df_scope[df_scope["영상/이미지 구분"].astype(str).isin(sel_adtype)]
    st.markdown("**🍧 제품군**")
    sel_prodgroup = st.multiselect("제품군", valid_opts(df, "제품군"),
                                   placeholder="전체", label_visibility="collapsed")
    st.markdown("**📦 제품코드**")
    _df_pg = df[df["제품군"].astype(str).isin(sel_prodgroup)] if sel_prodgroup else df
    prodcode_opts = valid_opts(_df_pg, "제품코드")
    if "f_prodcode" in st.session_state:
        st.session_state["f_prodcode"] = [x for x in st.session_state["f_prodcode"] if x in prodcode_opts]
    sel_prodcode = st.multiselect("제품코드", prodcode_opts, placeholder="전체",
                                  key="f_prodcode", label_visibility="collapsed")
    st.markdown("---")
    st.markdown("**🎨 포맷 · 연출**")
    st.markdown("**🧩 대분류 포맷**")
    format_opts = valid_opts(df_scope, "대분류 포맷")
    if "f_format" in st.session_state:
        st.session_state["f_format"] = [x for x in st.session_state["f_format"] if x in format_opts]
    sel_format = st.multiselect("대분류 포맷", format_opts, placeholder="전체",
                                key="f_format", label_visibility="collapsed")
    st.markdown("**🎭 소분류 연출**")
    deroul_opts = valid_opts(df_scope, "소분류 연출")
    if "f_deroul" in st.session_state:
        st.session_state["f_deroul"] = [x for x in st.session_state["f_deroul"] if x in deroul_opts]
    sel_deroul = st.multiselect("소분류 연출", deroul_opts, placeholder="전체",
                                key="f_deroul", label_visibility="collapsed")
    st.button("↩️ 포맷·연출 초기화", key="rst_fd",
              on_click=_reset_keys, args=(["f_format", "f_deroul"],),
              use_container_width=True)
    st.markdown("---")
    st.markdown("**🅜 Meta 구조**")
    st.caption("↓ 캠페인부터 고르면 아래 목록이 좁혀져요")
    # 캠페인
    st.markdown("**📢 캠페인**")
    camp_opts = valid_opts(df_scope, "캠페인명")
    if "f_campaign" in st.session_state:
        st.session_state["f_campaign"] = [x for x in st.session_state["f_campaign"] if x in camp_opts]
    sel_campaign = st.multiselect("캠페인", camp_opts, placeholder="전체",
                                  key="f_campaign", label_visibility="collapsed")
    # 광고세트 (선택 캠페인으로 좁힘)
    st.markdown("**🗂 광고세트**")
    _df_c = df_scope[df_scope["캠페인명"].astype(str).isin(sel_campaign)] if sel_campaign else df_scope
    adset_opts = valid_opts(_df_c, "광고그룹명")
    if "f_adset" in st.session_state:
        st.session_state["f_adset"] = [x for x in st.session_state["f_adset"] if x in adset_opts]
    sel_adset = st.multiselect("광고세트", adset_opts, placeholder="전체",
                               key="f_adset", label_visibility="collapsed")
    # 소재 (선택 캠페인+광고세트로 좁힘)
    st.markdown("**🖼 소재**")
    _df_ca = _df_c[_df_c["광고그룹명"].astype(str).isin(sel_adset)] if sel_adset else _df_c
    creative_opts = valid_opts(_df_ca, "소재명")
    if "f_creative" in st.session_state:
        st.session_state["f_creative"] = [x for x in st.session_state["f_creative"] if x in creative_opts]
    sel_creative = st.multiselect("소재", creative_opts, placeholder="전체",
                                  key="f_creative", label_visibility="collapsed")
    st.button("↩️ Meta 구조 초기화", key="rst_meta",
              on_click=_reset_keys, args=(["f_campaign", "f_adset", "f_creative"],),
              use_container_width=True)
    st.markdown("---")
    st.markdown("**👤 담당자**")
    st.markdown("**🧑‍💼 마케터**")
    sel_marketer = st.multiselect("마케터", person_name_opts(df, "마케터"),
                                  placeholder="전체 (이름 입력해 검색)",
                                  key="f_marketer", label_visibility="collapsed")
    st.markdown("**🎨 PD/디자이너**")
    sel_designer = st.multiselect("PD/디자이너", person_name_opts(df, "PD/디자이너"),
                                  placeholder="전체 (이름 입력해 검색)",
                                  key="f_designer", label_visibility="collapsed")
    st.button("↩️ 담당자 초기화", key="rst_person",
              on_click=_reset_keys, args=(["f_marketer", "f_designer"],),
              use_container_width=True)
    st.markdown("---")
    st.markdown("**💰 광고비 최소금액**")
    st.caption("분류별 성과 표에서 합계 광고비가 이 금액 미만인 항목을 숨깁니다 (0 = 전체 표시)")
    min_spend = st.number_input("광고비 최소금액", min_value=0, value=0, step=10000,
                                format="%d", label_visibility="collapsed")
    st.markdown("---")
    if st.button("🔄 데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"최근 업데이트: {max_date}")
# =============================================================
# 광고 목적 뷰 (전환광고 / 인지광고) — 지표 세트를 통째로 바꾼다
# =============================================================
_purpose_labels = {"전환": "🛒 전환광고 (자사몰 구매)", "인지": "📣 인지광고 (동영상 조회)"}
view_purpose = st.radio(
    "광고 목적", ["전환", "인지"], horizontal=True,
    format_func=lambda p: _purpose_labels[p],
    key="view_purpose", label_visibility="collapsed",
)
IS_AW = (view_purpose == "인지")
# =============================================================
# 필터 적용
# =============================================================
mask = df["광고목적"].astype(str) == view_purpose
if sel_years:
    mask &= df["날짜"].dt.year.isin(sel_years)
if sel_months:
    sel_month_nums = [int(m.replace("월", "")) for m in sel_months]
    mask &= df["날짜"].dt.month.isin(sel_month_nums)
if sel_weeks:
    _row_wk = (df["날짜"] - pd.to_timedelta(df["날짜"].dt.weekday, unit="D")).dt.normalize().dt.date
    mask &= _row_wk.isin(sel_weeks)
if sel_dates:
    mask &= df["날짜"].dt.strftime("%Y-%m-%d").isin(sel_dates)
if sel_media:
    mask &= df["매체"].astype(str).isin(sel_media)
if sel_adtype:
    mask &= df["영상/이미지 구분"].astype(str).isin(sel_adtype)
if sel_prodgroup:
    mask &= df["제품군"].astype(str).isin(sel_prodgroup)
if sel_prodcode:
    mask &= df["제품코드"].astype(str).isin(sel_prodcode)
if sel_campaign:
    mask &= df["캠페인명"].astype(str).isin(sel_campaign)
if sel_adset:
    mask &= df["광고그룹명"].astype(str).isin(sel_adset)
if sel_creative:
    mask &= df["소재명"].astype(str).isin(sel_creative)
if sel_format:
    mask &= df["대분류 포맷"].astype(str).isin(sel_format)
if sel_deroul:
    mask &= df["소분류 연출"].astype(str).isin(sel_deroul)
if sel_marketer:
    mask &= df["마케터"].astype(str).isin(sel_marketer)
if sel_designer:
    mask &= df["PD/디자이너"].astype(str).isin(sel_designer)
fdf = df[mask].copy()
# 월별 추이: 월·주·일(날짜성) 필터만 무시하고 나머지(매체·광고유형·제품군·제품코드 등)는 모두 적용
# → 월 하나만 골라도 12개월이 다 보이게 하되, 제품코드 등 다른 필터는 정상 반영
mask_month_trend = df["광고목적"].astype(str) == view_purpose
if sel_years:
    mask_month_trend &= df["날짜"].dt.year.isin(sel_years)
if sel_media:
    mask_month_trend &= df["매체"].astype(str).isin(sel_media)
if sel_adtype:
    mask_month_trend &= df["영상/이미지 구분"].astype(str).isin(sel_adtype)
if sel_prodgroup:
    mask_month_trend &= df["제품군"].astype(str).isin(sel_prodgroup)
if sel_prodcode:
    mask_month_trend &= df["제품코드"].astype(str).isin(sel_prodcode)
if sel_campaign:
    mask_month_trend &= df["캠페인명"].astype(str).isin(sel_campaign)
if sel_adset:
    mask_month_trend &= df["광고그룹명"].astype(str).isin(sel_adset)
if sel_creative:
    mask_month_trend &= df["소재명"].astype(str).isin(sel_creative)
if sel_format:
    mask_month_trend &= df["대분류 포맷"].astype(str).isin(sel_format)
if sel_deroul:
    mask_month_trend &= df["소분류 연출"].astype(str).isin(sel_deroul)
if sel_marketer:
    mask_month_trend &= df["마케터"].astype(str).isin(sel_marketer)
if sel_designer:
    mask_month_trend &= df["PD/디자이너"].astype(str).isin(sel_designer)
fdf_year_only = df[mask_month_trend].copy()
if fdf.empty:
    st.warning("필터 조건에 맞는 데이터가 없어요. 필터를 조정해주세요.")
    st.stop()
kpi = calc_kpi(fdf)
STYLE = style_awareness if IS_AW else style_summary   # 표 스타일: 목적별 컬럼 세트
render_kpi_view = render_kpi_aw if IS_AW else render_kpi
# =============================================================
# 탭
# =============================================================
render_update_buttons()
# 빙과 대시보드: 제과 전용 탭(단쉐·팝콘)은 제거하고 전체 요약만 사용.
# (빙과 제품별 탭은 백필로 데이터가 쌓여 실제 제품코드를 확인한 뒤 추가 예정)
tab1, tab2 = st.tabs(["📊 전체 요약", "🧩 분류별 성과"])
# --- TAB 1: 전체 요약 ---
with tab1:
    render_kpi_view(kpi)
    st.markdown("---")
    daily_prod = (
        fdf.groupby([fdf["날짜"].dt.date, "제품코드"])
        .agg(spend=("광고비 (KRW)", "sum"))
        .reset_index().rename(columns={"날짜": "date"})
    )
    daily_prod["spend_man"] = daily_prod["spend"] / 10000
    daily_cpa = (
        fdf.groupby(fdf["날짜"].dt.date)
        .agg(spend=("광고비 (KRW)", "sum"), imp=("노출", "sum"),
             clk=("클릭", "sum"), conv=("전환수", "sum"), thru=("ThruPlay", "sum"))
        .reset_index().rename(columns={"날짜": "date"})
    )
    daily_cpa["CPA"] = (daily_cpa["spend"] / daily_cpa["conv"].replace(0, float("nan"))).fillna(0)
    daily_cpa["CTR"] = (daily_cpa["clk"] / daily_cpa["imp"].replace(0, float("nan")) * 100).fillna(0)
    daily_cpa["CPC"] = (daily_cpa["spend"] / daily_cpa["clk"].replace(0, float("nan"))).fillna(0)
    daily_cpa["CVR"] = (daily_cpa["conv"] / daily_cpa["clk"].replace(0, float("nan")) * 100).fillna(0)
    daily_cpa["결과당비용"] = (daily_cpa["spend"] / daily_cpa["thru"].replace(0, float("nan"))).fillna(0)
    prod_codes_sorted = (
        daily_prod.groupby("제품코드")["spend"].sum()
        .sort_values(ascending=False).index.tolist()
    )
    _line_metric = "결과당비용" if IS_AW else "CPA"   # 인지는 결과당비용, 전환은 CPA
    hdr_col, btn_col = st.columns([6, 1])
    with hdr_col:
        st.markdown(f"**📊 일별 광고비 & {_line_metric}**")
    with btn_col:
        view_mode = st.radio("보기", ["테이블", "그래프"], horizontal=True,
                             label_visibility="collapsed", key="daily_view_mode")
    if view_mode == "그래프":
        fig = go.Figure()
        for i, pc in enumerate(prod_codes_sorted):
            d = daily_prod[daily_prod["제품코드"] == pc]
            fig.add_bar(x=d["date"], y=d["spend_man"], name=str(pc),
                        marker_color=BAR_PALETTE[i % len(BAR_PALETTE)], yaxis="y1",
                        hovertemplate=f"<b>{pc}</b><br>날짜: %{{x}}<br>광고비: %{{y:,.0f}}만원<extra></extra>")
        fig.add_scatter(x=daily_cpa["date"], y=daily_cpa[_line_metric],
                        name=_line_metric, mode="lines+markers",
                        line=dict(color="#9B8EC4", width=2.5), marker=dict(size=6), yaxis="y2",
                        hovertemplate="날짜: %{x}<br>" + _line_metric + ": %{y:,.0f}원<extra></extra>")
        fig.update_layout(
            barmode="stack",
            xaxis=dict(title=""),
            yaxis=dict(title="광고비", ticksuffix="만원", tickformat=",",
                       showgrid=True, gridcolor="#f0f0f0"),
            yaxis2=dict(title=f"{_line_metric} (원)", overlaying="y", side="right",
                        showgrid=False, tickformat=",", ticksuffix="원"),
            legend=dict(orientation="h", y=1.10, font=dict(size=11)),
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(t=50, b=40), height=400,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        render_pinned_total_table(daily_table_aw(fdf) if IS_AW else daily_table(fdf))
    col_a, col_b = st.columns(2)
    with col_a:
        by_adtype = fdf.groupby("영상/이미지 구분")["광고비 (KRW)"].sum().reset_index()
        fig2 = px.pie(by_adtype, names="영상/이미지 구분", values="광고비 (KRW)",
                      title="소재유형별 광고비 비중 (V/I)", color_discrete_sequence=PALETTE)
        fig2.update_layout(height=300, margin=dict(t=50, b=20),
                           paper_bgcolor="white", plot_bgcolor="white")
        st.plotly_chart(fig2, use_container_width=True)
    with col_b:
        by_media_pie = fdf.groupby("매체")["광고비 (KRW)"].sum().reset_index()
        fig3 = px.pie(by_media_pie, names="매체", values="광고비 (KRW)",
                      title="매체별 광고비 비중", color_discrete_sequence=PALETTE)
        fig3.update_layout(height=300, margin=dict(t=50, b=20),
                           paper_bgcolor="white", plot_bgcolor="white")
        st.plotly_chart(fig3, use_container_width=True)
    st.markdown("---")
    fdf_m = fdf_year_only.copy()
    fdf_m["월"] = fdf_m["날짜"].dt.month
    monthly_tbl = build_summary_table(fdf_m, "월", label_fn=lambda x: f"{int(x):02d}")
    st.markdown("**📅 월별 데이터 추이**")
    render_pinned_total_table(STYLE(monthly_tbl, "월"))
    fdf_w = fdf.copy()
    fdf_w["week_start"] = fdf_w["날짜"].dt.to_period("W").apply(lambda p: p.start_time.date())
    recent_weeks = sorted(fdf_w["week_start"].unique())[-4:]
    fdf_w4 = fdf_w[fdf_w["week_start"].isin(recent_weeks)]
    weekly_tbl = build_summary_table(fdf_w4, "week_start", label_fn=week_label)
    weekly_tbl = weekly_tbl.rename(columns={"week_start": "주차"})
    st.markdown("**📆 주차별 성과 (최근 4주)**")
    render_pinned_total_table(STYLE(weekly_tbl, "주차"))
# --- TAB 2: 분류별 성과 ---
with tab2:
    render_kpi_view(kpi)
    st.markdown("---")
    # --- 포맷·연출별 성과 (항상 표시. 좌측 필터를 고르면 그 범위로 좁혀짐) ---
    st.markdown("**🎨 포맷·연출별 성과**")
    st.caption("좌측 필터를 선택하면 그 범위로 좁혀집니다 (미선택 시 전체)")
    fdf_fd = fdf.copy()
    for _c in ["대분류 포맷", "소분류 연출"]:
        fdf_fd[_c] = (fdf_fd[_c].fillna("(미지정)").astype(str).str.strip()
                      .replace({"": "(미지정)", "nan": "(미지정)",
                                "None": "(미지정)", "<NA>": "(미지정)"}))
    fmt_tbl = filter_min_spend(
        sort_summary_by_spend(build_summary_table(fdf_fd, "대분류 포맷"), "대분류 포맷"), min_spend)
    st.markdown("**🧩 대분류 포맷별 성과**")
    render_table_paged(STYLE(fmt_tbl, "대분류 포맷"), "fmt")
    der_tbl = filter_min_spend(
        sort_summary_by_spend(build_summary_table(fdf_fd, "소분류 연출"), "소분류 연출"), min_spend)
    st.markdown("**🎭 소분류 연출별 성과**")
    render_table_paged(STYLE(der_tbl, "소분류 연출"), "der")
    # --- Meta 구조별 성과 (항상 표시) ---
    st.markdown("---")
    st.markdown("**🅜 Meta 구조별 성과**")
    st.caption("좌측 Meta 구조 필터를 선택하면 그 범위로 좁혀집니다 (미선택 시 전체)")
    # 광고세트별
    adset_tbl = filter_min_spend(
        sort_summary_by_spend(build_summary_table(fdf, "광고그룹명"), "광고그룹명"), min_spend)
    adset_tbl = adset_tbl.rename(columns={"광고그룹명": "광고세트"})
    st.markdown("**🗂 광고세트별 성과**")
    render_table_paged(STYLE(adset_tbl, "광고세트"), "adset")
    # 소재별 (소재명 클릭 → 인스타 광고페이지)
    creative_tbl = filter_min_spend(
        sort_summary_by_spend(build_summary_table(fdf, "소재명"), "소재명"), min_spend)
    creative_tbl = creative_tbl.rename(columns={"소재명": "소재"})
    # 소재명 → 인스타링크 매핑 (RD에 '인스타링크' 열이 있을 때만; 없으면 링크 없이 표시)
    link_map = {}
    if "인스타링크" in fdf.columns:
        _lk = fdf[["소재명", "인스타링크"]].copy()
        _lk["인스타링크"] = _lk["인스타링크"].astype(str).str.strip()
        _lk = _lk[_lk["인스타링크"] != ""]
        link_map = dict(zip(_lk["소재명"], _lk["인스타링크"]))
    st.markdown("**🖼 소재별 성과**")
    if link_map:
        st.caption("소재명을 클릭하면 인스타 광고페이지가 열려요 🔗")
    styled_creative = STYLE(creative_tbl, "소재")
    styled_creative["_link"] = styled_creative["소재"].map(link_map).fillna("")
    # 맨 끝에 '소재 링크' 열 추가 — 표를 복사해 노션에 붙여도 링크가 텍스트로 따라가게 함
    # (소재명에 걸린 하이퍼링크는 _link로 그대로 유지)
    styled_creative["소재 링크"] = styled_creative["_link"]
    render_table_paged(styled_creative, "creative", link_col="_link")
