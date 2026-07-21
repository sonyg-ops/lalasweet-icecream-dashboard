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
 * 출력 컬럼(메타/틱톡 raw CSV와 동일):
 *   date, campaign_name, adset_name, ad_name, impressions, clicks, spend, conversions, thruplay, 광고목적
 *   - 인지(영상): conversions=0, thruplay=조회수(views)  → 대시보드가 결과당비용=광고비÷조회수 계산
 */

// ===== 설정 (여기 두 줄만 채우면 됨) =====
var SHEET_ID = '여기에_구글시트_ID_붙여넣기';   // 기존 통합RD 시트 ID 그대로 넣어도 됨 (탭만 새로 생김)
var TAB_NAME = 'google_raw';                    // 기록할 탭 이름 (그대로 두면 됨)

// 백필(과거 재수집)할 때만 아래 두 날짜를 'YYYY-MM-DD' 로 채운다. 평소엔 비워두면 '어제'만 수집.
var START_DATE = '';
var END_DATE   = '';
// =========================================

var HEADER = ['date','campaign_name','adset_name','ad_name',
              'impressions','clicks','spend','conversions','thruplay','광고목적'];

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

      // 유튜브 영상은 전부 인지광고 → 광고목적 고정, 조회수를 결과 지표(thruplay 자리)로 사용
      var purpose     = '인지';
      var conversions = 0;
      var thruplay    = views;

      newDates[ymd] = true;
      out.push([ymd, campaign, adGroup, adName, imps, clks, spend, conversions, thruplay, purpose]);
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
