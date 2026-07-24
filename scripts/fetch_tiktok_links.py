# -*- coding: utf-8 -*-
"""
소재 → 틱톡 소재(미리보기) 링크 매핑 생성 (GitHub Actions 파이프라인용)
- tiktok_api.py 가 만든 data/tiktok_raw_*.csv 를 읽어 소재명별 광고비 합계 + 대표 ad_id 계산
  (raw 는 이미 빙과만·spend>0 상태이고 ad_id 포함)
- LINK_SINCE(기본 2026-07-01) 이후 집행 + 광고비 >= LINK_MIN_SPEND 인 소재만 대상
  (틱톡·유튜브 링크는 7/1부터만 연결하기로 함 → build_rd.py LINK_SINCE 와 동일)
- 대표 ad_id → /ad/get/ 로 video_id → /file/video/ad/info/ 로 preview_url(미리보기) 조회
- 결과를 data/tiktok_links.csv (소재명, tiktok_link, ad_id, video_id) 로 저장
  · 틱톡 preview_url 은 임시(서명·만료) 링크라 캐시하지 않고 대상 소재는 매 실행 새로 받는다
    (매일 돌아 활성 소재 링크가 갱신됨. 사장님: 기간제한 있는 링크여도 무방).
- tiktok_api 뒤, build_rd 앞에서 실행. 자격증명 없으면 조용히 스킵(기존 링크 유지).
※ 필드명(video_id / preview_url)은 실제 응답으로 1회 검증 필요 — 실패해도 대시보드는 그대로 동작.
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
        # 인증/권한 오류는 재시도 무의미 → 중단
        if code in (40100, 40101, 40102, 40105):
            log("  인증 오류 -> 틱톡 링크 조회 중단(기존 링크 유지)")
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

# 2) 대표 ad_id → video_id  (/ad/get/)
id_to_name = {name_bestad[n][0]: n for n in targets}
ad_ids     = list(id_to_name.keys())
adid_video = {}   # ad_id -> video_id
for ch in chunks(ad_ids, 100):
    d = tt_get("/ad/get/", {
        "advertiser_id": ADV_ID,
        "filtering":     json.dumps({"ad_ids": ch}),
        "fields":        json.dumps(["ad_id", "ad_name", "video_id"]),
        "page_size":     100,
    })
    if d is None:
        break
    for ad in d.get("data", {}).get("list", []):
        vid = (ad.get("video_id") or "").strip()
        if vid:
            adid_video[str(ad.get("ad_id"))] = vid

log(f"video_id 확보: {len(adid_video)}개 소재")

# 3) video_id → preview_url  (/file/video/ad/info/)
video_ids  = list({v for v in adid_video.values()})
video_prev = {}   # video_id -> preview_url
for ch in chunks(video_ids, 60):
    d = tt_get("/file/video/ad/info/", {
        "advertiser_id": ADV_ID,
        "video_ids":     json.dumps(ch),
    })
    if d is None:
        break
    for v in d.get("data", {}).get("list", []):
        url = (v.get("preview_url") or "").strip()
        if url:
            video_prev[str(v.get("video_id"))] = url

log(f"미리보기 링크 확보: {len(video_prev)}개")

# 4) 소재명 → 링크 조립. 기존 캐시(만료 가능)에 대상 소재는 새 값으로 덮어씀
links = {}   # 소재명 -> {tiktok_link, ad_id, video_id}
if os.path.exists(OUT_PATH):
    with open(OUT_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            n = (r.get("소재명") or "").strip()
            u = (r.get("tiktok_link") or "").strip()
            if n and u:
                links[n] = {"tiktok_link": u, "ad_id": (r.get("ad_id") or "").strip(),
                            "video_id": (r.get("video_id") or "").strip()}

fresh = 0
for aid, name in id_to_name.items():
    vid = adid_video.get(aid, "")
    url = video_prev.get(vid, "")
    if url:
        links[name] = {"tiktok_link": url, "ad_id": aid, "video_id": vid}
        fresh += 1
log(f"이번 실행 갱신: {fresh}개 소재")

# 5) 저장
with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["소재명", "tiktok_link", "ad_id", "video_id"])
    for n in sorted(links):
        w.writerow([n, links[n]["tiktok_link"], links[n].get("ad_id", ""), links[n].get("video_id", "")])
log(f"완료: {len(links)}개 링크 -> {OUT_PATH}")
