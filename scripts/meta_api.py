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
AD_ACCOUNT_ID   = os.environ["META_AD_ACCOUNT_ID"]              # 메인 계정 = 전환광고
SUB_AD_ACCOUNT_ID = os.environ.get("META_AD_ACCOUNT_ID_SUB", "").strip()  # 서브 계정 = 인지광고(없으면 건너뜀)
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_USER_ID   = os.environ.get("SLACK_USER_ID", "")

# 수집 대상 계정 목록: (광고계정 ID, 광고목적). 같은 토큰으로 두 계정 모두 접근 가능.
ACCOUNTS = [(AD_ACCOUNT_ID, "전환")]
if SUB_AD_ACCOUNT_ID:
    ACCOUNTS.append((SUB_AD_ACCOUNT_ID, "인지"))

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

# GitHub Actions 러너는 UTC이므로 '오늘/어제'는 반드시 KST 기준으로 계산한다.
# (UTC로 계산하면 07:00 KST=전날 22:00 UTC 실행 시 하루 밀려 전전날을 가져온다.
#  유튜브(google_sheet_to_raw.py)와 동일한 방식으로 맞춤.)
_KST  = datetime.timezone(datetime.timedelta(hours=9))
today = datetime.datetime.now(_KST).date()

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
    "ad_id,campaign_name,adset_id,adset_name,ad_name,impressions,spend,"
    "inline_link_clicks,video_thruplay_watched_actions,actions"
)

# 빙과 대시보드: 계정 전체를 조회한 뒤 아래(수집 후 처리)에서 빙과만 남긴다.
# (Meta filtering은 조건이 AND로만 묶여 "빙과 캠페인 OR 소재"를 한 번에 못 거르므로,
#  여기서는 노출>0만 걸고 파이썬 단계에서 빙과 포함 필터를 적용한다.)
filtering = [
    {"field": "impressions", "operator": "GREATER_THAN", "value": 0},
]

# ── 인사이트 리포트 실행 (청크 단위) ────────────────────────────
# 큰 날짜 범위(수백 일)를 한 번의 async 리포트로 요청하면 메타 요청 한도
# (error_code 4, "Application request limit reached")에 걸려 중간에 Job Failed 된다.
# → 범위를 CHUNK_DAYS 단위로 잘라 작은 리포트 여러 개로 나눠 수집하고,
#    리포트가 일시적 오류로 실패하면 잠시 쉬었다가 리포트를 재생성한다.
CHUNK_DAYS    = int(os.environ.get("META_CHUNK_DAYS", "30"))     # 청크당 최대 일수
CHUNK_GAP_SEC = int(os.environ.get("META_CHUNK_GAP_SEC", "15"))  # 청크 사이 대기(초)
REPORT_RETRY_DELAYS = [30, 60, 120, 300, 300]
REPORT_TRANSIENT = (1, 2, 4, 17, 341, 613)

class _ReportError(Exception):
    def __init__(self, status):
        self.status = status if isinstance(status, dict) else {"error_message": str(status)}
        super().__init__(str(self.status))

def _run_report_once(account_id, c_since, c_until):
    """비동기 인사이트 리포트 1회 실행 → 행 리스트 반환. 작업 실패 시 _ReportError."""
    params = {
        "level":       "ad",
        "fields":      fields,
        "time_range":  json.dumps({"since": str(c_since), "until": str(c_until)}),
        "time_increment": 1,
        "filtering":   json.dumps(filtering),
        "use_unified_attribution_setting": "true",
        "access_token": ACCESS_TOKEN,
    }
    run = api_call("POST", f"{BASE}/{account_id}/insights", data=params)
    report_id = run.get("report_run_id")
    if not report_id:
        raise _ReportError({"error_message": f"report_run_id 없음: {run}"})
    log(f"  리포트 작업 생성: {report_id} ({c_since} ~ {c_until})")

    while True:
        s  = api_call("GET", f"{BASE}/{report_id}", params={"access_token": ACCESS_TOKEN})
        st = s.get("async_status")
        log(f"    {s.get('async_percent_completion')}% / {st}")
        if st == "Job Completed":
            break
        if st in ("Job Failed", "Job Skipped"):
            raise _ReportError(s)
        time.sleep(5)

    chunk_rows = []
    url  = f"{BASE}/{report_id}/insights"
    qp   = {"limit": 500, "access_token": ACCESS_TOKEN}
    page = 0
    while url:
        resp  = api_call("GET", url, params=qp)
        batch = resp.get("data", [])
        chunk_rows.extend(batch)
        page += 1
        paging = resp.get("paging", {})
        url = paging.get("next")
        qp  = {}
        log(f"    페이지 {page}: +{len(batch)}행 (청크 누적 {len(chunk_rows)}행)")
    return chunk_rows

def run_report(account_id, c_since, c_until):
    """리포트 실행 + 일시적 실패(요청 한도 등) 시 재시도. 재시도 소진/영구오류면 die()."""
    for attempt in range(len(REPORT_RETRY_DELAYS) + 1):
        try:
            return _run_report_once(account_id, c_since, c_until)
        except _ReportError as e:
            ec = e.status.get("error_code")
            transient = ec in REPORT_TRANSIENT or bool(e.status.get("is_transient"))
            if not transient or attempt >= len(REPORT_RETRY_DELAYS):
                die(f"리포트 작업 실패({c_since}~{c_until}): {e.status}")
            d = REPORT_RETRY_DELAYS[attempt]
            log(f"  리포트 실패(code {ec}) -> {d}s 후 재시도 (시도 {attempt+1}/{len(REPORT_RETRY_DELAYS)})")
            time.sleep(d)

def _date_chunks(start, end, size_days):
    cur = start
    while cur <= end:
        c_end = min(cur + datetime.timedelta(days=size_days - 1), end)
        yield cur, c_end
        cur = c_end + datetime.timedelta(days=1)

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

def collect_account(account_id, purpose):
    """광고계정 하나를 수집해 out-dict 리스트를 반환. purpose=광고목적('전환'/'인지')."""
    log(f"===== 계정 {account_id} ({purpose}) 수집 시작 =====")
    chunks = list(_date_chunks(since, until, CHUNK_DAYS))
    log(f"수집 청크 {len(chunks)}개 (청크당 최대 {CHUNK_DAYS}일)")
    rows = []
    for i, (c_since, c_until) in enumerate(chunks, 1):
        log(f"[청크 {i}/{len(chunks)}] {c_since} ~ {c_until}")
        rows.extend(run_report(account_id, c_since, c_until))
        log(f"  누적 {len(rows)}행")
        if i < len(chunks) and CHUNK_GAP_SEC > 0:
            time.sleep(CHUNK_GAP_SEC)

    # 광고세트별 최적화 목표 조회 (결과 지표 판단용)
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

    acct_out  = []
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

        try:
            tp = int(float(thruplay)) if thruplay not in (None, "") else 0
        except (TypeError, ValueError):
            tp = 0

        raw_name = r.get("ad_name", "") or ""
        ad_name  = clean_ad_name(raw_name)
        if ad_name != raw_name:
            sbon_count += 1

        acct_out.append({
            "date":          r.get("date_start"),
            "ad_id":         r.get("ad_id", ""),
            "campaign_name": r.get("campaign_name"),
            "adset_name":    r.get("adset_name"),
            "ad_name":       ad_name,
            "impressions":   r.get("impressions", 0),
            "clicks":        r.get("inline_link_clicks", 0),
            "spend":         r.get("spend", 0),
            "conversions":   result,
            "thruplay":      tp,
            "광고목적":      purpose,
        })

    if sbon_count > 0:
        log(f"  소재명 ' - 사본' 정리: {sbon_count}건")
    log(f"===== 계정 {account_id} ({purpose}): {len(acct_out)}행 수집 =====")
    return acct_out

out = []
for _acct_id, _purpose in ACCOUNTS:
    out.extend(collect_account(_acct_id, _purpose))

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
    "BA딸", "BA옥", "BA혼", "JD망", "JD멜", "MB바", "M우", "M팥",
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
