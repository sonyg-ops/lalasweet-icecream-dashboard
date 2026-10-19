# -*- coding: utf-8 -*-
"""일회성 읽기 전용: 메타 메인 계정(전환) 광고 단위 월별 광고비 → CSV (아티팩트로만 저장, 저장소 data/ 미변경)."""
import os, sys, json, time, csv, datetime, requests
TOKEN = os.environ["META_ACCESS_TOKEN"]; ACCT = os.environ["META_AD_ACCOUNT_ID"]
SINCE = datetime.date.fromisoformat(os.environ["SINCE"]); UNTIL = datetime.date.fromisoformat(os.environ["UNTIL"])
BASE = "https://graph.facebook.com/v21.0"
def call(method, url, **kw):
    for i, d in enumerate([0, 10, 30, 60, 120, 300, 300]):
        time.sleep(d)
        try:
            data = requests.request(method, url, timeout=180, **kw).json()
        except Exception as e:
            print("net err", e); continue
        if "error" not in data: return data
        print("API err", data["error"].get("code"), data["error"].get("message"))
        if data["error"].get("code") not in (1, 2, 4, 17, 341, 613) and not data["error"].get("is_transient"):
            sys.exit(1)
    sys.exit(1)
def months(a, b):
    cur = a.replace(day=1)
    while cur <= b:
        nxt = (cur.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        yield max(cur, a), min(nxt - datetime.timedelta(days=1), b)
        cur = nxt
rows = []
for s, u in months(SINCE, UNTIL):
    p = {"level": "ad", "fields": "campaign_name,adset_name,ad_name,impressions,spend,inline_link_clicks,actions",
         "time_range": json.dumps({"since": str(s), "until": str(u)}),
         "filtering": json.dumps([{"field": "impressions", "operator": "GREATER_THAN", "value": 0}]),
         "use_unified_attribution_setting": "true", "access_token": TOKEN}
    rid = call("POST", f"{BASE}/{ACCT}/insights", data=p)["report_run_id"]
    while True:
        st = call("GET", f"{BASE}/{rid}", params={"access_token": TOKEN})
        if st.get("async_status") == "Job Completed": break
        if st.get("async_status") in ("Job Failed", "Job Skipped"):
            print("job failed", st); sys.exit(1)
        time.sleep(5)
    url, qp, n = f"{BASE}/{rid}/insights", {"limit": 500, "access_token": TOKEN}, 0
    while url:
        r = call("GET", url, params=qp); qp = {}
        for x in r.get("data", []):
            act = {a["action_type"]: a["value"] for a in x.get("actions", []) or []}
            pur = next((act[t] for t in ("offsite_conversion.fb_pixel_purchase", "omni_purchase", "purchase") if t in act), 0)
            rows.append({"month": str(s)[:7], "campaign_name": x.get("campaign_name"), "adset_name": x.get("adset_name"),
                         "ad_name": x.get("ad_name"), "impressions": x.get("impressions", 0), "spend": x.get("spend", 0),
                         "clicks": x.get("inline_link_clicks", 0), "purchases": pur}); n += 1
        url = r.get("paging", {}).get("next")
    print(s, u, n, "rows"); time.sleep(15)
os.makedirs("out", exist_ok=True)
with open("out/meta_monthly_adlevel.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["month","campaign_name","adset_name","ad_name","impressions","spend","clicks","purchases"])
    w.writeheader(); w.writerows(rows)
print("total", len(rows))
