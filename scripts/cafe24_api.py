# -*- coding: utf-8 -*-
"""
카페24 팝콘 주문 데이터 수집 (GitHub Actions 전용)

수집 대상: 상품 135, 161
수집 지표: 일별 팝콘_주문수, 팝콘_실매출, 옵션별(N개입) 주문수
출력 시트: 카페24_팝콘_요약 / 카페24_팝콘_옵션별

실행 예시:
  python scripts/cafe24_api.py                                   # 어제 데이터
  python scripts/cafe24_api.py --start 2026-06-01 --end 2026-06-21  # 백필
"""

import os, sys, re, json, time, base64, argparse, datetime
from collections import defaultdict
import requests

# ── 상수 ──────────────────────────────────────────────────────
MALL_ID     = "lalasweet17"
PRODUCT_NOS = {135, 161}
API_BASE    = f"https://{MALL_ID}.cafe24api.com/api/v2"
GH_REPO     = "youngeun-yun/lalasweet-ad-dashboard"

# 유효 주문 상태: 결제완료(F) ~ 구매확정(G), 교환 계열(E·J·K·L) 포함
# 제외: N(새주문/미결제), P(입금전), I/O(취소), D/H/R/U(반품·환불)
VALID_STATUSES = {"F", "M", "A", "B", "C", "G", "E", "J", "K", "L"}

SHEET_SUMMARY = "카페24_팝콘_요약"
SHEET_OPTIONS = "카페24_팝콘_옵션별"

# ── 전역 상태 (main에서 초기화) ────────────────────────────────
_access_token  = ""
_refresh_token = ""
_client_id     = ""
_client_secret = ""
_gh_pat        = ""
_slack_token   = ""
_slack_uid     = ""


# ── 유틸 ──────────────────────────────────────────────────────
def get_env(key, required=True):
    val = os.environ.get(key, "")
    if required and not val:
        die(f"환경 변수 누락: {key}")
    return val


def log(msg):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def send_slack(text):
    if not _slack_token:
        return
    try:
        requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {_slack_token}"},
            json={"channel": _slack_uid, "text": text},
            timeout=30,
        )
    except Exception:
        pass


def die(msg):
    log(f"!!! 실패: {msg}")
    send_slack(f":x: *카페24 데이터 수집 실패*\n• 사유: {msg}")
    sys.exit(1)


# ── 토큰 자동 갱신 + GitHub Secrets 업데이트 ──────────────────
def refresh_access_token():
    global _access_token, _refresh_token
    log("Access Token 만료 → 갱신 시도...")

    resp = requests.post(
        f"https://{MALL_ID}.cafe24api.com/api/v2/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": _refresh_token},
        auth=(_client_id, _client_secret),
        timeout=30,
    )
    if resp.status_code != 200:
        die(f"토큰 갱신 실패: {resp.status_code} {resp.text[:300]}")

    data = resp.json()
    new_access  = data.get("access_token")
    new_refresh = data.get("refresh_token", _refresh_token)

    if not new_access:
        die(f"토큰 응답에 access_token 없음: {data}")

    _access_token  = new_access
    _refresh_token = new_refresh

    log("토큰 갱신 완료 → GitHub Secrets 업데이트 중...")
    _put_github_secret("CAFE24_ACCESS_TOKEN",  new_access)
    _put_github_secret("CAFE24_REFRESH_TOKEN", new_refresh)
    log("GitHub Secrets 업데이트 완료")


def _gh_headers():
    return {
        "Authorization": f"Bearer {_gh_pat}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_pubkey():
    r = requests.get(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key",
        headers=_gh_headers(),
        timeout=30,
    )
    if r.status_code != 200:
        die(f"GitHub public key 조회 실패: {r.status_code} {r.text[:200]}")
    return r.json()


def _encrypt(pubkey_b64: str, value: str) -> str:
    """PyNaCl을 사용해 GitHub Secrets 암호화"""
    from nacl import encoding, public as nacl_public
    pk  = nacl_public.PublicKey(pubkey_b64.encode(), encoding.Base64Encoder())
    box = nacl_public.SealedBox(pk)
    return base64.b64encode(box.encrypt(value.encode("utf-8"))).decode("utf-8")


def _put_github_secret(name: str, value: str):
    ki = _get_pubkey()
    r = requests.put(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/{name}",
        headers=_gh_headers(),
        json={"encrypted_value": _encrypt(ki["key"], value), "key_id": ki["key_id"]},
        timeout=30,
    )
    if r.status_code not in (201, 204):
        die(f"GitHub Secret '{name}' 업데이트 실패: {r.status_code} {r.text[:200]}")


# ── 카페24 API 호출 ────────────────────────────────────────────
def cafe24_get(path, params=None, _retried=False):
    """GET 요청. 401(토큰 만료) 시 자동 갱신 후 1회 재시도."""
    headers = {
        "Authorization": f"Bearer {_access_token}",
        "Content-Type":  "application/json",
    }
    r = requests.get(f"{API_BASE}{path}", headers=headers, params=params, timeout=60)

    if r.status_code == 401 and not _retried:
        refresh_access_token()
        return cafe24_get(path, params, _retried=True)

    if r.status_code != 200:
        die(f"카페24 API 오류 {r.status_code}: {path}\n{r.text[:300]}")

    return r.json()


# ── 개입 수 추출 ───────────────────────────────────────────────
def extract_개입(option_value_str) -> int:
    """
    아이템의 option_value 문자열에서 'N개입' 패턴을 모두 합산하여 총 개입 수를 반환.
    예) '구성=저당 콘스프맛 팝콘 5개입, 구성=저당 카라멜 팝콘 5개입' → 10
        '추가 구성=저당 초코범벅 팝콘 10개입' 포함 시 → 20
    """
    if not option_value_str:
        return 0
    total = 0
    for m in re.findall(r"(\d+)개입", str(option_value_str)):
        total += int(m)
    return total


# ── 날짜별 주문 수집 ───────────────────────────────────────────
def collect_date(date_str: str):
    """
    해당 날짜의 유효 주문에서 상품 135·161 아이템만 추출.

    Returns:
        summary  : {product_no: {"주문수": int, "실매출": float}}
        opt_data : {product_no: {"name": str, "buckets": {개입수: 주문수}}}
    """
    summary  = defaultdict(lambda: {"주문수": 0, "실매출": 0.0})
    opt_data = defaultdict(lambda: {"name": "", "buckets": defaultdict(int)})

    offset = 0
    limit  = 500

    while True:
        data = cafe24_get("/admin/orders", params={
            "start_date": date_str,
            "end_date":   date_str,
            "embed":      "items",
            "limit":      limit,
            "offset":     offset,
        })

        orders = data.get("orders", [])
        if not orders:
            break

        for order in orders:
            # 손익봇과 동일: order 레벨 canceled 필드로 취소 주문 제외
            if order.get("canceled") == "T":
                continue

            for item in (order.get("items") or []):
                try:
                    pno = int(item.get("product_no", 0))
                except (TypeError, ValueError):
                    continue
                if pno not in PRODUCT_NOS:
                    continue

                qty = int(item.get("quantity", 1) or 1)

                # 실매출: 할인가 × 수량
                # (카페24 discounted_price = 회원등급·상품할인 적용가, 쿠폰 미포함 근사치)
                price   = float(item.get("discounted_price") or item.get("product_price") or 0)
                revenue = price * qty

                summary[pno]["주문수"]  += 1
                summary[pno]["실매출"] += revenue

                개입 = extract_개입(item.get("option_value"))
                if 개입 > 0:
                    opt_data[pno]["name"]           = item.get("product_name", "")
                    opt_data[pno]["buckets"][개입]   += 1

        if len(orders) < limit:
            break

        offset += limit
        time.sleep(0.2)  # API rate limit 준수

    return summary, opt_data


# ── Google Sheets 헬퍼 ─────────────────────────────────────────
def get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        json.loads(get_env("GCP_SERVICE_ACCOUNT_JSON")),
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(creds)


def get_or_create_sheet(spreadsheet, name, rows=5000, cols=50):
    import gspread
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=name, rows=rows, cols=cols)


# ── 시트 1: 요약 ───────────────────────────────────────────────
def update_summary_sheet(sheet, new_data: dict):
    """
    new_data: {date_str: {"주문수": int, "실매출": float}}
    기존 데이터와 날짜 기준 병합 후 오름차순 정렬하여 전체 덮어쓰기.
    """
    # 기존 데이터 로드
    data_map = {}
    for row in sheet.get_all_records():
        d = str(row.get("날짜", "")).strip()
        if d:
            data_map[d] = {
                "주문수":  int(row.get("팝콘_주문수",  0) or 0),
                "실매출": int(row.get("팝콘_실매출", 0) or 0),
            }

    # 신규 데이터 병합
    for date_str, vals in new_data.items():
        data_map[date_str] = {
            "주문수":  vals["주문수"],
            "실매출": round(vals["실매출"]),
        }

    rows = [["날짜", "팝콘_주문수", "팝콘_실매출"]]
    for d in sorted(data_map):
        v = data_map[d]
        rows.append([d, v["주문수"], v["실매출"]])

    sheet.clear()
    sheet.update(rows, value_input_option="USER_ENTERED")
    log(f"[{SHEET_SUMMARY}] {len(rows) - 1}행 기록 완료")


# ── 시트 2: 옵션별 ────────────────────────────────────────────
def update_options_sheet(sheet, new_data: dict):
    """
    new_data: {date_str: {product_no: {"name": str, "buckets": {개입수: count}}}}
    (날짜, 상품코드) 기준 병합, 개입수 컬럼 동적 생성, 합계 자동 산출.
    """
    # 기존 데이터 로드
    existing_rows = sheet.get_all_values()
    row_map = {}  # (date, str(pno)) → dict
    if len(existing_rows) > 1:
        headers = existing_rows[0]
        for row in existing_rows[1:]:
            rd = dict(zip(headers, row))
            key = (rd.get("날짜", ""), str(rd.get("상품코드", "")))
            row_map[key] = rd

    # 신규 데이터 병합
    for date_str, products in new_data.items():
        for pno, info in products.items():
            key = (date_str, str(pno))
            if key not in row_map:
                row_map[key] = {}
            row_map[key]["날짜"]    = date_str
            row_map[key]["상품코드"] = pno
            row_map[key]["상품명"]  = info["name"]
            for qty, cnt in info["buckets"].items():
                row_map[key][f"{qty}개"] = cnt

    if not row_map:
        log(f"[{SHEET_OPTIONS}] 기록할 데이터 없음")
        return

    # 동적 개입수 컬럼: 숫자 오름차순 정렬
    qty_cols = sorted(
        {k for rd in row_map.values() for k in rd if re.match(r"^\d+개$", k)},
        key=lambda x: int(re.search(r"\d+", x).group()),
    )

    all_cols = ["날짜", "상품코드", "상품명"] + qty_cols + ["합계"]

    rows = [all_cols]
    for key in sorted(row_map):
        rd       = row_map[key]
        qty_vals = [int(rd.get(c, 0) or 0) for c in qty_cols]
        rows.append(
            [rd.get("날짜", ""), rd.get("상품코드", ""), rd.get("상품명", "")]
            + qty_vals
            + [sum(qty_vals)]
        )

    sheet.clear()
    sheet.update(rows, value_input_option="USER_ENTERED")
    log(f"[{SHEET_OPTIONS}] {len(rows) - 1}행 기록 완료")


# ── 메인 ──────────────────────────────────────────────────────
def main():
    global _access_token, _refresh_token, _client_id, _client_secret
    global _gh_pat, _slack_token, _slack_uid

    parser = argparse.ArgumentParser(description="카페24 팝콘 주문 수집")
    parser.add_argument("--start", default=None, help="시작일 YYYY-MM-DD (기본: 어제)")
    parser.add_argument("--end",   default=None, help="종료일 YYYY-MM-DD (기본: 어제)")
    args = parser.parse_args()

    # 환경 변수 로드
    _client_id     = get_env("CAFE24_API_KEY")
    _client_secret = get_env("CAFE24_API_SECRET")
    _access_token  = get_env("CAFE24_ACCESS_TOKEN")
    _refresh_token = get_env("CAFE24_REFRESH_TOKEN")
    _gh_pat        = get_env("GH_PAT")
    _slack_token   = get_env("SLACK_BOT_TOKEN", required=False)
    _slack_uid     = get_env("SLACK_USER_ID",   required=False)

    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start = datetime.date.fromisoformat(args.start) if args.start else yesterday
    end   = datetime.date.fromisoformat(args.end)   if args.end   else yesterday

    if start > end:
        die(f"시작일({start})이 종료일({end})보다 늦습니다.")

    log(f"수집 범위: {start} ~ {end} ({(end - start).days + 1}일)")

    # 날짜별 수집
    all_summary = {}
    all_options = {}

    current = start
    while current <= end:
        ds = str(current)
        log(f"[{ds}] 수집 중...")

        summary, opts = collect_date(ds)

        # 요약: 두 상품 합산
        total_orders  = sum(v["주문수"]  for v in summary.values())
        total_revenue = sum(v["실매출"] for v in summary.values())
        all_summary[ds] = {"주문수": total_orders, "실매출": total_revenue}
        log(f"  → 주문수: {total_orders}건, 실매출: {total_revenue:,.0f}원")

        if opts:
            all_options[ds] = {
                pno: {"name": v["name"], "buckets": dict(v["buckets"])}
                for pno, v in opts.items()
            }

        current += datetime.timedelta(days=1)
        time.sleep(0.5)

    # Google Sheets 업데이트
    log("Google Sheets 업데이트 중...")
    gc = get_gspread_client()
    ss = gc.open_by_key(get_env("SPREADSHEET_ID"))

    update_summary_sheet(get_or_create_sheet(ss, SHEET_SUMMARY), all_summary)
    update_options_sheet(get_or_create_sheet(ss, SHEET_OPTIONS), all_options)

    log("전체 완료!")


if __name__ == "__main__":
    main()
