/**
 * 라라스윗 빙과 대시보드 — Google Ads 소재별 성과 수집 스크립트
 *
 * 실행 위치: Google Ads → 도구 → 대량 작업 → 스크립트
 *   (계정에 로그인된 상태로 실행되므로 Developer Token·OAuth 전부 불필요)
 * 하는 일: 영상(유튜브) 광고를 소재×날짜 단위로 집계해 구글시트에 기록. "매일" 예약 실행.
 *   기기(휴대전화/태블릿/TV)로 쪼개지 않고 소계·총계 없이 깔끔한 행만 남긴다.
 *
 * 방식: 스크립트의 GAQL(AdsApp.search)은 영상 조회수 필드를 지원하지 않으므로
 *   영상 광고 객체(AdsApp.videoAds) + Stats.getViews() 로 수집한다.
 *
 * 출력: 통합RD_원본과 동일한 33컬럼 헤더로 기록 (소재명 파싱값 채움, 메타/틱톡 인지 행과 동일 형식).
 *   유튜브=인지 → 전환수·CPA·인스타링크는 공란, 조회수→ThruPlay, 결과당비용=광고비÷조회수.
 */

// ===== 설정 (여기 두 줄만 채우면 됨) =====
var SHEET_ID = '여기에_구글시트_ID_붙여넣기';   // 기존 통합RD 시트 ID 그대로 넣어도 됨 (탭만 새로 생김)
var TAB_NAME = 'google_raw';                    // 기록할 탭 이름 (그대로 두면 됨)

// 백필(과거 재수집)할 때만 아래 두 날짜를 'YYYY-MM-DD' 로 채운다. 평소엔 비워두면 '어제'만 수집.
var START_DATE = '';
var END_DATE   = '';
// =========================================

// 통합RD_원본과 동일한 33컬럼 헤더 (build_rd.py 의 RD_COLUMNS 순서와 동일)
var HEADER = ['날짜','매체','광고목적','캠페인명','광고그룹명','소재명',
  '제작월','채널구분','영상/이미지 구분','제품코드','광고종류','스킴명','대분류 포맷','소분류 연출',
  '배리에이션 여부','지면 유형','상세연출(소재구분)','프로젝트','파트 구분','마케터','집행시작일','본부 구분','PD/디자이너',
  '노출','클릭','CTR (%)','광고비 (KRW)','CPC (KRW)','전환수','CPA (KRW)','ThruPlay','결과당비용','인스타링크'];

function main() {
  var dates = datesToCollect();          // ['2026-07-20', ...]
  var out = [];
  var newDates = {};

  var ads = AdsApp.videoAds().get();     // 계정의 모든 영상 광고
  var adList = [];
  while (ads.hasNext()) { adList.push(ads.next()); }

  for (var di = 0; di < dates.length; di++) {
    var ymd = dates[di];                 // '2026-07-20'
    var c = ymd.replace(/-/g, '');       // '20260720' (getStatsFor 형식)

    for (var ai = 0; ai < adList.length; ai++) {
      var ad = adList[ai];
      var st = ad.getStatsFor(c, c);
      var imps = Number(st.getImpressions() || 0);
      if (imps <= 0) continue;           // 그날 노출 없는 소재는 건너뜀

      var adName = ad.getName() || '';
      var adGroup = '', campaign = '';
      try {
        var ag = ad.getVideoAdGroup();
        adGroup  = ag.getName();
        campaign = ag.getVideoCampaign().getName();
      } catch (e) {
        try { campaign = ad.getVideoCampaign().getName(); } catch (e2) {}
      }

      // 빙과 소재만 남긴다 (제과·팝콘 등 제외)
      if (!isBingwa(campaign, adName)) continue;

      var clks  = Number(st.getClicks() || 0);
      var spend = Number(st.getCost() || 0);    // 계정 통화(KRW) 그대로
      var views = Number(st.getViews() || 0);

      // build_rd.py 와 동일 계산 (유튜브=인지: 전환수·CPA 공란, ThruPlay=조회수, 결과당비용=광고비÷조회수)
      var ctr        = imps > 0 ? Math.round(clks / imps * 100 * 10000) / 10000 : 0;
      var cpc        = clks > 0 ? Math.round(spend / clks) : 0;
      var resultCost = views > 0 ? Math.round(spend / views) : 0;
      var parsed     = parseAdName(adName);      // 소재명 파싱 17컬럼

      newDates[ymd] = true;
      out.push(
        [ymd, 'YouTube', '인지', campaign, adGroup, adName]
          .concat(parsed)
          .concat([imps, clks, ctr, spend, cpc, 0, '', views, resultCost, ''])
      );
    }
  }

  writeToSheet(out, newDates);
  Logger.log('수집 완료: ' + out.length + '행 (' + dates.length + '일)');
}

function datesToCollect() {
  if (START_DATE && END_DATE) {
    var list = [];
    var d = new Date(START_DATE + 'T00:00:00Z');
    var e = new Date(END_DATE + 'T00:00:00Z');
    while (d.getTime() <= e.getTime()) {
      list.push(Utilities.formatDate(d, 'GMT', 'yyyy-MM-dd'));
      d = new Date(d.getTime() + 86400000);
    }
    return list;
  }
  // 평소: 계정 시간대 기준 '어제' 하루
  var tz = AdsApp.currentAccount().getTimeZone();
  var y = new Date(new Date().getTime() - 86400000);
  return [Utilities.formatDate(y, tz, 'yyyy-MM-dd')];
}

function writeToSheet(newRows, newDates) {
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName(TAB_NAME) || ss.insertSheet(TAB_NAME);

  // 이번에 다시 수집한 날짜의 기존 행은 지우고 최신본으로 교체 (재실행해도 중복 안 쌓임)
  var keep = [];
  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {            // 0행 = 헤더
    var d = String(data[i][0]);
    if (d && !newDates[d]) keep.push(data[i]);
  }

  var all = [HEADER].concat(keep).concat(newRows);
  sheet.clearContents();
  sheet.getRange(1, 1, all.length, HEADER.length).setValues(all);
}

// ===== 소재명 파싱 (build_rd.py 의 parse_ad_name 과 동일 규칙) =====
// 반환: 17개 값 배열 (제작월,채널구분,영상/이미지 구분,제품코드,광고종류,스킴명,대분류 포맷,
//        소분류 연출,배리에이션 여부,지면 유형,상세연출(소재구분),프로젝트,파트 구분,마케터,
//        집행시작일,본부 구분,PD/디자이너). 규칙 밖(띄어쓰기 이름 등)이면 전부 공란.
function parseAdName(adName) {
  var r = ['','','','','','','','','','','','','','','','',''];   // 17개
  if (!adName) return r;
  var b = adName.indexOf('[');
  if (b === -1) return r;
  var p = adName.slice(b).split('_');
  if (p.length < 3) return r;
  var m = /^(\[.+?\])(.*)$/.exec(p[0]);
  if (m) { r[0] = m[1]; r[1] = m[2]; }          // 제작월, 채널구분
  if (p.length > 1) r[2] = p[1];                // 영상/이미지 구분
  if (p.length > 2) r[3] = p[2];                // 제품코드
  if (p.length > 3) r[4] = p[3];                // 광고종류
  if (p.length > 4) r[5] = p[4];                // 스킴명
  if (p.length > 5) r[6] = p[5];                // 대분류 포맷
  if (p.length > 6) r[7] = p[6];                // 소분류 연출
  if (p.length > 7) { var kl = p[7].split('.'); r[8] = kl[0]; r[9]  = kl.length > 1 ? kl.slice(1).join('.') : ''; }  // 배리에이션, 지면유형
  if (p.length > 8) { var mn = p[8].split('.'); r[10] = mn[0]; r[11] = mn.length > 1 ? mn.slice(1).join('.') : ''; } // 상세연출, 프로젝트
  if (p.length > 9)  r[12] = p[9];              // 파트 구분
  if (p.length > 10) r[13] = p[10];             // 마케터
  if (p.length > 11) r[14] = p[11];             // 집행시작일
  if (p.length > 12) r[15] = p[12];             // 본부 구분
  if (p.length > 13) r[16] = p.slice(13).join('_');  // PD/디자이너
  return r;
}

// ===== 빙과만 남기기 (scripts/meta_api.py 의 is_bingwa 와 동일 규칙) =====
var BINGWA_CAMPAIGN_KW = ['빙과','파인트','스틱바','얼리썸머','패밀리세일','듬뿍바','딸기축제',
  '망요바','모나카','미니생초코','쫀득바','초코페스티벌','멜론바','젤라또','요거트바','복요파','블요바','제로바'];
var BINGWA_AD_CODES = ['BA망','CO바','P혼','ZB귤','ZB파','제로바','BA딸','BA옥','BA혼','JD망','JD멜','MB바','M우','M팥'];
var BINGWA_PRODUCT_CODES = ['P혼','P망','P요','P복','P바','P초','P말','P오','P우','P치','P애','P고'];

function productCode(adName) {
  if (!adName || adName.charAt(0) !== '[') return '';
  var parts = adName.split('_');
  return parts.length > 2 ? parts[2] : '';
}
function containsAny(arr, s) {
  for (var i = 0; i < arr.length; i++) { if (s.indexOf(arr[i]) !== -1) return true; }
  return false;
}
function isBingwa(campaign, adName) {
  var c = campaign || '', a = adName || '';
  if (containsAny(BINGWA_CAMPAIGN_KW, c)) return true;
  if (containsAny(BINGWA_AD_CODES, a)) return true;
  if (BINGWA_PRODUCT_CODES.indexOf(productCode(a)) !== -1) return true;
  if (a.indexOf('C혼') !== -1 && a.indexOf('PC혼') === -1) return true;   // C혼=빙과, PC혼=팝콘 제외
  return false;
}
