# -*- coding: utf-8 -*-
import os, sys, csv, json
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GCP_SA_JSON = os.environ["GCP_SERVICE_ACCOUNT_JSON"]
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CSV_PATH = os.path.join(DATA_DIR, "통합RD_마스터.csv")
SHEET_NAME = "통합RD_원본"
# 시트에는 이 날짜(포함) 이후 데이터만 올린다. 그 이전은 시트에서 제외.
# (다른 팀이 2026-04 이후 데이터만 수식으로 참조하므로 이전 데이터는 시트에 둘 필요 없음.
#  Streamlit 대시보드는 이 시트가 아니라 통합RD_마스터.csv 전체를 읽으므로 영향 없음.)
SHEET_SINCE = os.environ.get("SHEET_SINCE", "2026-04-01").strip()

if not os.path.exists(CSV_PATH):
    print("CSV 없음 -> 스킵")
    sys.exit(0)

creds_info = json.loads(GCP_SA_JSON)
creds = Credentials.from_service_account_info(creds_info, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SPREADSHEET_ID)
try:
    sheet = spreadsheet.worksheet(SHEET_NAME)
except gspread.WorksheetNotFound:
    sheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=10000, cols=30)

with open(CSV_PATH, encoding="utf-8-sig") as f:
    rows = list(csv.reader(f))

if not rows:
    print("데이터 없음 -> 스킵")
    sys.exit(0)

# 날짜(SHEET_SINCE) 이후 행만 남긴다. (날짜는 ISO YYYY-MM-DD → 문자열 비교로 날짜 비교 성립)
if SHEET_SINCE and len(rows) > 1:
    header = rows[0]
    try:
        date_idx = next(i for i, c in enumerate(header) if c.strip().lstrip("﻿") == "날짜")
    except StopIteration:
        date_idx = 0
    kept = [r for r in rows[1:] if len(r) > date_idx and r[date_idx] >= SHEET_SINCE]
    rows = [header] + kept
    print(f"시트 필터: {SHEET_SINCE} 이후 {len(kept)}행만 업로드 (전체 대비 축소)")

sheet.clear()
sheet.update(rows, value_input_option="USER_ENTERED")
print(f"동기화 완료: {len(rows)-1}행 -> {SHEET_NAME}")
