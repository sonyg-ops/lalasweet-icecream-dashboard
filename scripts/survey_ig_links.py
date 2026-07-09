# -*- coding: utf-8 -*-
"""
[표본 조사용] 광고 소재 → 인스타/페북 광고페이지 링크 연결 가능성 점검 (GitHub Actions 전용)
- 최근 기간(SURVEY_SINCE~UNTIL) 계정 인사이트에서 광고비 발생 광고를 모아 소재명별 총 광고비 집계
- 빙과 소재 중 광고비 >= SURVEY_MIN_SPEND 인 소재만 대상 (대표 ad_id = 그 소재에서 최다 지출 광고)
- 대표 ad_id들의 creative 필드를 배치 조회해 링크 종류별 개수 집계
    · instagram_permalink_url  → 공개 인스타 게시물 링크 (원하는 것)
    · effective_object_story_id → 페북 게시물 링크 (facebook.com/{id})
    · 둘 다 없음               → 다크포스트(공개 링크 없음)
- 출력: data/ig_link_survey.csv + 로그에 요약
※ 조사 전용. 정기 수집 파이프라인과 무관.
"""
import os, sys, time, json, datetime, csv, re
from collections import defaultdict
import requests

TOKEN = os.environ["META_ACCESS_TOKEN"]
ACCT  = os.environ["META_AD_ACCOUNT_ID"]
API   = "v21.0"
BASE  = f"https://graph.facebook.com/{API}"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

today = datetime.date.today()
UNTIL = (os.environ.get("SURVEY_UNTIL") or "").strip() or str(today - datetime.timedelta(days=1))
SINCE = (os.environ.get("SURVEY_SINCE") or "").strip() or str(today - datetime.timedelta(days=90))
MIN_SPEND = float(os.environ.get("SURVEY_MIN_SPEND") or "1000000")
MAX_ADS   = int(os.environ.get("SURVEY_MAX_ADS") or "200")

def api(method, url, **kw):
    last = None
    for d in [0, 10, 30, 60, 120, 300]:
        if d:
            time.sleep(d)
        try:
            r = requests.request(method, url, timeout=120, **kw)
            data = r.json()
        except Exception as e:
            last = f"네트워크 오류: {e}"; print("  ", last); continue
        if "error" not in data:
            return data
        err = data["error"]; last = err; code = err.get("code")
        print(f"   API 오류(code {code}): {err.get('message')}")
        if code not in (1, 2, 4, 17, 341, 613):
            raise SystemExit(f"영구 API 오류: {err}")
    raise SystemExit(f"재시도 소진: {last}")

def clean_ad_name(name):
    return re.sub(r'\s*-\s*사본(\s+\d+)?$', '', name or '').strip()

# 빙과 판별 (meta_api.py 와 동일 기준)
BINGWA_CAMPAIGN_KW = ["빙과","파인트","스틱바","얼리썸머","패밀리세일","듬뿍바","딸기축제","망요바",
    "모나카","미니생초코","쫀득바","초코페스티벌","멜론바","젤라또","요거트바","복요파","블요바","제로바"]
BINGWA_AD_CODES = ["BA망","CO바","P혼","ZB귤","ZB파","제로바","BA딸","BA옥","BA혼","JD망","MB바","M우","M팥"]
BINGWA_PRODUCT_CODES = ["P혼","P망","P요","P복","P바","P초","P말","P오","P우","P치","P애","P고"]
def _pcode(a):
    if not a or not a.startswith("["):
        return ""
    p = a.split("_")
    return p[2] if len(p) > 2 else ""
def is_bingwa(camp, ad):
    c, a = camp or "", ad or ""
    if any(k in c for k in BINGWA_CAMPAIGN_KW): return True
    if any(x in a for x in BINGWA_AD_CODES):    return True
    if _pcode(a) in BINGWA_PRODUCT_CODES:       return True
    if "C혼" in a and "PC혼" not in a:          return True
    return False

print(f"[표본조사] 기간 {SINCE} ~ {UNTIL} / 소재 최소 광고비 {int(MIN_SPEND):,} / 최대 {MAX_ADS}개")

# 1) 인사이트에서 ad_id별 광고비 (기간 합산, time_increment 없음)
params = {
    "level": "ad",
    "fields": "ad_id,ad_name,campaign_name,spend",
    "time_range": json.dumps({"since": SINCE, "until": UNTIL}),
    "filtering": json.dumps([{"field": "impressions", "operator": "GREATER_THAN", "value": 0}]),
    "limit": 500,
    "access_token": TOKEN,
}
agg = {}   # ad_id -> [spend, ad_name(clean), campaign_name]
data = api("GET", f"{BASE}/{ACCT}/insights", params=params)
page = 0
while True:
    for r in data.get("data", []):
        aid = r.get("ad_id")
        if not aid:
            continue
        e = agg.setdefault(aid, [0.0, clean_ad_name(r.get("ad_name", "")), r.get("campaign_name", "")])
        e[0] += float(r.get("spend", 0) or 0)
    page += 1
    nxt = data.get("paging", {}).get("next")
    print(f"  인사이트 페이지 {page}: 누적 광고 {len(agg)}개")
    if not nxt:
        break
    data = api("GET", nxt)

# 2) 소재명별 총 광고비 + 대표 ad_id(최다 지출)
by_name = defaultdict(lambda: [0.0, None, 0.0])  # name -> [총광고비, 대표ad_id, 대표광고비]
for aid, (sp, name, camp) in agg.items():
    if not name or not is_bingwa(camp, name):
        continue
    e = by_name[name]
    e[0] += sp
    if sp > e[2]:
        e[1], e[2] = aid, sp
targets = [(name, tot, aid) for name, (tot, aid, _) in by_name.items() if tot >= MIN_SPEND and aid]
targets.sort(key=lambda x: -x[1])
targets = targets[:MAX_ADS]
print(f"빙과 소재 {len(by_name)}개 중 광고비 {int(MIN_SPEND):,}원 이상: {len(targets)}개 조사")

# 3) 대표 ad_id들의 creative 배치 조회 (50개씩)
cre = {}
ids = [t[2] for t in targets]
for i in range(0, len(ids), 50):
    chunk = ids[i:i+50]
    d = api("GET", f"{BASE}/", params={
        "ids": ",".join(chunk),
        "fields": "creative{effective_object_story_id,instagram_permalink_url,effective_instagram_media_id,object_type}",
        "access_token": TOKEN,
    })
    for aid, obj in d.items():
        cre[aid] = (obj.get("creative", {}) if isinstance(obj, dict) else {}) or {}

# 4) 집계 + CSV
rows = []
n_ig = n_fb = n_none = 0
for name, tot, aid in targets:
    c = cre.get(aid, {})
    ig    = c.get("instagram_permalink_url", "") or ""
    story = c.get("effective_object_story_id", "") or ""
    igm   = c.get("effective_instagram_media_id", "") or ""
    fb    = f"https://www.facebook.com/{story}" if story else ""
    if ig:            n_ig += 1
    elif story or igm: n_fb += 1
    else:             n_none += 1
    rows.append({
        "소재명": name, "광고비_기간합계": int(round(tot)), "ad_id": aid,
        "instagram_permalink": ig, "facebook_link": fb,
        "effective_instagram_media_id": igm, "object_type": c.get("object_type", ""),
    })

os.makedirs(DATA_DIR, exist_ok=True)
out = os.path.join(DATA_DIR, "ig_link_survey.csv")
with open(out, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["소재명","광고비_기간합계","ad_id","instagram_permalink","facebook_link","effective_instagram_media_id","object_type"])
    w.writeheader()
    w.writerows(rows)

tot_t = len(targets) or 1
print("\n===== 표본 조사 요약 =====")
print(f"대상 소재: {len(targets)}개")
print(f"① 공개 인스타 링크 있음 : {n_ig}개 ({n_ig*100//tot_t}%)")
print(f"② 페북 링크만 (IG 없음) : {n_fb}개 ({n_fb*100//tot_t}%)")
print(f"③ 링크 없음(다크포스트) : {n_none}개 ({n_none*100//tot_t}%)")
print(f"\n결과 파일: {out}")
for r in rows[:8]:
    print(f"  - {r['광고비_기간합계']:>12,} | IG:{'O' if r['instagram_permalink'] else 'X'} | {r['소재명'][:50]}")
