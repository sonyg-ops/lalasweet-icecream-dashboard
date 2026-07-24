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
MASTER_PATH    = os.path.join(DATA_DIR, "통합RD_마스터.parquet")
MASTER_CSV_OLD = os.path.join(DATA_DIR, "통합RD_마스터.csv")  # 구 포맷 — 있으면 1회 승계 후 미사용

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
    # 소재 → 각 매체 광고페이지 링크 (행의 '매체' 기준으로 채움)
    #  · 메타 = 인스타 영구링크(ig_links.csv) / 틱톡 = 미리보기 링크(tiktok_links.csv, 임시)
    #  · 유튜브 = watch 영구링크(google_raw 유래, youtube_links.csv 누적)
    "소재링크",
]

# 틱톡·유튜브 링크는 이 날짜부터 집행된 소재만 연결한다 (메타는 전 기간).
LINK_SINCE = "2026-07-01"

PARSE_COLS = [
    "제작월", "채널구분", "영상/이미지 구분", "제품코드", "광고종류",
    "스킴명", "대분류 포맷", "소분류 연출",
    "배리에이션 여부", "지면 유형", "상세연출(소재구분)", "프로젝트",
    "파트 구분", "마케터", "집행시작일", "본부 구분", "PD/디자이너",
]

def _parse_spaced_name(ad_name: str, result: dict) -> dict:
    """구글애즈가 소재명의 특수문자([ ] _ .)를 공백으로 바꿔버린 형식 대응.
    예: '26 07F V JD멜 인지 ...'  ← 원본 '[26.07]F_V_JD멜_인지_...'
    앞쪽(제작월~소분류연출)은 위치가 고정이라 안전하게 복원하고,
    표준 17토큰 형태일 때만 나머지(배리에이션~PD)까지 채운다. 아니면 앞부분만."""
    t = ad_name.split()
    if len(t) < 5:
        return result
    m1 = re.match(r"^(\d{2})([A-Za-z]+)$", t[1])   # '07F' → 월 07, 채널 F
    if not (re.match(r"^\d{2}$", t[0]) and m1):
        return result                              # 이 패턴 아니면 공란 유지
    result["제작월"]          = f"[{t[0]}.{m1.group(1)}]"
    result["채널구분"]         = m1.group(2)
    result["영상/이미지 구분"]  = t[2]
    result["제품코드"]         = t[3]
    result["광고종류"]         = t[4]
    if len(t) > 5: result["스킴명"]      = t[5]
    if len(t) > 6: result["대분류 포맷"] = t[6]
    if len(t) > 7: result["소분류 연출"] = t[7]
    if len(t) == 17:   # 표준 전체 구조일 때만 중간·뒤까지 (아니면 앞부분만 안전하게)
        result["배리에이션 여부"]    = t[8]
        result["지면 유형"]         = t[9]
        result["상세연출(소재구분)"] = t[10]
        result["프로젝트"]          = t[11]
        result["파트 구분"]         = t[12]
        result["마케터"]            = t[13]
        result["집행시작일"]         = t[14]
        result["본부 구분"]         = t[15]
        result["PD/디자이너"]       = t[16]
    return result

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
        # 대괄호 없음 → 구글애즈가 특수문자를 공백으로 치환한 형식일 수 있음
        return _parse_spaced_name(ad_name, result)
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

def _norm_key(s) -> str:
    """소재명에서 구분자·공백([ ] _ . 및 공백)을 모두 제거한 매칭 키.
    구글애즈가 특수문자를 공백으로 바꿔도, 정상 이름과 글자만 같으면 동일 키가 된다."""
    return re.sub(r"[\[\]_.\s]", "", str(s))

def fill_youtube_from_clean(df: pd.DataFrame) -> int:
    """구글애즈(유튜브)가 소재명의 특수문자를 공백으로 바꿔 보고해
    _parse_spaced_name 만으로는 뒤쪽 분류 열이 비는 경우를 보정한다.
    3채널에 같은 소재를 동일 제목으로 집행하므로, 특수문자를 제거한 키로
    정상 이름(메타/틱톡/정상 유튜브)을 찾아 그 분류 17열을 그대로 가져온다.
    (소재명 셀 자체는 구글 보고값 그대로 두고, 분류 열만 채운다.)"""
    # 정상 이름('['로 시작) 행 → 정규화 키별 분류 17열 사전
    clean = {}
    for _, r in df.iterrows():
        name = str(r.get("소재명", ""))
        if name.startswith("["):
            k = _norm_key(name)
            if k and k not in clean:
                clean[k] = {c: r.get(c, "") for c in PARSE_COLS}
    fixed = 0
    for i, r in df.iterrows():
        if str(r.get("매체", "")) != "YouTube":
            continue
        name = str(r.get("소재명", ""))
        if name.startswith("["):          # 이미 정상형 → 손대지 않음
            continue
        tw = clean.get(_norm_key(name))
        if tw:
            for c in PARSE_COLS:
                df.at[i, c] = tw[c]
            fixed += 1
    return fixed

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
            # 유튜브 raw(google_sheet_to_raw)만 '소재링크'를 담아 옴 → 아래 매체별 조인에서 활용.
            # 메타·틱톡 raw엔 없으므로 공란(뒤에서 ig_links / tiktok_links 로 채움).
            # ※ 빈 셀은 pandas NaN → str(NaN or "")='nan' 오염을 막으려 pd.isna로 먼저 거른다.
            "소재링크":     ("" if pd.isna(row.get("소재링크", "")) else str(row.get("소재링크", "")).strip()),
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
    master = pd.read_parquet(MASTER_PATH)
elif os.path.exists(MASTER_CSV_OLD):        # 전환기: 구 CSV 마스터를 1회 승계
    master = pd.read_csv(MASTER_CSV_OLD, encoding="utf-8-sig", dtype=str)
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

# 유튜브 공백형 소재명 분류 복구: 전체(과거+신규)에 적용 → 다음 수집 때 과거 행도 자동 정리
n_fixed = fill_youtube_from_clean(result)
if n_fixed:
    print(f"유튜브 공백형 소재명 분류 복구: {n_fixed}행 (정상 이름 매칭)")

# ── 매체별 소재 링크 조인 (행의 '매체' 기준으로 각 플랫폼 링크를 채움) ──────────
#   메타 = 인스타 영구링크(전 기간) / 틱톡·유튜브 = LINK_SINCE 이후만
def _load_link_csv(path, url_field):
    m = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                n = (r.get("소재명") or "").strip()
                u = (r.get(url_field) or "").strip()
                if n and u and u.lower() != "nan":   # 'nan' 문자열 오염 방어
                    m[n] = u
    return m

if "소재링크" not in result.columns:
    result["소재링크"] = ""
result["소재링크"] = result["소재링크"].fillna("").astype(str).str.strip()
_name  = result["소재명"].astype(str).str.strip()
_media = result["매체"].astype(str)
_date  = result["날짜"].astype(str)

# 1) 메타 → 인스타 영구링크 (전 기간)
ig_map = _load_link_csv(os.path.join(DATA_DIR, "ig_links.csv"), "instagram_permalink")
m_meta = _media == "Meta"
result.loc[m_meta, "소재링크"] = _name[m_meta].map(ig_map).fillna("")
print(f"메타 인스타링크 매핑: {len(ig_map)}개 소재")

# 2) 유튜브 → watch 영구링크 (LINK_SINCE+). 이번 수집분(raw 유래) 링크를 youtube_links.csv에 누적 후 조인
YT_PATH = os.path.join(DATA_DIR, "youtube_links.csv")
yt_map = _load_link_csv(YT_PATH, "youtube_link")
_yt_link = new_df["소재링크"].fillna("").astype(str).str.strip()
new_yt = new_df[(new_df["매체"] == "YouTube") & (_yt_link != "") & (_yt_link.str.lower() != "nan")]
for _, rr in new_yt.iterrows():
    yt_map[str(rr["소재명"]).strip()] = str(rr["소재링크"]).strip()
with open(YT_PATH, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f); w.writerow(["소재명", "youtube_link"])
    for n in sorted(yt_map):
        w.writerow([n, yt_map[n]])
m_yt = (_media == "YouTube") & (_date >= LINK_SINCE)
result.loc[m_yt, "소재링크"] = _name[m_yt].map(yt_map).fillna("")
result.loc[(_media == "YouTube") & (_date < LINK_SINCE), "소재링크"] = ""
print(f"유튜브 watch링크 매핑: {len(yt_map)}개 소재 (누적)")

# 3) 틱톡 → 미리보기 링크 (LINK_SINCE+, 임시 URL)
tt_map = _load_link_csv(os.path.join(DATA_DIR, "tiktok_links.csv"), "tiktok_link")
m_tt = (_media == "TikTok") & (_date >= LINK_SINCE)
result.loc[m_tt, "소재링크"] = _name[m_tt].map(tt_map).fillna("")
result.loc[(_media == "TikTok") & (_date < LINK_SINCE), "소재링크"] = ""
print(f"틱톡 미리보기링크 매핑: {len(tt_map)}개 소재")

# 컬럼 누락 방지 + 순서 고정
for c in RD_COLUMNS:
    if c not in result.columns:
        result[c] = ""
result = result[RD_COLUMNS]

# 마스터는 Parquet로 저장(용량·읽기속도↑, git 증가 억제). 셀 문자열을 CSV와 동일하게 유지하기 위해
# 전 컬럼 문자열·빈칸 처리 후 기록 → sync/streamlit 의 읽기 동작이 기존 CSV와 동일.
result.fillna("").astype(str).to_parquet(MASTER_PATH, index=False)
print(f"통합 RD 완료: +{len(new_df)}행 반영 -> 총 {len(result)}행 ({MASTER_PATH})")
