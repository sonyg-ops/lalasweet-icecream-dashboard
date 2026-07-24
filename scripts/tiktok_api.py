# -*- coding: utf-8 -*-
"""
TikTok 광고 데이터 수집 (빙과본부 대시보드 전용, GitHub Actions 전용)
- 매일 실행 (주말 포함)
- 자격증명: 환경 변수 (GitHub Secrets)
- 수집 대상: 빙과 제품만 (제과는 제외)
- 광고목적(전환/인지): 광고주 ID 1개 안에서 캠페인명에 '인지'가 들어가면 인지, 아니면 전환
  → 메타(meta_api.py collect_account)처럼 각 행에 '광고목적' 태그 + thruplay 출력
  → 인지는 결과당비용(=광고비/결과) 표시용으로, TikTok 네이티브 'result'(결과)를 thruplay 자리에 담음
- 지출(spend) = 0인 행 제외
- 출력: data/tiktok_raw_{since}_{until}.csv
- 상태 파일: data/tiktok_last_success.txt
"""
import os, sys, time, json, datetime, csv
import requests

ACCESS_TOKEN    = os.environ.get("TIKTOK_ACCESS_TOKEN", "").strip()
ADVERTISER_ID   = os.environ.get("TIKTOK_ADVERTISER_ID", "").strip()
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_USER_ID   = os.environ.get("SLACK_USER_ID", "")

# TikTok 자격증명이 없으면 틱톡 수집을 건너뛴다 (Meta만으로도 대시보드 동작).
# 나중에 TIKTOK_ACCESS_TOKEN / TIKTOK_ADVERTISER_ID 시크릿을 추가하면 자동으로 수집 시작.
if not ACCESS_TOKEN or not ADVERTISER_ID:
    print("TikTok 자격증명(TIKTOK_ACCESS_TOKEN/ADVERTISER_ID) 없음 -> 틱톡 수집 건너뜀")
    sys.exit(0)

BASE     = "https://business-api.tiktok.com/open_api/v1.3"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 인지 광고 '결과' 지표: 사장님이 커스텀 설정한 '15초 이상 조회수'를 잡는 값.
# TikTok 네이티브 'result'(결과)는 캠페인 최적화 목표(=15초 조회)를 그대로 반영하므로
# 메타에서 optimization_goal로 결과를 잡는 것과 같은 방식이다.
# build_rd.py 가 인지 행에서 결과당비용 = 광고비 / thruplay 로 계산하므로 이 값을 thruplay 자리에 담는다.
# ★ 만약 첫 실전 수집에서 이 지표가 오류(code 40002 '지표 없음' 등)나면 "video_watched_6s"(6초 조회)로 교체.
AWARENESS_METRIC = "result"

def purpose_of(campaign_name):
    """캠페인명에 '인지'가 들어가면 인지, 아니면 전환 (광고주 ID 1개 안에서 구분)."""
    return "인지" if "인지" in (campaign_name or "") else "전환"

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
# GitHub Actions 러너는 UTC이므로 '오늘/어제'는 반드시 KST 기준으로 계산한다.
# (UTC로 계산하면 07:00 KST=전날 22:00 UTC 실행 시 하루 밀려 전전날을 가져온다.
#  유튜브(google_sheet_to_raw.py)와 동일한 방식으로 맞춤.)
_KST  = datetime.timezone(datetime.timedelta(hours=9))
today = datetime.datetime.now(_KST).date()

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
                AWARENESS_METRIC,  # 인지 광고 결과(=15초 조회) — 인지 행의 thruplay 자리에 사용
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
        "BA딸", "BA옥", "BA혼", "JD망", "JD멜", "MB바", "M우", "M팥",
    ]

    def _is_bingwa(m):
        cn = m.get("campaign_name", "")
        an = m.get("ad_name", "")
        # 전환·인지 모두 수집 (광고목적은 아래 출력 단계에서 캠페인명으로 태그)
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
def _to_int(v):
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0

out = []
for r in all_rows:
    m     = r.get("metrics", {})
    d     = r.get("dimensions", {})
    spend = float(m.get("spend", 0) or 0)
    if spend == 0:
        continue
    cn      = m.get("campaign_name", "")
    purpose = purpose_of(cn)
    # 인지: 결과(15초 조회)를 thruplay 자리에 담고 전환수는 비움(0) → build_rd가 결과당비용 계산
    # 전환: thruplay 0, 전환수=conversion
    if purpose == "인지":
        thruplay    = _to_int(m.get(AWARENESS_METRIC, 0))
        conversions = 0
    else:
        thruplay    = 0
        conversions = _to_int(m.get("conversion", 0))
    out.append({
        "date":          d.get("stat_time_day", "")[:10],
        "ad_id":         d.get("ad_id", ""),   # 소재 미리보기 링크 조회(fetch_tiktok_links.py)용
        "campaign_name": cn,
        "adset_name":    m.get("adgroup_name", ""),
        "ad_name":       m.get("ad_name", ""),
        "impressions":   _to_int(m.get("impressions", 0)),
        "clicks":        _to_int(m.get("clicks", 0)),
        "spend":         spend,
        "conversions":   conversions,
        "thruplay":      thruplay,
        "광고목적":      purpose,
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
        f.write("date,ad_id,campaign_name,adset_name,ad_name,impressions,clicks,spend,conversions,thruplay,광고목적\n")

if not IS_BACKFILL:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        f.write(str(until))
    log(f"마지막 성공 날짜 갱신: {until}")
else:
    log("[백필 모드] state 파일 갱신 생략")

log(f"완료: {len(out)}행 -> {out_path}")
