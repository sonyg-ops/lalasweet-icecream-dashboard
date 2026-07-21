# -*- coding: utf-8 -*-
"""
메타 + 틱톡 raw CSV → 통합 RD 마스터 CSV
- data/meta_raw_*.csv + data/tiktok_raw_*.csv 읽기
- 소재명 파싱: 파일명 생성기 열 기준 (17컬럼)
- 날짜별 교체 방식: 새로 수집된 날짜는 마스터에서 해당 날짜 행을 제거 후 최신본으로 교체
  (실시간 수집한 당일 잠정치가 다음날 정기 수집 때 정확한 수치로 자동 갱신됨)
- 백필 모드(BACKFILL_SINCE/UNTIL 환경변수 존재 시):
    기존 마스터에서 해당 날짜 범위 행만 제거 후 새 데이터로 교체
    (다른 날짜 데이터는 보존)
"""
import os, glob, re, csv
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MASTER_PATH = os.path.join(DATA_DIR, "통합RD_마스터.csv")

# 백필 모드 감지 (meta_api.py / tiktok_api.py 와 동일한 방식)
BACKFILL_SINCE = os.environ.get("BACKFILL_SINCE", "").strip()
BACKFILL_UNTIL = os.environ.get("BACKFILL_UNTIL", "").strip()
IS_BACKFILL = bool(BACKFILL_SINCE and BACKFILL_UNTIL)

# 통합 RD 최종 컬럼 순서
RD_COLUMNS = [
    # 기본 정보
    "날짜", "매체", "광고목적", "캠페인명", "광고그룹명", "소재명",
    # 파일명 생성기 파싱 (17컬럼)
    "제작월", "채널구분", "영상/이미지 구분", "제품코드", "광고종류",
    "스킴명", "대분류 포맷", "소분류 연출",
    "배리에이션 여부", "지면 유형", "상세연출(소재구분)", "프로젝트",
    "파트 구분", "마케터", "집행시작일", "본부 구분", "PD/디자이너",
    # 성과 지표 — 전환광고: 전환수·CPA / 인지광고: ThruPlay·결과당비용
    "노출", "클릭", "CTR (%)", "광고비 (KRW)", "CPC (KRW)", "전환수", "CPA (KRW)",
    "ThruPlay", "결과당비용",
    # 소재 → 인스타 광고페이지 링크 (fetch_ig_links.py 가 채운 data/ig_links.csv 에서 조인)
    "인스타링크",
]

PARSE_COLS = [
    "제작월", "채널구분", "영상/이미지 구분", "제품코드", "광고종류",
    "스킴명", "대분류 포맷", "소분류 연출",
    "배리에이션 여부", "지면 유형", "상세연출(소재구분)", "프로젝트",
    "파트 구분", "마케터", "집행시작일", "본부 구분", "PD/디자이너",
]

def parse_ad_name(ad_name: str) -> dict:
    """
    파일명 생성기 수식 기준 파싱
    예시: [26.06]F_V_PC혼_전환_콘스프맛팝콘출시_신규BP_...
                                  ↑ parts[4] = 스킴명
    """
    result = {col: "" for col in PARSE_COLS}

    if not isinstance(ad_name, str):
        return result
    # 소재명 앞에 "(운영X) " 같은 접두어가 붙어도 파싱되도록 첫 '['부터 사용
    b = ad_name.find("[")
    if b == -1:
        return result
    ad_name = ad_name[b:]

    parts = ad_name.split("_")
    if len(parts) < 3:
        return result

    try:
        # parts[0] = "[26.06]F" → 제작월=[26.06], 채널구분=F
        m = re.match(r"(\[.+?\])(.*)", parts[0])
        if m:
            result["제작월"] = m.group(1)   # [26.06]
            result["채널구분"] = m.group(2)  # F

        if len(parts) > 1: result["영상/이미지 구분"] = parts[1]
        if len(parts) > 2: result["제품코드"] = parts[2]
        if len(parts) > 3: result["광고종류"] = parts[3]
        if len(parts) > 4: result["스킴명"] = parts[4]
        if len(parts) > 5: result["대분류 포맷"] = parts[5]
        if len(parts) > 6: result["소분류 연출"] = parts[6]

        if len(parts) > 7:
            kl = parts[7].split(".", 1)
            result["배리에이션 여부"] = kl[0]
            result["지면 유형"] = kl[1] if len(kl) > 1 else ""

        if len(parts) > 8:
            mn = parts[8].split(".", 1)
            result["상세연출(소재구분)"] = mn[0]
            result["프로젝트"] = mn[1] if len(mn) > 1 else ""

        if len(parts) > 9:  result["파트 구분"] = parts[9]
        if len(parts) > 10: result["마케터"] = parts[10]
        if len(parts) > 11: result["집행시작일"] = parts[11]
        if len(parts) > 12: result["본부 구분"] = parts[12]
        if len(parts) > 13: result["PD/디자이너"] = "_".join(parts[13:])

    except Exception:
        pass

    return result

def load_raw(pattern: str, media: str) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(DATA_DIR, pattern)))
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        df = pd.read_csv(f, encoding="utf-8-sig")
        if df.empty:
            continue
        df["_media"] = media
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def to_rd_rows(raw: pd.DataFrame, media: str) -> pd.DataFrame:
    records = []
    for _, row in raw.iterrows():
        ad_name = str(row.get("ad_name", ""))
        parsed = parse_ad_name(ad_name)

        # 광고목적: raw에 있으면 사용, 없으면(과거 데이터·틱톡 등) 전환으로 간주
        purpose = (str(row.get("광고목적", "")).strip() or "전환")
        # 구글애즈(유튜브)는 전부 인지광고 → 소재명 표기가 흔들려도 인지로 고정
        if media == "YouTube":
            purpose = "인지"

        spend    = float(row.get("spend", 0) or 0)
        clicks   = int(float(row.get("clicks", 0) or 0))
        imps     = int(float(row.get("impressions", 0) or 0))
        convs    = int(float(row.get("conversions", 0) or 0))
        thruplay = int(float(row.get("thruplay", 0) or 0))

        ctr = round(clicks / imps * 100, 4) if imps > 0 else 0
        cpc = round(spend / clicks)         if clicks > 0 else 0

        if purpose == "인지":
            # 인지광고: 자사몰 구매링크가 없어 전환/CPA는 의미 없음 → 비우고 ThruPlay·결과당비용으로 표시
            convs_out    = 0
            cpa_out      = ""
            result_cost  = round(spend / thruplay) if thruplay > 0 else 0
        else:
            # 전환광고: 기존 전환수·CPA 유지, 결과당비용은 비움
            convs_out    = convs
            cpa_out      = round(spend / convs) if convs > 0 else 0
            result_cost  = ""

        rec = {
            "날짜":    str(row.get("date", "")),
            "매체":    media,
            "광고목적": purpose,
            "캠페인명": row.get("campaign_name", ""),
            "광고그룹명": row.get("adset_name", ""),
            "소재명":  ad_name,
        }
        rec.update(parsed)
        rec.update({
            "노출":         imps,
            "클릭":         clicks,
            "CTR (%)":      ctr,
            "광고비 (KRW)": spend,
            "CPC (KRW)":    cpc,
            "전환수":       convs_out,
            "CPA (KRW)":    cpa_out,
            "ThruPlay":     thruplay,
            "결과당비용":   result_cost,
        })
        records.append(rec)
    return pd.DataFrame(records, columns=RD_COLUMNS) if records else pd.DataFrame(columns=RD_COLUMNS)

# ── 실행 ──────────────────────────────────────────────────────
meta_raw   = load_raw("meta_raw_*.csv",   "Meta")
tiktok_raw = load_raw("tiktok_raw_*.csv", "TikTok")
google_raw = load_raw("google_raw_*.csv", "YouTube")   # 구글애즈(유튜브 영상) — google_sheet_to_raw.py 산출

new_df = pd.concat(
    [to_rd_rows(meta_raw, "Meta"), to_rd_rows(tiktok_raw, "TikTok"), to_rd_rows(google_raw, "YouTube")],
    ignore_index=True,
)

if new_df.empty:
    print("빌드할 새 데이터 없음 -> 종료")
    exit(0)

# raw CSV 내 중복 제거 (여러 raw 파일이 같은 날짜를 중복 커버할 경우 대비)
# 키: 날짜+매체+광고목적+광고그룹명+소재명 (전환·인지 계정에 같은 소재명이 있어도 분리 유지)
# keep="last": 파일명 정렬상 뒤에 오는(더 최근 수집된) 파일의 값을 우선
before = len(new_df)
new_df = new_df.drop_duplicates(subset=["날짜", "매체", "광고목적", "광고그룹명", "소재명"], keep="last")
if len(new_df) < before:
    print(f"raw CSV 내 중복 제거: {before - len(new_df)}행 제거")

# 기존 마스터 로드
if os.path.exists(MASTER_PATH):
    master = pd.read_csv(MASTER_PATH, encoding="utf-8-sig", dtype=str)
else:
    master = pd.DataFrame(columns=RD_COLUMNS)

# 백필 모드: 해당 날짜 범위 행 제거 (이번에 수집한 매체만 — 다른 매체 데이터는 보존)
if IS_BACKFILL:
    before_rows = len(master)
    if not master.empty:
        media_in = set(new_df["매체"].astype(str))
        in_range = master["날짜"].astype(str).between(BACKFILL_SINCE, BACKFILL_UNTIL)
        in_media = master["매체"].astype(str).isin(media_in)
        master = master[~(in_range & in_media)]
    removed = before_rows - len(master)
    print(f"[백필 모드] {BACKFILL_SINCE} ~ {BACKFILL_UNTIL} (매체 {sorted(set(new_df['매체']))}) 기존 {removed}행 제거 → 새 데이터로 교체")

# 날짜·매체별 교체: 새로 수집된 (날짜+매체) 조합만 기존 행을 지우고 최신본으로 교체
# (실시간 수집으로 들어간 당일 잠정치가 다음 수집 때 정확한 수치로 덮어써지도록.
#  매체 단위로 교체하므로, 한 매체만 재수집해도 같은 날짜의 다른 매체는 그대로 보존됨)
if not master.empty:
    _sep = "\x1f"
    new_keys = set(new_df["날짜"].astype(str) + _sep + new_df["매체"].astype(str))
    before_rows = len(master)
    m_keys = master["날짜"].astype(str) + _sep + master["매체"].astype(str)
    master = master[~m_keys.isin(new_keys)]
    replaced = before_rows - len(master)
    if replaced > 0:
        print(f"날짜·매체별 교체: 기존 {replaced}행 제거 후 최신 데이터로 교체")

result = pd.concat([master, new_df], ignore_index=True)
result = result.sort_values("날짜", kind="stable").reset_index(drop=True)

# 소재명 → 인스타 광고페이지 링크 조인 (data/ig_links.csv, 없거나 빈 값이면 공란)
IG_PATH = os.path.join(DATA_DIR, "ig_links.csv")
link_map = {}
if os.path.exists(IG_PATH):
    with open(IG_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            u = (r.get("instagram_permalink") or "").strip()
            if u:
                link_map[(r.get("소재명") or "").strip()] = u
    print(f"인스타링크 매핑 로드: {len(link_map)}개")
result["인스타링크"] = result["소재명"].astype(str).str.strip().map(link_map).fillna("")

# 컬럼 누락 방지 + 순서 고정
for c in RD_COLUMNS:
    if c not in result.columns:
        result[c] = ""
result = result[RD_COLUMNS]

result.to_csv(MASTER_PATH, index=False, encoding="utf-8-sig")
print(f"통합 RD 완료: +{len(new_df)}행 반영 -> 총 {len(result)}행 ({MASTER_PATH})")
