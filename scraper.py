"""
Vip X Store — يسحب كل ألعاب ستيم (حسابات جديدة صفر ساعات) من G2G ويحسب سعر البيع بالدينار.

التشغيل:
    python scraper.py                    # سحب حقيقي من G2G (يشتغل على GitHub كل ساعة)
    python scraper.py --sample x.json    # تجربة على بيانات محفوظة بدون إنترنت

شلون يلگى كل الألعاب:
  1. يمر على كل صفحات قسم Steam Accounts بـ G2G + أقسام الألعاب اللي بـ games.json
  2. من عنوان كل عرض يطلع اسم اللعبة، ويتأكد منه من متجر ستيم (الاسم الرسمي + الغلاف + النوع)
  3. يختار أرخص عرض لكل لعبة ولكل إصدار، من بائع موثوق
الألعاب اللي بـ games.json تاخذ اسمها العربي وقسمها من هناك، والباقي ينضاف تلقائياً.
"""
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "site")
API = "https://sls.g2g.com/offer/search"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")

FRESH_RE = re.compile(
    r"(\b0\s*h\b|\b0\s*hrs?\b|\b0h\b|\boh played|\b0\s*hours?|zero hours?|0 level|"
    r"fresh|new steam account|brand new|new account|original (e-?)?mail|fist email|first email|native email)",
    re.I)

GLOBAL_EXCLUDE = [
    "epic", "ps4", "ps5", "playstation", "xbox", "switch", "gift", "china", "chinese",
    "community ban", "red flag", "limited", "web app", "webapp", "coins", "points", "wallet",
    "trial", "without game", "not included", "no game", "does not have any games", "rent",
    "shared", "offline", "family", "key only", "cd key", "achievement", "boost", "₹", "inr",
    "played 5h", "dm me", "region lock", "turkey", "argentina", "random game", "mystery",
    "beta", "playtest", "unlock all", "mod menu", "hack", "cheat",
    "unbought", "subscription", "pre-order", "preorder", "pre order", "pre-purchase", "already in library",
    "visit in advance", "anniversary", " dlc", "lvl", " yrs", "years old", "redsec",
    "random country", "random region", "no games", "un-bought", "not bought", "not purchased", "unused",
    "to-play", "to play", "not owned", "ready to buy", "veteran", "phone verified", "non prime", "non-prime",
    "market disabled", "episode", "tlauncher", "game pass", "games:", "sms", "number verif", "outlook",
    "add friends", "activision", "& clean",
]
BUNDLE_RE = re.compile(r"\b\d+\s*(games|game)\b|\+\s*\d+\s*(other|more)", re.I)
BLOCK_SELLERS = {"el9in", "AdamBrian", "Maper", "week1997", "yubabuneng", "Stark2Doom", "Maddigital",
                 "YellowLion", "GauGau", "WangFuGuei", "diminutive", "SyndicatoR_x"}
TRUST = {}


def build_trust(offers):
    """ثقة البائع من مجموع تقييماته بكل عروضه (بعض العروض تجي بلا تقييم)."""
    agg = {}
    for o in offers:
        a = agg.setdefault(o.get("username"), [0, 0.0])
        r = o.get("total_rating") or 0
        a[0] += r
        a[1] += r * (o.get("satisfaction_rate") or 0)
    TRUST.clear()
    TRUST.update({u: a[0] >= 5 and a[1] / a[0] >= 0.97 for u, a in agg.items() if a[0]})
COVER_FIX = {}
try:
    COVER_FIX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "covers_fix.json")))
except Exception:
    pass


def fix_cover(appid, cover):
    if appid and str(appid) in COVER_FIX:
        return "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/" + COVER_FIX[str(appid)]
    return cover

EDITIONS = [
    ("ultimate plus", "ألتيميت بلس"), ("ultimate", "ألتيميت"), ("deluxe", "ديلوكس"),
    ("gold", "غولد"), ("premium", "بريميوم"), ("vault", "فولت"), ("complete", "كومبليت"),
    ("director", "ديركتورز كت"), ("goty", "غوتي"), ("game of the year", "غوتي"),
]

# كلمات تنشال من العنوان حتى يبقى اسم اللعبة بس
NOISE = re.compile(
    r"\b(steam|account|accounts|acc|fresh|new|brand|instant|delivery|fast|auto|full|access|"
    r"original|native|mail|email|e-mail|fist|first|change|changeable|can|data|played|playing|"
    r"hours?|hrs?|0h|oh|0|level|global|region|free|pc|game|games|edition|standard|"
    r"warranty|safe|secure|verified|phone|guard|lifetime|cheap|best|price|offer|online|"
    r"with|the game|stock|ready|login|password|info|details)\b", re.I)
SPLIT = re.compile(r"[|｜丨/\\◈•●⟡✦✧★☆【】\[\]()（）{}⟦⟧<>«»~]|\s-{1,3}\s|\s—\s|--+|::")


def load(name, default=None):
    p = os.path.join(ROOT, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(name, data):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def http_json(url, referer="https://www.g2g.com/"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Origin": referer.rstrip("/"), "Referer": referer})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def norm(t):
    t = (t or "").lower().replace("™", "").replace("®", "").replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


def wmatch(word, text):
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


def key_of(name):
    return re.sub(r"[^a-z0-9]+", "", norm(name))


# ---------------------------------------------------------------- G2G
def fetch(seo, q, cfg, max_pages):
    out = []
    for page in range(1, max_pages + 1):
        params = {"seo_term": seo, "page_size": cfg["page_size"], "page": page,
                  "sort": "lowest_price", "currency": "USD", "country": "US"}
        if q:
            params["q"] = q
        try:
            data = http_json(API + "?" + urllib.parse.urlencode(params))
        except Exception as e:
            print(f"  ! {seo} q={q} صفحة {page}: {e}")
            break
        res = (data.get("payload") or {}).get("results") or []
        for r in res:
            r["_seo"] = seo
        out.extend(res)
        if len(res) < cfg["page_size"]:
            break
        time.sleep(cfg["request_delay_seconds"])
    print(f"  {seo} q={q!r}: {len(out)} عرض")
    return out


# ---------------------------------------------------------------- الفلترة
def edition_of(title):
    t = norm(title)
    for k, ar in EDITIONS:
        if k in t:
            return ("goty" if k == "game of the year" else k), ar
    return "standard", "ستاندرد"


from collections import Counter
REJECT = Counter()


def base_ok(o, cfg, steam=True, fresh=True, extra_ex=()):
    r = _base_ok(o, cfg, steam, fresh, extra_ex)
    REJECT[r] += 1
    return r == "ok"


def _base_ok(o, cfg, steam=True, fresh=True, extra_ex=()):
    t = norm(o.get("title"))
    if o.get("username") in BLOCK_SELLERS:
        return "blocked"
    if not steam and not fresh:  # ماينكرافت: بس فلاترها الخاصة
        if any(e in t for e in list(extra_ex) + ["tlauncher", "game pass"]):
            return "exclude"
    else:
        ex = GLOBAL_EXCLUDE + list(extra_ex)
        if any(e in t for e in ex) or BUNDLE_RE.search(t):
            return "exclude"
    if steam and "steam" not in t and o.get("_seo") != "steam-global-account":
        return "nosteam"
    if fresh and not FRESH_RE.search(t):
        return "notfresh"
    if (o.get("available_qty") or 0) < 1:
        return "qty"
    if (o.get("min_qty") or 1) > 1 or o.get("status", "live") != "live":
        return "minq/status"
    sat, rat = (o.get("satisfaction_rate") or 0), (o.get("total_rating") or 0)
    good = rat >= cfg["min_seller_ratings"] and sat >= cfg["min_seller_satisfaction"]
    if not (good or (TRUST.get(o.get("username")) and (rat == 0 or sat >= cfg["min_seller_satisfaction"]))):
        return "trust"
    return "ok" if float(o.get("unit_price_in_usd") or 0) > 0 else "price0"


def extract_name(title):
    """يطلع اسم اللعبة من عنوان العرض."""
    t = title.replace("™", "").replace("®", "")
    best = ""
    for seg in SPLIT.split(t):
        s = NOISE.sub(" ", seg)
        for k, _ in EDITIONS:
            s = re.sub(r"\b" + re.escape(k) + r"\b", " ", s, flags=re.I)
        s = re.sub(r"[^\w:'&.\- ]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip(" -:.'")
        if len(re.sub(r"[^A-Za-z]", "", s)) >= 3 and len(s) > len(best):
            best = s
    return best


# ---------------------------------------------------------------- ستيم
MISSING = set()


def steam_lookup(name, cache, budget):
    """يرجع {appid, name, img, genres, free} من متجر ستيم أو None."""
    k = key_of(name)
    if k in cache:
        return cache[k]
    if budget["left"] <= 0:
        MISSING.add(name)
        return None
    budget["left"] -= 1
    info = None
    try:
        s = http_json("https://store.steampowered.com/api/storesearch/?" +
                      urllib.parse.urlencode({"term": name, "cc": "us", "l": "en"}),
                      referer="https://store.steampowered.com/")
        for it in (s.get("items") or [])[:5]:
            if it.get("type") != "app":
                continue
            ratio = SequenceMatcher(None, key_of(name), key_of(it["name"])).ratio()
            if ratio >= 0.72 or key_of(it["name"]).startswith(key_of(name)):
                info = steam_details(it["id"]) or {"appid": it["id"], "name": it["name"]}
                break
        time.sleep(0.35)
    except Exception as e:
        print(f"  ! ستيم {name}: {e}")
        return None
    cache[k] = info
    return info


def steam_details(appid):
    try:
        d = http_json(f"https://store.steampowered.com/api/appdetails?appids={appid}&l=en",
                      referer="https://store.steampowered.com/")
        data = (d.get(str(appid)) or {}).get("data") or {}
        if not data:
            return None
        return {"appid": appid, "name": data.get("name"), "img": data.get("header_image"),
                "free": bool(data.get("is_free")),
                "genres": [g["description"] for g in data.get("genres", [])],
                "cats": [c["description"] for c in data.get("categories", [])]}
    except Exception:
        return None


def cats_from_steam(info, name):
    g = set(info.get("genres") or [])
    c = set(info.get("cats") or [])
    n = norm(name)
    out = []
    if "call of duty" in n:
        out.append("cod")
    if "Sports" in g or re.search(r"\b(fc|fifa|nba|wwe|f1|pes|efootball)\b", n):
        out.append("sports")
    if "Racing" in g:
        out.append("racing")
    if re.search(r"horror|resident evil|silent hill|outlast|evil within|dead space|amnesia|phasmo", n):
        out.append("horror")
    if {"Online Co-op", "Online PvP", "Multi-player", "MMO"} & c or "Massively Multiplayer" in g:
        out.append("online")
    if "Simulation" in g:
        out.append("sim")
    if "RPG" in g:
        out.append("rpg")
    if "Action" in g or "Adventure" in g:
        out.append("action")
    return out


# ---------------------------------------------------------------- الحساب
def price_iqd(usd, cfg):
    cost = (usd * (1 + cfg["g2g_fee_percent"] / 100) + cfg["g2g_fee_fixed_usd"]) * cfg["usd_rate_iqd"]
    step = cfg["round_up_to_iqd"] or 1
    return round(cost), int(math.ceil((cost + cfg["profit_iqd"]) / step) * step)


# ---------------------------------------------------------------- التشغيل
def main():
    cfg, games = load("config.json"), load("games.json", [])
    cache = load("steam_cache.json", {})
    sample = None
    if "--sample" in sys.argv:
        sample = json.load(open(sys.argv[sys.argv.index("--sample") + 1], encoding="utf-8"))
    if "--raw" in sys.argv:  # ملف مسحوب من المتصفح: صفوف مختصرة
        rows = json.load(open(sys.argv[sys.argv.index("--raw") + 1], encoding="utf-8"))
        rows = rows.values() if isinstance(rows, dict) else rows
        sample = [{"offer_id": r[0], "title": r[1], "unit_price_in_usd": r[2], "username": r[3],
                   "satisfaction_rate": r[4], "total_rating": r[5], "available_qty": r[6],
                   "reserved_qty": 0, "min_qty": r[7], "status": r[8], "_seo": r[9]} for r in rows]
    budget = {"left": 0 if sample is not None else cfg.get("max_new_steam_lookups", 400)}

    # 1) جمع العروض
    pool = {}
    if sample is not None:
        for o in sample:
            pool.setdefault(o["offer_id"], o)
    else:
        for o in fetch("steam-global-account", None, cfg, cfg.get("max_pages_full_scan", 300)):
            pool.setdefault(o["offer_id"], o)
        seen = set()
        for g in games:
            for s in g.get("sources") or []:
                k = (s["seo"], s.get("q"))
                if k in seen or (s["seo"] == "steam-global-account" and not s.get("q")):
                    continue
                seen.add(k)
                for o in fetch(s["seo"], s.get("q"), cfg, cfg["max_pages_per_source"]):
                    pool.setdefault(o["offer_id"], o)
    print(f"مجموع العروض: {len(pool)}")
    build_trust(pool.values())
    if sample is None:
        with open(os.path.join(ROOT, "debug_offers.json"), "w", encoding="utf-8") as f:
            json.dump(list(pool.values())[:3] + [o for o in pool.values() if o.get("_seo") == "fc-27-accounts"][:3],
                      f, ensure_ascii=False, indent=1)

    # 2) توزيع العروض على الألعاب
    groups = {}   # game key -> {"meta":..., "best": {edition: (usd, ar, offer)}}

    def offer_to(gkey, meta, o):
        grp = groups.setdefault(gkey, {"meta": meta, "best": {}})
        ek, ear = edition_of(o["title"])
        usd = float(o.get("unit_price_in_usd") or 0)
        if ek not in grp["best"] or usd < grp["best"][ek][0]:
            grp["best"][ek] = (usd, ear, o)

    for o in pool.values():
        t = norm(o.get("title"))
        known = next((g for g in games if any(wmatch(m, t) for m in g["match"])
                      and not any(e in t for e in g.get("exclude", []))), None)
        if known:
            if base_ok(o, cfg, known.get("steam", True), known.get("fresh", True), known.get("exclude", [])):
                offer_to("k:" + known["id"], {"known": known}, o)
            continue
        if o.get("_seo") != "steam-global-account" or not base_ok(o, cfg):
            continue
        name = extract_name(o["title"])
        if not name:
            continue
        info = steam_lookup(name, cache, budget)
        if not info or info.get("free"):
            continue
        offer_to(f"s:{info['appid']}", {"steam": info}, o)

    # 3) بناء ملف الموقع
    items, report = [], []
    for gkey, grp in groups.items():
        if "known" in grp["meta"]:
            g = grp["meta"]["known"]
            info = None
            if g.get("steam", True) and "cover" not in g:
                info = steam_lookup(g.get("steam_name") or g["match"][0], cache, budget)
            gid, name_ar = g["id"], g["name_ar"]
            name_en = g.get("steam_name") or (info or {}).get("name") or g["id"]
            appid = g.get("appid") or (info or {}).get("appid")
            cover = g.get("cover", (info or {}).get("img") or (
                f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg" if appid else ""))
            cats = g.get("cat", [])
            order = 0
        else:
            info = grp["meta"]["steam"]
            gid, name_en, appid = f"app{info['appid']}", info["name"], info["appid"]
            name_ar = ""
            cover = info.get("img") or f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg"
            cats = cats_from_steam(info, name_en)
            order = 1
        cover = fix_cover(appid, cover)
        for ek, (usd, ear, o) in grp["best"].items():
            cost, sell = price_iqd(usd, cfg)
            items.append({"game_id": gid, "name_ar": name_ar, "name_en": name_en, "appid": appid,
                          "cover": cover, "cat": cats, "edition": ear, "edition_key": ek,
                          "price_iqd": sell, "_o": order})
            report.append((name_ar or name_en, ear, usd, cost, sell, o))

    # الألعاب اللي بالقائمة وما إلها عرض هسه: تنعرض حتى يلگاها الزبون ويسأل عن سعرها
    have = {x["game_id"] for x in items}
    if items:
        for g in games:
            if g["id"] in have:
                continue
            appid = g.get("appid")
            cover = g.get("cover", (cache.get(key_of(g.get("steam_name") or "")) or {}).get("img") or (
                f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg" if appid else ""))
            items.append({"game_id": g["id"], "name_ar": g["name_ar"], "name_en": g.get("steam_name") or g["id"],
                          "appid": appid, "cover": fix_cover(appid, cover), "cat": g.get("cat", []), "edition": "",
                          "edition_key": "standard", "price_iqd": None, "_o": 2})

    if not items:
        print("ما لگينا ولا عرض — الموقع يبقى على آخر تحديث.")
        sys.exit(0 if sample is None else 1)

    known_order = {g["id"]: i for i, g in enumerate(games)}
    ed_order = {k: i for i, (k, _) in enumerate([("standard", "")] + EDITIONS)}
    items.sort(key=lambda x: (min(x["_o"], 1) if x["_o"] != 2 else 0, known_order.get(x["game_id"], 0),
                              x["name_en"].lower() if x["_o"] == 1 else "", ed_order.get(x["edition_key"], 99)))
    for x in items:
        x.pop("_o")

    print("أسباب الرفض:", dict(REJECT))
    # حماية: إذا السحب طلع ناقص كلش (حظر/خطأ مؤقت) نبقي الأسعار القديمة
    old = load(os.path.join("site", "data.json"), {}) or {}
    old_priced = len({x["game_id"] for x in old.get("items", []) if x.get("price_iqd")})
    new_priced = len({x["game_id"] for x in items if x.get("price_iqd")})
    if sample is None and old_priced and new_priced < 0.7 * old_priced:
        print(f"السحب ناقص ({new_priced} مقابل {old_priced}) — نبقي الأسعار القديمة.")
        sys.exit(0)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    os.makedirs(SITE, exist_ok=True)
    with open(os.path.join(SITE, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now, "telegram": cfg["telegram"], "items": items},
                  f, ensure_ascii=False, separators=(",", ":"))
    save("steam_cache.json", cache)

    lines = ["| اللعبة | الإصدار | G2G $ | التكلفة | سعر البيع | البائع | التقييم | الرابط |",
             "|---|---|---|---|---|---|---|---|"]
    for name, ear, usd, cost, sell, o in report:
        url = f"https://www.g2g.com/categories/{o.get('_seo')}/offer/{o.get('offer_id')}"
        lines.append(f"| {name} | {ear} | {usd:.2f} | {cost:,} | {sell:,} | {o.get('username')} | "
                     f"{(o.get('satisfaction_rate') or 0)*100:.1f}% ({o.get('total_rating')}) | [شراء]({url}) |")
    md = "\n".join(lines)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"## أسعار {now} — {len(groups)} لعبة\n\n{md}\n")
    with open(os.path.join(ROOT, "supplier_report.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    if "--names-out" in sys.argv:
        with open(sys.argv[sys.argv.index("--names-out") + 1], "w", encoding="utf-8") as f:
            json.dump(sorted(MISSING), f, ensure_ascii=False)
    print(f"تم: {len(groups)} لعبة، {len(items)} منتج. أسماء تحتاج ستيم: {len(MISSING)}")


if __name__ == "__main__":
    main()
