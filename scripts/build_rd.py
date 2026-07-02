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
import os, glob, re
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
    "날짜", "매체", "캠페인명", "광고그룹명", "소재명",
    # 파일명 생성기 파싱 (17컬럼)
    "제작월", "채널구분", "영상/이미지 구분", "제품코드", "광고종류",
    "스킴명", "대분류 포맷", "소분류 연출",
    "배리에이션 여부", "지면 유형", "상세연출(소재구분)", "프로젝트",
    "파트 구분", "마케터", "집행시작일", "본부 구분", "PD/디자이너",
    # 성과 지표 (메타+틱톡 공통)
    "노출", "클릭", "CTR (%)", "광고비 (KRW)", "CPC (KRW)", "전환수", "CPA (KRW)",
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

    if not isinstance(ad_name, str) or not ad_name.startswith("["):
        return result

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

        spend  = float(row.get("spend", 0) or 0)
        clicks = int(float(row.get("clicks", 0) or 0))
        imps   = int(float(row.get("impressions", 0) or 0))
        convs  = int(float(row.get("conversions", 0) or 0))

        ctr = round(clicks / imps * 100, 4) if imps > 0 else 0
        cpc = round(spend / clicks)         if clicks > 0 else 0
        cpa = round(spend / convs)          if convs > 0 else 0

        rec = {
            "날짜":    str(row.get("date", "")),
            "매체":    media,
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
            "전환수":       convs,
            "CPA (KRW)":    cpa,
        })
        records.append(rec)
    return pd.DataFrame(records, columns=RD_COLUMNS) if records else pd.DataFrame(columns=RD_COLUMNS)

# ── 실행 ──────────────────────────────────────────────────────
meta_raw   = load_raw("meta_raw_*.csv",   "Meta")
tiktok_raw = load_raw("tiktok_raw_*.csv", "TikTok")

new_df = pd.concat(
    [to_rd_rows(meta_raw, "Meta"), to_rd_rows(tiktok_raw, "TikTok")],
    ignore_index=True,
)

if new_df.empty:
    print("빌드할 새 데이터 없음 -> 종료")
    exit(0)

# raw CSV 내 중복 제거 (여러 raw 파일이 같은 날짜를 중복 커버할 경우 대비)
# 키: 날짜+매체+광고그룹명+소재명 (같은 소재가 여러 광고 세트에 배정될 수 있으므로 광고그룹명 포함)
# keep="last": 파일명 정렬상 뒤에 오는(더 최근 수집된) 파일의 값을 우선
before = len(new_df)
new_df = new_df.drop_duplicates(subset=["날짜", "매체", "광고그룹명", "소재명"], keep="last")
if len(new_df) < before:
    print(f"raw CSV 내 중복 제거: {before - len(new_df)}행 제거")

# 기존 마스터 로드
if os.path.exists(MASTER_PATH):
    master = pd.read_csv(MASTER_PATH, encoding="utf-8-sig", dtype=str)
else:
    master = pd.DataFrame(columns=RD_COLUMNS)

# 백필 모드: 해당 날짜 범위 행 제거 (새 데이터에 없는 날짜도 범위 내면 비워짐)
if IS_BACKFILL:
    before_rows = len(master)
    if not master.empty:
        master = master[
            ~master["날짜"].astype(str).between(BACKFILL_SINCE, BACKFILL_UNTIL)
        ]
    removed = before_rows - len(master)
    print(f"[백필 모드] {BACKFILL_SINCE} ~ {BACKFILL_UNTIL} 기존 {removed}행 제거 → 새 데이터로 교체")

# 날짜별 교체: 새로 수집된 날짜는 기존 행을 지우고 최신본으로 교체
# (실시간 수집으로 들어간 당일 잠정치가 다음 수집 때 정확한 수치로 덮어써지도록)
if not master.empty:
    new_dates = set(new_df["날짜"].astype(str))
    before_rows = len(master)
    master = master[~master["날짜"].astype(str).isin(new_dates)]
    replaced = before_rows - len(master)
    if replaced > 0:
        print(f"날짜별 교체: 기존 {replaced}행 제거 후 최신 데이터로 교체")

result = pd.concat([master, new_df], ignore_index=True)
result = result.sort_values("날짜", kind="stable").reset_index(drop=True)
result.to_csv(MASTER_PATH, index=False, encoding="utf-8-sig")
print(f"통합 RD 완료: +{len(new_df)}행 반영 -> 총 {len(result)}행 ({MASTER_PATH})")
