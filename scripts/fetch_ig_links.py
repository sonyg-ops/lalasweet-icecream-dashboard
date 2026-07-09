# -*- coding: utf-8 -*-
"""
소재 → 인스타 광고페이지 링크 매핑 생성 (GitHub Actions 파이프라인용)
- meta_api.py 가 만든 data/meta_raw_*.csv 를 읽어 소재명별 광고비 합계 계산
  (raw 는 이미 빙과만·소재명 정리 완료 상태이고 ad_id 포함)
- 광고비 >= LINK_MIN_SPEND 인 소재만 대상, 대표 ad_id(그 소재에서 최다 지출)의
  creative.instagram_permalink_url 을 배치 조회
- 결과를 data/ig_links.csv (소재명, instagram_permalink, ad_id) 로 저장
  · 이미 링크가 있는 소재는 재조회하지 않음(캐시)
- meta_api 뒤, build_rd 앞에서 실행. 별도 insights 질의를 하지 않으므로 요청 한도 부담 없음.
※ META_ACCESS_TOKEN 없으면 조용히 스킵(기존 링크 유지).
"""
import os, glob, csv, time, datetime
import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
IG_PATH  = os.path.join(DATA_DIR, "ig_links.csv")
TOKEN    = os.environ.get("META_ACCESS_TOKEN", "").strip()
API      = "v21.0"
BASE     = f"https://graph.facebook.com/{API}"
MIN_SPEND = float(os.environ.get("LINK_MIN_SPEND") or "1000000")

def log(m):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {m}")

if not TOKEN:
    log("META_ACCESS_TOKEN 없음 -> 링크 조회 스킵")
    raise SystemExit(0)

def api(method, url, **kw):
    last = None
    for d in [0, 10, 30, 60, 120]:
        if d:
            time.sleep(d)
        try:
            data = requests.request(method, url, timeout=120, **kw).json()
        except Exception as e:
            last = f"네트워크 오류: {e}"; log("  " + last); continue
        if "error" not in data:
            return data
        err = data["error"]; last = err; code = err.get("code")
        log(f"  API 오류(code {code}): {err.get('message')}")
        if code not in (1, 2, 4, 17, 341, 613):
            log("  영구 오류 -> 링크 조회 중단(기존 링크 유지)")
            return None
    log(f"  재시도 소진: {last} -> 중단")
    return None

# 1) raw 읽어서 소재명별 광고비 합계 + 대표 ad_id
name_spend = {}      # ad_name -> 총 광고비
name_bestad = {}     # ad_name -> (대표 ad_id, 그 ad_id 광고비)
adid_spend = {}      # ad_id -> 광고비 합계
raws = sorted(glob.glob(os.path.join(DATA_DIR, "meta_raw_*.csv")))
if not raws:
    log("meta_raw_*.csv 없음 -> 스킵")
    raise SystemExit(0)
for path in raws:
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            name = (r.get("ad_name") or "").strip()
            aid  = (r.get("ad_id") or "").strip()
            if not name:
                continue
            try:
                sp = float(r.get("spend", 0) or 0)
            except ValueError:
                sp = 0.0
            name_spend[name] = name_spend.get(name, 0.0) + sp
            if aid:
                adid_spend[aid] = adid_spend.get(aid, 0.0) + sp
# 대표 ad_id: 소재명별로 광고비 최다 ad_id (raw 재순회)
for path in raws:
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            name = (r.get("ad_name") or "").strip()
            aid  = (r.get("ad_id") or "").strip()
            if not name or not aid:
                continue
            cur = name_bestad.get(name)
            if cur is None or adid_spend.get(aid, 0) > cur[1]:
                name_bestad[name] = (aid, adid_spend.get(aid, 0))

targets = sorted(
    [n for n, s in name_spend.items() if s >= MIN_SPEND and n in name_bestad],
    key=lambda n: -name_spend[n],
)
log(f"소재 {len(name_spend)}개 중 광고비 {int(MIN_SPEND):,}원 이상: {len(targets)}개")

# 2) 기존 캐시 로드 (링크 있는 소재는 재조회 안 함)
links = {}   # 소재명 -> {"instagram_permalink":..., "ad_id":...}
if os.path.exists(IG_PATH):
    with open(IG_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            n = (r.get("소재명") or "").strip()
            u = (r.get("instagram_permalink") or "").strip()
            if n and u:
                links[n] = {"instagram_permalink": u, "ad_id": (r.get("ad_id") or "").strip()}
todo = [n for n in targets if n not in links]
log(f"기존 캐시 {len(links)}개 / 신규 조회 {len(todo)}개")

# 3) 신규 대상 대표 ad_id creative 배치 조회
def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

fetched = 0
id_to_name = {name_bestad[n][0]: n for n in todo}
ids = list(id_to_name.keys())
for ch in chunks(ids, 50):
    d = api("GET", f"{BASE}/", params={
        "ids": ",".join(ch),
        "fields": "creative{instagram_permalink_url,effective_object_story_id}",
        "access_token": TOKEN,
    })
    if d is None:
        break
    for aid, obj in d.items():
        name = id_to_name.get(aid)
        if not name:
            continue
        cre = (obj.get("creative", {}) if isinstance(obj, dict) else {}) or {}
        url = (cre.get("instagram_permalink_url") or "").strip()
        if url:
            links[name] = {"instagram_permalink": url, "ad_id": aid}
            fetched += 1

log(f"신규 링크 확보: {fetched}개")

# 4) 저장 (기존 + 신규 합본)
with open(IG_PATH, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["소재명", "instagram_permalink", "ad_id"])
    for n in sorted(links):
        w.writerow([n, links[n]["instagram_permalink"], links[n].get("ad_id", "")])
log(f"완료: {len(links)}개 링크 -> {IG_PATH}")
