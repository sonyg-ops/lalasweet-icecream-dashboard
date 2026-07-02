# -*- coding: utf-8 -*-
"""
TikTok 광고 데이터 수집 (빙과본부 대시보드 전용, GitHub Actions 전용)
- 매일 실행 (주말 포함)
- 자격증명: 환경 변수 (GitHub Secrets)
- 수집 대상: 빙과 제품만 (제과는 제외). 단 '인지'(인지도) 캠페인은 전환 대시보드 특성상 계속 제외
- 지출(spend) = 0인 행 제외
- 출력: data/tiktok_raw_{since}_{until}.csv
- 상태 파일: data/tiktok_last_success.txt
"""
import os, sys, time, json, datetime, csv
import requests

ACCESS_TOKEN    = os.environ["TIKTOK_ACCESS_TOKEN"]
ADVERTISER_ID   = os.environ["TIKTOK_ADVERTISER_ID"]
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_USER_ID   = os.environ.get("SLACK_USER_ID", "")

BASE     = "https://business-api.tiktok.com/open_api/v1.3"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)

LOG_PATH   = os.path.join(DATA_DIR, "tiktok_run.log")
STATE_PATH = os.path.join(DATA_DIR, "tiktok_last_success.txt")

# ── 로그 ──────────────────────────────────────────────────────
def log(msg):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line  = f"[{stamp}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def send_slack(text):
    if not SLACK_BOT_TOKEN:
        return
    try:
        r = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"channel": SLACK_USER_ID, "text": text},
            timeout=30,
        ).json()
        if not r.get("ok"):
            log(f"슬랙 전송 실패: {r}")
    except Exception as e:
        log(f"슬랙 전송 예외: {e}")

def die(msg):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"!!! 실패: {msg}")
    send_slack(
        f":x: *틱톡 데이터 수집 실패*\n"
        f"• 시각: {stamp}\n"
        f"• 사유: {msg}"
    )
    sys.exit(1)

# ── TikTok API GET (재시도) ────────────────────────────────────
def tt_get(endpoint, params):
    url = f"{BASE}{endpoint}"
    for attempt in range(8):
        try:
            resp = requests.get(
                url,
                headers={"Access-Token": ACCESS_TOKEN},
                params=params,
                timeout=60,
            )
            data = resp.json()
        except Exception as e:
            log(f"   네트워크 오류: {e} -> 재시도")
            time.sleep(min(10 * (2 ** attempt), 300))
            continue
        code = data.get("code", 0)
        if code == 0:
            return data
        # 토큰 만료/권한 오류 → 즉시 중단
        if code in (40100, 40101, 40102, 40105):
            die(
                f"TikTok 인증 오류 (토큰 재발급 필요)\n"
                f"code={code}: {data.get('message')}\n"
                f"토큰 재발급: https://ads.tiktok.com/marketing_api/apps/"
            )
        log(f"   TikTok API 오류 code={code}: {data.get('message')} -> 재시도")
        time.sleep(min(10 * (2 ** attempt), 300))
    die("TikTok API 재시도 모두 소진")

# ── 날짜 범위 ──────────────────────────────────────────────────
today = datetime.date.today()

# 백필 모드: 환경변수 BACKFILL_SINCE / BACKFILL_UNTIL 우선
_backfill_since = os.environ.get("BACKFILL_SINCE", "").strip()
_backfill_until = os.environ.get("BACKFILL_UNTIL", "").strip()
IS_BACKFILL = bool(_backfill_since and _backfill_until)

if IS_BACKFILL:
    since = datetime.date.fromisoformat(_backfill_since)
    until = datetime.date.fromisoformat(_backfill_until)
    log(f"[백필 모드] 수집 범위: {since} ~ {until}")
else:
    until = today - datetime.timedelta(days=1)

    last_success = None
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                last_success = datetime.date.fromisoformat(f.read().strip())
        except Exception:
            pass

    since = (last_success + datetime.timedelta(days=1)) if last_success else until

    if since > until:
        log(f"받을 새 데이터 없음 (이미 {last_success}까지 수집 완료) -> 종료")
        sys.exit(0)

gap_days = (until - since).days + 1
if gap_days > 60:
    log(f"경고: 수집 범위 {gap_days}일. 장기 누락 가능성.")
log(f"수집 범위: {since} ~ {until} ({gap_days}일)")

# ── 날짜별 수집 ────────────────────────────────────────────────
# TikTok API 최대 조회 범위: 30일. 안전하게 날짜 단위로 순회.
all_rows = []
current  = since

while current <= until:
    date_str   = str(current)
    daily_rows = []
    page       = 1

    while True:
        params = {
            "advertiser_id": ADVERTISER_ID,
            "report_type":   "BASIC",
            "dimensions":    json.dumps(["ad_id", "stat_time_day"]),
            "metrics":       json.dumps([
                "campaign_name", "adgroup_name", "ad_name",
                "spend", "impressions", "clicks", "conversion",
            ]),
            "data_level": "AUCTION_AD",
            "start_date": date_str,
            "end_date":   date_str,
            "page":       page,
            "page_size":  100,
        }
        result = tt_get("/report/integrated/get/", params)
        data   = result.get("data", {})
        batch  = data.get("list", [])
        daily_rows.extend(batch)
        total  = data.get("page_info", {}).get("total_number", 0)
        if not batch or len(daily_rows) >= total:
            break
        page += 1

    # 빙과만 남기기: 제과 대시보드가 "제외"하던 빙과 목록을 그대로 "포함" 기준으로 사용.
    # (캠페인명 또는 소재명에 아래 키워드/코드가 하나라도 있으면 빙과)
    _BINGWA_KW = [
        "빙과", "파인트", "스틱바", "얼리썸머", "패밀리세일",
        "제로바", "듬뿍바", "멜론바", "모나카", "미니생초코",
        "복요파", "블요바", "젤라또", "쫀득바", "요거트바", "초코페스티벌",
        "딸기축제", "망요바",
    ]
    _BINGWA_CODES = [
        "BA망", "CO바", "P혼", "ZB귤", "ZB파", "스틱바", "제로바",
        "BA딸", "BA옥", "BA혼", "JD망", "MB바", "M우", "M팥",
    ]

    def _is_bingwa(m):
        cn = m.get("campaign_name", "")
        an = m.get("ad_name", "")
        # '인지'(인지도) 캠페인은 전환 대시보드 특성상 제외
        if "인지" in cn:
            return False
        if any(kw in cn for kw in _BINGWA_KW):
            return True
        if any(code in an for code in _BINGWA_CODES):
            return True
        # C혼(초코)은 빙과, 단 PC혼(팝콘)은 제과이므로 제외
        if "C혼" in an and "PC혼" not in an:
            return True
        return False

    filtered = [r for r in daily_rows if _is_bingwa(r.get("metrics", {}))]
    log(f"  {date_str}: 전체 {len(daily_rows)}행 -> 빙과 {len(filtered)}행")
    all_rows.extend(filtered)
    current += datetime.timedelta(days=1)

# ── spend = 0 행 제외 후 CSV 저장 ─────────────────────────────
out = []
for r in all_rows:
    m     = r.get("metrics", {})
    d     = r.get("dimensions", {})
    spend = float(m.get("spend", 0) or 0)
    if spend == 0:
        continue
    out.append({
        "date":          d.get("stat_time_day", "")[:10],
        "campaign_name": m.get("campaign_name", ""),
        "adset_name":    m.get("adgroup_name", ""),
        "ad_name":       m.get("ad_name", ""),
        "impressions":   int(float(m.get("impressions", 0) or 0)),
        "clicks":        int(float(m.get("clicks", 0) or 0)),
        "spend":         spend,
        "conversions":   int(float(m.get("conversion", 0) or 0)),
    })

log(f"spend > 0 행: {len(out)}개")

out_path = os.path.join(DATA_DIR, f"tiktok_raw_{since}_{until}.csv")
with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
    if out:
        writer = csv.DictWriter(f, fieldnames=out[0].keys())
        writer.writeheader()
        writer.writerows(out)
    else:
        # 지출이 없는 날에도 파일 생성 (빈 헤더)
        f.write("date,campaign_name,adset_name,ad_name,impressions,clicks,spend,conversions\n")

if not IS_BACKFILL:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        f.write(str(until))
    log(f"마지막 성공 날짜 갱신: {until}")
else:
    log("[백필 모드] state 파일 갱신 생략")

log(f"완료: {len(out)}행 -> {out_path}")
