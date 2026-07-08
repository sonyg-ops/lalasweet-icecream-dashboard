# -*- coding: utf-8 -*-
"""
Meta 광고 데이터 수집 (빙과본부 대시보드 전용, GitHub Actions 전용)
- 매일 실행 (주말 포함, 주말 체크 없음)
- 자격증명: 환경 변수 (GitHub Secrets)
- 수집 대상: 빙과 제품만 (파인트/바류/모나카/젤라또/생초코 등)
  → 계정 전체를 조회한 뒤, 빙과 캠페인·소재만 남기고 나머지(제과)는 제외
- 소재명 정리: ' - 사본' / ' - 사본 N' 패턴 제거
- 출력: data/meta_raw_{since}_{until}.csv
- 상태 파일: data/meta_last_success.txt
"""
import os, sys, time, json, datetime, csv, re
import requests

ACCESS_TOKEN    = os.environ["META_ACCESS_TOKEN"]
AD_ACCOUNT_ID   = os.environ["META_AD_ACCOUNT_ID"]
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_USER_ID   = os.environ.get("SLACK_USER_ID", "")

API_VERSION = "v21.0"
BASE        = f"https://graph.facebook.com/{API_VERSION}"
DATA_DIR    = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)

LOG_PATH   = os.path.join(DATA_DIR, "meta_run.log")
STATE_PATH = os.path.join(DATA_DIR, "meta_last_success.txt")

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
        f":x: *메타 데이터 수집 실패*\n"
        f"• 시각: {stamp}\n"
        f"• 사유: {msg}"
    )
    sys.exit(1)

RETRY_DELAYS = [10, 30, 60, 120, 300, 300, 300]

def api_call(method, url, **kw):
    last = None
    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            log(f"   재시도 대기 {delay}s... (시도 {attempt}/{len(RETRY_DELAYS)})")
            time.sleep(delay)
        try:
            resp = requests.request(method, url, timeout=180, **kw)
            data = resp.json()
        except Exception as e:
            last = f"네트워크/파싱 오류: {e}"
            log(f"   {last} -> 재시도")
            continue
        if "error" not in data:
            return data
        err  = data["error"]
        last = err
        code = err.get("code")
        transient = bool(err.get("is_transient")) or code in (1, 2, 4, 17, 341, 613, 80000, 80003, 80004)
        log(f"   API 오류(code {code}): {err.get('message')} -> {'재시도' if transient else '영구 오류, 중단'}")
        if not transient:
            die(f"영구 API 오류: {err}")
    die(f"재시도 모두 소진: {last}")

def clean_ad_name(name: str) -> str:
    if not name:
        return name
    return re.sub(r'\s*-\s*사본(\s+\d+)?$', '', name).strip()

# 백필 모드: 환경변수 BACKFILL_SINCE / BACKFILL_UNTIL 우선
_backfill_since = os.environ.get("BACKFILL_SINCE", "").strip()
_backfill_until = os.environ.get("BACKFILL_UNTIL", "").strip()
IS_BACKFILL = bool(_backfill_since and _backfill_until)

today = datetime.date.today()

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
    log(f"경고: 다운로드 범위가 {gap_days}일. 장기 미실행/연속 실패 가능성.")
log(f"수집 범위: {since} ~ {until} ({gap_days}일)")

fields = (
    "campaign_name,adset_id,adset_name,ad_name,impressions,spend,"
    "inline_link_clicks,video_thruplay_watched_actions,actions"
)

# 빙과 대시보드: 계정 전체를 조회한 뒤 아래(수집 후 처리)에서 빙과만 남긴다.
# (Meta filtering은 조건이 AND로만 묶여 "빙과 캠페인 OR 소재"를 한 번에 못 거르므로,
#  여기서는 노출>0만 걸고 파이썬 단계에서 빙과 포함 필터를 적용한다.)
filtering = [
    {"field": "impressions", "operator": "GREATER_THAN", "value": 0},
]

params = {
    "level":       "ad",
    "fields":      fields,
    "time_range":  json.dumps({"since": str(since), "until": str(until)}),
    "time_increment": 1,
    "filtering":   json.dumps(filtering),
    "use_unified_attribution_setting": "true",
    "access_token": ACCESS_TOKEN,
}

run = api_call("POST", f"{BASE}/{AD_ACCOUNT_ID}/insights", data=params)
report_id = run.get("report_run_id")
if not report_id:
    die(f"report_run_id 없음: {run}")
log(f"리포트 작업 생성: {report_id}")

while True:
    s  = api_call("GET", f"{BASE}/{report_id}", params={"access_token": ACCESS_TOKEN})
    st = s.get("async_status")
    log(f"  {s.get('async_percent_completion')}% / {st}")
    if st == "Job Completed":
        break
    if st in ("Job Failed", "Job Skipped"):
        die(f"리포트 작업 실패: {s}")
    time.sleep(5)

rows = []
url  = f"{BASE}/{report_id}/insights"
qp   = {"limit": 500, "access_token": ACCESS_TOKEN}
page = 0
while url:
    resp  = api_call("GET", url, params=qp)
    batch = resp.get("data", [])
    rows.extend(batch)
    page += 1
    paging = resp.get("paging", {})
    url = paging.get("next")
    qp  = {}
    log(f"  페이지 {page}: +{len(batch)}행 (누적 {len(rows)}행)")

adset_ids = sorted({r.get("adset_id") for r in rows if r.get("adset_id")})
opt_map   = {}
for i in range(0, len(adset_ids), 50):
    chunk = adset_ids[i:i+50]
    data  = api_call(
        "GET", f"{BASE}/",
        params={"ids": ",".join(chunk), "fields": "optimization_goal", "access_token": ACCESS_TOKEN},
    )
    for aid, obj in data.items():
        if isinstance(obj, dict):
            opt_map[aid] = obj.get("optimization_goal", "")
log(f"광고세트 목표 조회: {len(opt_map)}/{len(adset_ids)}개")

def action_val(actions, atype):
    for a in actions or []:
        if a.get("action_type") == atype:
            return a.get("value")
    return 0

PURCHASE_TYPES = [
    "offsite_conversion.fb_pixel_purchase", "omni_purchase",
    "purchase", "onsite_web_purchase",
]
def purchase_val(actions):
    d = {a.get("action_type"): a.get("value") for a in actions or []}
    for t in PURCHASE_TYPES:
        if t in d:
            return d[t]
    return 0

GOAL = {
    "THRUPLAY":            ("video_thruplay",    "ThruPlay"),
    "LINK_CLICKS":         ("link_click",        "링크 클릭"),
    "VIDEO_VIEWS":         ("video_view",        "동영상 3초 이상 재생"),
    "OFFSITE_CONVERSIONS": ("purchase",          "웹사이트 구매"),
    "LANDING_PAGE_VIEWS":  ("landing_page_view", "랜딩 페이지 조회"),
    "REACH":               (None,                "도달"),
    "IMPRESSIONS":         (None,                "노출"),
    "POST_ENGAGEMENT":     ("post_engagement",   "게시물 참여"),
}

out = []
sbon_count = 0
for r in rows:
    goal = opt_map.get(r.get("adset_id"), "")
    res_type_key, res_type_kr = GOAL.get(goal, (None, goal))
    thruplay = action_val(r.get("video_thruplay_watched_actions"), "video_view")

    if res_type_key == "video_thruplay":
        result = thruplay
    elif res_type_key == "purchase":
        result = purchase_val(r.get("actions"))
    elif res_type_key:
        result = action_val(r.get("actions"), res_type_key)
    else:
        pv = purchase_val(r.get("actions"))
        result = pv if (pv and float(pv) != 0) else ""
        if pv and float(pv) != 0:
            res_type_kr = "웹사이트 구매"

    try:
        if result in (None, "") or float(result) == 0:
            res_type_kr, result = "", 0
    except (TypeError, ValueError):
        res_type_kr, result = "", 0

    raw_name = r.get("ad_name", "") or ""
    ad_name  = clean_ad_name(raw_name)
    if ad_name != raw_name:
        sbon_count += 1
        log(f"  소재명 정리: '{raw_name}' -> '{ad_name}'")

    out.append({
        "date":          r.get("date_start"),
        "campaign_name": r.get("campaign_name"),
        "adset_name":    r.get("adset_name"),
        "ad_name":       ad_name,
        "impressions":   r.get("impressions", 0),
        "clicks":        r.get("inline_link_clicks", 0),
        "spend":         r.get("spend", 0),
        "conversions":   result,
    })

if sbon_count > 0:
    log(f"소재명 ' - 사본' 정리 완료: {sbon_count}건")

# ── 빙과만 남기기 ──────────────────────────────────────────────
# 제과 대시보드가 "제외"하던 항목이 곧 빙과이므로, 그 목록을 그대로 "포함" 기준으로 사용.
# (캠페인명 또는 소재명에 아래 키워드/코드가 하나라도 있으면 빙과로 간주)
BINGWA_CAMPAIGN_KW = [
    "빙과", "파인트", "스틱바", "얼리썸머", "패밀리세일",
    "듬뿍바", "딸기축제", "망요바", "모나카", "미니생초코", "쫀득바",
    "초코페스티벌", "멜론바", "젤라또", "요거트바", "복요파", "블요바", "제로바",
]
BINGWA_AD_CODES = [
    "BA망", "CO바", "P혼", "ZB귤", "ZB파", "제로바",
    "BA딸", "BA옥", "BA혼", "JD망", "MB바", "M우", "M팥",
]
# 파인트 제품코드: 캠페인명에 "파인트" 등 빙과 키워드가 없어도, 소재의 제품코드가
# 파인트면 빙과로 수집한다. (P말 등이 파인트 안 붙은 캠페인에서 돌아도 누락 안 되도록)
# 목록은 streamlit_app.py 의 PRODUCT_GROUPS["파인트"] 와 동일하게 유지.
BINGWA_PRODUCT_CODES = [
    "P혼", "P망", "P요", "P복", "P바", "P초", "P말", "P오", "P우", "P치", "P애", "P고",
]

def _product_code(ad_name: str) -> str:
    # 소재명 형식: [YY.MM]<채널>_<영상/이미지>_<제품코드>_...  → 3번째 토큰이 제품코드.
    # (substring이 아닌 토큰 단위로 확인 → "P바" 등이 문구 중간에 우연히 들어간
    #  제과 소재를 오수집하지 않음)
    if not ad_name or not ad_name.startswith("["):
        return ""
    parts = ad_name.split("_")
    return parts[2] if len(parts) > 2 else ""

def is_bingwa(campaign_name: str, ad_name: str) -> bool:
    c = campaign_name or ""
    a = ad_name or ""
    if any(kw in c for kw in BINGWA_CAMPAIGN_KW):
        return True
    if any(code in a for code in BINGWA_AD_CODES):
        return True
    # 파인트 제품코드면 캠페인명과 무관하게 빙과로 수집
    if _product_code(a) in BINGWA_PRODUCT_CODES:
        return True
    # C혼(초코...)은 빙과, 단 PC혼은 팝콘(제과)이므로 제외
    if "C혼" in a and "PC혼" not in a:
        return True
    return False

before_bingwa = len(out)
out = [r for r in out if is_bingwa(r.get("campaign_name"), r.get("ad_name"))]
log(f"빙과 필터: 전체 {before_bingwa}행 -> 빙과 {len(out)}행")

if len(out) == 0:
    die("수집된 빙과 행이 0개 -> 파일 생성 안 함")

out_path = os.path.join(DATA_DIR, f"meta_raw_{since}_{until}.csv")
with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=out[0].keys())
    writer.writeheader()
    writer.writerows(out)

if not IS_BACKFILL:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        f.write(str(until))
    log(f"마지막 성공 날짜 갱신: {until}")

log(f"완료: {len(out)}행 -> {out_path}")
