"""يبني site/data.json من بيانات سُحبت بالمتصفح (groups.tsv + steam.json)."""
import json, math, os, re, sys
from datetime import datetime, timezone
from difflib import get_close_matches

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from scraper import cats_from_steam, price_iqd, EDITIONS, key_of  # noqa

SEO = {'s': 'steam-global-account', 'f': 'fc-27-accounts', 'c': 'cod-account', 'm': 'minecraft-account',
       'a': 'arc-raiders-accounts', 'r': 'rus-accounts-buy', '6': 'tom-clancys-rainbow-six-siege-account',
       'x': 'counter-strike-global-offensive-accounts'}
ED_AR = dict(EDITIONS); ED_AR["standard"] = "ستاندرد"; ED_AR["goty"] = "غوتي"

cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
games = json.load(open(os.path.join(ROOT, "games.json"), encoding="utf-8"))
gmap = {g["id"]: g for g in games}
steam = json.load(open(sys.argv[2], encoding="utf-8"))
groups = {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.rstrip("\n")
    if not line:
        continue
    key, ed, usd, user, oid, seo = line.split("\t")
    groups.setdefault(key, {})[ed] = (float(usd), user, oid, SEO.get(seo, seo))

COVER_FIX = json.load(open(os.path.join(ROOT, "covers_fix.json"))) if os.path.exists(os.path.join(ROOT, "covers_fix.json")) else {}

def img_of(info, appid):
    if appid and str(appid) in COVER_FIX:
        return "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/" + COVER_FIX[str(appid)]
    if info and info[2]:
        return info[2].split("?")[0]
    return f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg" if appid else ""

def to_info(a):
    return {"appid": a[0], "name": a[1], "img": a[2], "free": bool(a[3]),
            "genres": [x for x in a[4].split(",") if x], "cats": [x for x in a[5].split(",") if x]}

clean = {key_of(n): n for n in steam["names"]}
final = {}
KNOWN_APPS = {g.get("appid") for g in games} | {v[0] for v in steam["known"].values() if v}
known_order = {g["id"]: i for i, g in enumerate(games)}
for key, eds in groups.items():
    if key.startswith("k:"):
        gid = key[2:]; g = gmap[gid]; a = steam["known"].get(gid)
        appid = g.get("appid") or (a[0] if a else None)
        name_en = g.get("steam_name") or (a[1] if a else gid)
        meta = dict(game_id=gid, name_ar=g["name_ar"], name_en=name_en, appid=appid,
                    cover=g.get("cover", img_of(a, appid)), cat=g.get("cat", []))
        o = (0, known_order[gid], "")
    else:
        k = key_of(key[2:])
        m = clean.get(k) or next(iter([clean[x] for x in get_close_matches(k, list(clean), 1, 0.8)]), None)
        a = steam["names"].get(m) if m else None
        if not a or a[3]:
            continue
        if a[0] in KNOWN_APPS:
            continue  # لعبة موجودة بالقائمة العربية
        info = to_info(a)
        meta = dict(game_id=f"app{a[0]}", name_ar="", name_en=a[1], appid=a[0],
                    cover=img_of(a, a[0]), cat=cats_from_steam(info, a[1]))
        o = (1, 0, a[1].lower())
    gid = meta["game_id"]
    if gid in final:
        for ed, v in eds.items():
            if ed not in final[gid][1] or v[0] < final[gid][1][ed][0]:
                final[gid][1][ed] = v
    else:
        final[gid] = (meta, dict(eds), o)
items, report = [], []
for meta, eds, o in final.values():
    for ed, (usd, user, oid, seo) in eds.items():
        cost, sell = price_iqd(usd, cfg)
        items.append(dict(meta, edition=ED_AR.get(ed, ed), edition_key=ed, price_iqd=sell, _o=o))
        report.append((meta["name_ar"] or meta["name_en"], ED_AR.get(ed, ed), usd, cost, sell, user,
                       f"https://www.g2g.com/categories/{seo}/offer/{oid}"))

have = {x["game_id"] for x in items}
for g in games:
    if g["id"] in have:
        continue
    a = steam["known"].get(g["id"]); appid = g.get("appid") or (a[0] if a else None)
    items.append(dict(game_id=g["id"], name_ar=g["name_ar"], name_en=g.get("steam_name") or g["id"], appid=appid,
                      cover=g.get("cover", img_of(a, appid)), cat=g.get("cat", []), edition="",
                      edition_key="standard", price_iqd=None, _o=(0, known_order[g["id"]], "")))

ed_order = {k: i for i, (k, _) in enumerate([("standard", "")] + EDITIONS)}
items.sort(key=lambda x: (x["_o"], ed_order.get(x["edition_key"], 99)))
for x in items:
    x.pop("_o")
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
json.dump({"updated": now, "telegram": cfg["telegram"], "items": items},
          open(os.path.join(ROOT, "site", "data.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
lines = ["| اللعبة | الإصدار | G2G $ | التكلفة | سعر البيع | البائع | الرابط |", "|---|---|---|---|---|---|---|"]
for r in sorted(report, key=lambda r: r[0].lower()):
    lines.append(f"| {r[0]} | {r[1]} | {r[2]:.2f} | {r[3]:,} | {r[4]:,} | {r[5]} | [شراء]({r[6]}) |")
open(os.path.join(ROOT, "supplier_report.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("games:", len({x['game_id'] for x in items}), "priced:", len({x['game_id'] for x in items if x['price_iqd']}), "items:", len(items))
