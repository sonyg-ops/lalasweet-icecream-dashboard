# -*- coding: utf-8 -*-
"""
소재 → 틱톡 공개(스파크) 링크 매핑 생성 (GitHub Actions 파이프라인용)
- tiktok_api.py 가 만든 data/tiktok_raw_*.csv 를 읽어 소재명별 광고비 합계 + 대표 ad_id 계산
  (raw 는 이미 빙과만·spend>0 상태이고 ad_id 포함)
- LINK_SINCE(기본 2026-07-01) 이후 집행 + 광고비 >= LINK_MIN_SPEND 인 소재만 대상
  (틱톡·유튜브 링크는 7/1부터만 연결하기로 함 → build_rd.py LINK_SINCE 와 동일)
- 대표 ad_id → /ad/get/ 로 tiktok_item_id(스파크 광고의 원본 게시물 ID) 조회
  → 공개 링크 https://www.tiktok.com/@{handle}/video/{item_id}
    (handle 은 /identity/get/ 로 best-effort 조회, 못 얻으면 임베드 링크 embed/v2/{item_id} 로 대체 — 둘 다 공개·영구)
  → item_id 가 없는(업로드형·비스파크) 소재는 공개 게시물이 없어 건너뜀
- 권한 필요한 /file/video/ad/info/ 를 쓰지 않으므로 추가 API 권한 불필요.
- 결과를 data/tiktok_links.csv (소재명, tiktok_link, ad_id, item_id) 로 저장.
- tiktok_api 뒤, build_rd 앞에서 실행. 자격증명 없으면 조용히 스킵(기존 링크 유지).
※ 첫 실행 로그의 '스파크 소재 N개'·'완료 N개 링크' 로 확인. 링크 없어도 대시보드는 정상 동작.
"""
import os, sys, glob, csv, json, time, datetime
import requests

DATA_DIR   = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT_PATH   = os.path.join(DATA_DIR, "tiktok_links.csv")
TOKEN      = os.environ.get("TIKTOK_ACCESS_TOKEN", "").strip()
ADV_ID     = os.environ.get("TIKTOK_ADVERTISER_ID", "").strip()
LINK_SINCE = os.environ.get("LINK_SINCE", "2026-07-01").strip()
MIN_SPEND  = float(os.environ.get("LINK_MIN_SPEND") or "100000")
BASE       = "https://business-api.tiktok.com/open_api/v1.3"

def log(m):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {m}")

if not TOKEN or not ADV_ID:
    log("TikTok 자격증명(TIKTOK_ACCESS_TOKEN/ADVERTISER_ID) 없음 -> 틱톡 링크 조회 스킵")
    sys.exit(0)

def tt_get(endpoint, params):
    """TikTok API GET (재시도). 영구 오류면 None 반환하고 중단(기존 링크 유지)."""
    url = f"{BASE}{endpoint}"
    last = None
    for attempt in range(5):
        try:
            data = requests.get(url, headers={"Access-Token": TOKEN},
                                params=params, timeout=60).json()
        except Exception as e:
            last = f"네트워크 오류: {e}"; log("  " + last)
            time.sleep(min(10 * (2 ** attempt), 120)); continue
        code = data.get("code", 0)
        if code == 0:
            return data
        last = f"code={code}: {data.get('message')}"
        log(f"  TikTok API 오류 {last}")
        # 인증·권한·파라미터 오류는 재시도 무의미 → 즉시 중단 (헛된 재시도로 시간 낭비 방지)
        if code in (40100, 40101, 40102, 40105, 40001, 40002):
            log("  권한/파라미터 오류 -> 이 조회 중단(기존 링크 유지)")
            return None
        time.sleep(min(10 * (2 ** attempt), 120))
    log(f"  재시도 소진: {last} -> 중단")
    return None

def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

# 1) raw 읽어서 (LINK_SINCE 이후) 소재명별 광고비 + 대표 ad_id
name_spend  = {}   # 소재명 -> 총 광고비
adid_spend  = {}   # ad_id  -> 광고비 합계
name_bestad = {}   # 소재명 -> (대표 ad_id, 그 ad_id 광고비)
raws = sorted(glob.glob(os.path.join(DATA_DIR, "tiktok_raw_*.csv")))
if not raws:
    log("tiktok_raw_*.csv 없음 -> 스킵")
    sys.exit(0)

rows = []
for path in raws:
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("date") or "").strip() < LINK_SINCE:   # 7/1 이후만
                continue
            name = (r.get("ad_name") or "").strip()
            aid  = (r.get("ad_id") or "").strip()
            if not name or not aid:
                continue
            try:
                sp = float(r.get("spend", 0) or 0)
            except ValueError:
                sp = 0.0
            rows.append((name, aid, sp))
            name_spend[name] = name_spend.get(name, 0.0) + sp
            adid_spend[aid]  = adid_spend.get(aid, 0.0) + sp

for name, aid, _ in rows:
    cur = name_bestad.get(name)
    if cur is None or adid_spend.get(aid, 0) > cur[1]:
        name_bestad[name] = (aid, adid_spend.get(aid, 0))

targets = sorted(
    [n for n, s in name_spend.items() if s >= MIN_SPEND and n in name_bestad],
    key=lambda n: -name_spend[n],
)
log(f"소재 {len(name_spend)}개 중 {LINK_SINCE}+ · 광고비 {int(MIN_SPEND):,}원 이상: {len(targets)}개")
if not targets:
    # ad_id 열이 없는 옛 raw 이거나 대상 없음 → 조용히 종료(기존 링크 유지)
    log("대상 소재 없음 -> 스킵")
    sys.exit(0)

# 2) 대표 ad_id → tiktok_item_id(스파크 원본 게시물 ID) + identity  (/ad/get/, 추가 권한 불필요)
id_to_name = {name_bestad[n][0]: n for n in targets}
ad_ids     = list(id_to_name.keys())
adid_item  = {}   # ad_id -> {"item_id":.., "identity_id":.., "identity_type":..}
for ch in chunks(ad_ids, 100):
    d = tt_get("/ad/get/", {
        "advertiser_id": ADV_ID,
        "filtering":     json.dumps({"ad_ids": ch}),
        "fields":        json.dumps(["ad_id", "ad_name", "tiktok_item_id",
                                     "identity_id", "identity_type"]),
        "page_size":     100,
    })
    if d is None:
        break
    for ad in d.get("data", {}).get("list", []):
        item = str(ad.get("tiktok_item_id") or "").strip()
        if item and item != "0":   # item_id 있으면 스파크(공개 게시물) 광고
            adid_item[str(ad.get("ad_id"))] = {
                "item_id":       item,
                "identity_id":   str(ad.get("identity_id") or "").strip(),
                "identity_type": str(ad.get("identity_type") or "").strip(),
            }
log(f"스파크(공개 게시물) 소재: {len(adid_item)}개 / 대상 {len(ad_ids)}개")

# 3) identity_id → @handle (best-effort). 못 얻으면 임베드 링크로 대체하므로 실패해도 무방.
handle_map = {}
seen = set()
for info in adid_item.values():
    iid, itype = info["identity_id"], info["identity_type"]
    if not iid or (itype, iid) in seen:
        continue
    seen.add((itype, iid))
    d = tt_get("/identity/get/", {"advertiser_id": ADV_ID,
                                  "identity_type": itype, "identity_id": iid})
    if d is None:
        continue
    data = d.get("data", {})
    idn_list = data.get("identity_list") or data.get("list") or ([data] if data else [])
    for idn in idn_list:
        for k in ("username", "handle", "unique_id", "nickname", "display_name"):
            v = str(idn.get(k) or "").strip()
            if v and " " not in v:   # @핸들은 공백이 없음
                handle_map[iid] = v.lstrip("@")
                break
        if iid in handle_map:
            break
log(f"핸들 확보: {len(handle_map)}개 identity (없으면 임베드 링크 사용)")

def build_link(info):
    item = info["item_id"]
    h = handle_map.get(info["identity_id"], "")
    if h:
        return f"https://www.tiktok.com/@{h}/video/{item}"       # 공개 게시물 페이지
    return f"https://www.tiktok.com/embed/v2/{item}"             # 핸들 미확보: 공개 임베드(항상 동작)

# 4) 소재명 → 링크 조립 (기존 캐시 유지 + 이번 대상 갱신)
links = {}   # 소재명 -> {tiktok_link, ad_id, item_id}
if os.path.exists(OUT_PATH):
    with open(OUT_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            n = (r.get("소재명") or "").strip()
            u = (r.get("tiktok_link") or "").strip()
            if n and u:
                links[n] = {"tiktok_link": u, "ad_id": (r.get("ad_id") or "").strip(),
                            "item_id": (r.get("item_id") or "").strip()}

fresh = 0
for aid, name in id_to_name.items():
    info = adid_item.get(aid)
    if not info:
        continue   # 스파크 아님(공개 게시물 없음) → 링크 없음
    links[name] = {"tiktok_link": build_link(info), "ad_id": aid, "item_id": info["item_id"]}
    fresh += 1
log(f"이번 실행 갱신: {fresh}개 소재 (비스파크는 제외)")

# 5) 저장
with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["소재명", "tiktok_link", "ad_id", "item_id"])
    for n in sorted(links):
        w.writerow([n, links[n]["tiktok_link"], links[n].get("ad_id", ""), links[n].get("item_id", "")])
log(f"완료: {len(links)}개 링크 -> {OUT_PATH}")
