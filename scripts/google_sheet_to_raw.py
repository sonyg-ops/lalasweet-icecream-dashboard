# -*- coding: utf-8 -*-
"""
구글시트 'google_raw' 탭 → data/google_raw_current.csv

- Google Ads 스크립트(scripts/google_ads_script.js)가 구글시트 'google_raw' 탭에
  매일 기록한 영상(유튜브) 광고 성과를, 메타/틱톡 raw CSV와 동일한 형식으로 저장한다.
  → build_rd.py 가 이 파일을 읽어 매체="YouTube" 로 통합 RD에 합류시킨다.
- 날짜 범위: 환경변수 BACKFILL_SINCE/UNTIL 있으면 그 범위, 없으면 '어제'(KST) 하루.
  (메타/틱톡과 같은 범위만 내보내야 build_rd 날짜 교체에서 매체 간 간섭이 없다)
- 자격증명은 sync_to_sheets.py 와 동일 (SPREADSHEET_ID + GCP_SERVICE_ACCOUNT_JSON).
"""
import os, sys, csv, json, datetime
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GCP_SA_JSON    = os.environ["GCP_SERVICE_ACCOUNT_JSON"]
TAB_NAME       = os.environ.get("GOOGLE_RAW_TAB", "google_raw")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT_PATH = os.path.join(DATA_DIR, "google_raw_current.csv")

# 컬럼 (메타/틱톡 raw + 유튜브 소재링크)
COLUMNS = ["date", "campaign_name", "adset_name", "ad_name",
           "impressions", "clicks", "spend", "conversions", "thruplay", "광고목적", "소재링크"]

def write_header_only():
    with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(COLUMNS)

# ── 대상 날짜 범위 ─────────────────────────────────────────────
_since = os.environ.get("BACKFILL_SINCE", "").strip()
_until = os.environ.get("BACKFILL_UNTIL", "").strip()
if _since and _until:
    since, until = _since, _until
else:
    kst = datetime.timezone(datetime.timedelta(hours=9))
    y = datetime.datetime.now(kst).date() - datetime.timedelta(days=1)
    since = until = y.isoformat()
print(f"구글 raw 대상 범위: {since} ~ {until}")

# ── 시트 읽기 ─────────────────────────────────────────────────
creds = Credentials.from_service_account_info(
    json.loads(GCP_SA_JSON),
    scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"],
)
client = gspread.authorize(creds)

try:
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(TAB_NAME)
except gspread.WorksheetNotFound:
    print(f"'{TAB_NAME}' 탭 없음 -> 구글 데이터 없이 진행")
    write_header_only()
    sys.exit(0)

rows = sheet.get_all_values()
if not rows or len(rows) < 2:
    print("google_raw 탭에 데이터 없음 -> 헤더만 저장")
    write_header_only()
    sys.exit(0)

header = rows[0]
idx = {name: i for i, name in enumerate(header)}

# 시트 헤더 → build_rd 입력 컬럼 매핑.
# google_raw 탭은 통합RD_원본 형식(한글 33컬럼)으로 기록됨. 옛 raw 형식(영문)도 폴백 지원.
# build_rd 는 소재명을 다시 파싱하고 지표를 다시 계산하므로, 여기선 기본 컬럼만 넘기면 된다.
SRC = {
    "date":          ["날짜", "date"],
    "campaign_name": ["캠페인명", "campaign_name"],
    "adset_name":    ["광고그룹명", "adset_name"],
    "ad_name":       ["소재명", "ad_name"],
    "impressions":   ["노출", "impressions"],
    "clicks":        ["클릭", "clicks"],
    "spend":         ["광고비 (KRW)", "spend"],
    "conversions":   ["전환수", "conversions"],
    "thruplay":      ["ThruPlay", "thruplay"],
    "광고목적":       ["광고목적"],
    "소재링크":       ["소재링크", "인스타링크"],   # 유튜브 watch 링크(옛 시트는 '인스타링크' 열)
}
colpos = {c: next((idx[n] for n in cands if n in idx), None) for c, cands in SRC.items()}
missing = [c for c, p in colpos.items() if p is None]
if missing:
    print(f"경고: 시트에서 못 찾은 컬럼 {missing} -> 공란 처리")

def cell(r, out_col):
    i = colpos.get(out_col)
    return r[i] if (i is not None and i < len(r)) else ""

out = []
for r in rows[1:]:
    d = cell(r, "date").strip()
    if not d:
        continue
    if not (since <= d <= until):      # ISO(YYYY-MM-DD) 문자열 비교 = 날짜 비교
        continue
    out.append([cell(r, c) for c in COLUMNS])

with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(COLUMNS)
    w.writerows(out)

print(f"구글 raw 저장: {len(out)}행 ({since} ~ {until}) -> {OUT_PATH}")
