# -*- coding: utf-8 -*-
import os, sys, csv, json
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GCP_SA_JSON = os.environ["GCP_SERVICE_ACCOUNT_JSON"]
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CSV_PATH = os.path.join(DATA_DIR, "통합RD_마스터.csv")
SHEET_NAME = "통합RD_원본"

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

sheet.clear()
sheet.update(rows, value_input_option="USER_ENTERED")
print(f"동기화 완료: {len(rows)-1}행 -> {SHEET_NAME}")
