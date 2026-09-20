# -*- coding: utf-8 -*-
"""權證資料擷取：證交所(上市) + 櫃買(上櫃)。純標準函式庫，無外部相依。"""
import json, gzip, sqlite3, time, os
import urllib.request

UA = "Mozilla/5.0 (warrant-analytics/1.0)"
TWSE_OPENAPI = "https://openapi.twse.com.tw/v1"
TWSE_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_OPENAPI = "https://www.tpex.org.tw/openapi/v1"

# MI_INDEX 的權證分類代碼
TWSE_QUOTE_TYPES = ["0999", "0999P", "0999C", "0999B", "0999X", "0999Y"]

ISSUERS = ["元大", "統一", "凱基", "群益", "永豐", "富邦", "中信", "台新", "兆豐",
           "日盛", "國泰", "玉山", "第一金", "華南", "康和", "國票", "福邦", "安泰",
           "宏遠", "大昌", "新光", "元富", "合庫", "亞東", "石橋", "犇亞", "高橋",
           "大展", "致和", "光和", "德信", "陽信", "上海", "土銀", "臺銀", "華信",
           "萬泰", "大慶", "口袋"]


def fetch(url, tries=3, timeout=90):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("fetch failed " + url + ": " + str(last))


def roc_to_ad(s):
    """民國 1150919 -> 2026-09-19；西元 20250924 -> 2025-09-24；空值 -> None"""
    s = (s or "").strip()
    if not s.isdigit():
        return None
    if len(s) == 7:
        return "%04d-%s-%s" % (int(s[:3]) + 1911, s[3:5], s[5:7])
    if len(s) == 8:
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
    return None


def num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "--", "-", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def guess_issuer(warrant_name, underlying_name):
    """權證簡稱 = 標的簡稱 + 券商 + 年月碼 + 購/售 + 序號，剝掉標的後比對券商。"""
    n = (warrant_name or "").strip()
    u = (underlying_name or "").strip()
    if u and n.startswith(u):
        n = n[len(u):]
    else:
        for cut in range(min(len(u), 4), 0, -1):
            if n.startswith(u[:cut]):
                n = n[cut:]
                break
    for iss in sorted(ISSUERS, key=len, reverse=True):
        if n.startswith(iss):
            return iss
    for iss in sorted(ISSUERS, key=len, reverse=True):
        if iss in (warrant_name or ""):
            return iss
    return None


# ---------------------------------------------------------------- 上市基本資料
def twse_basic():
    rows = fetch(TWSE_OPENAPI + "/opendata/t187ap37_L")
    out = []
    for r in rows:
        issued = num(r.get("發行單位數量(仟單位)"))
        ratio_k = num(r.get("最新標的履約配發數量(每仟單位權證)"))
        out.append(dict(
            snapshot_date=roc_to_ad(r.get("出表日期")),
            code=(r.get("權證代號") or "").strip(),
            name=(r.get("權證簡稱") or "").strip(),
            market="TSE",
            issuer=guess_issuer(r.get("權證簡稱"), r.get("標的證券/指數")),
            kind="CALL" if r.get("權證類型") == "認購" else "PUT",
            category=(r.get("類別") or "").strip(),
            style=None,                      # 證交所此表未提供美式/歐式
            underlying=None,                 # 由行情表回填標的代號
            underlying_nm=(r.get("標的證券/指數") or "").strip(),
            first_trade=roc_to_ad(r.get("履約開始日")),
            last_trade=roc_to_ad(r.get("最後交易日")),
            maturity=roc_to_ad(r.get("履約截止日")),
            strike=num(r.get("最新履約價格(元)/履約指數")),
            ratio=(ratio_k / 1000.0) if ratio_k else None,
            cap_price=num(r.get("最新上限價格(元)/上限指數")) or None,
            floor_price=num(r.get("最新下限價格(元)/下限指數")) or None,
            issued_units=issued,
            cancelled=None,
            outstanding=None,
            settle_type=(r.get("結算方式(詳附註編號說明)") or "").strip(),
            note=(r.get("備註") or "").strip()[:2000],
        ))
    return out


# ---------------------------------------------------------------- 上櫃基本資料
def tpex_basic():
    rows = fetch(TPEX_OPENAPI + "/tpex_warrant_issue")
    out = []
    for r in rows:
        issued = num(r.get("InitialIssuance")) or 0
        follow = num(r.get("Accum.Accum.Issuance")) or 0
        cancel = num(r.get("Accum.CanceledWarrant")) or 0
        total = issued + follow
        name = (r.get("Name") or "").strip()
        und_nm = (r.get("UnderlyingStock") or "").strip()
        kind_raw = (r.get("Type") or "").strip()
        out.append(dict(
            snapshot_date=roc_to_ad(r.get("Date")),
            code=(r.get("Code") or "").strip(),
            name=name,
            market="OTC",
            issuer=guess_issuer(name, und_nm),
            kind="CALL" if kind_raw == "認購" else "PUT",
            category=kind_raw,
            style=(r.get("American/European") or "").strip() or None,
            underlying=(r.get("UnderlyingStockCode") or "").strip(),
            underlying_nm=und_nm,
            first_trade=roc_to_ad(r.get("ListedDate")),
            last_trade=None,
            maturity=roc_to_ad(r.get("ExpiryDate")),
            strike=num(r.get("LatestExercisePrice")),
            ratio=num(r.get("Latest ExerciseRatio")),
            cap_price=num(r.get("CapPrice/Index")),
            floor_price=num(r.get("FloorPrice/Index")),
            issued_units=total or None,
            cancelled=cancel,
            outstanding=(total - cancel) if total else None,
            settle_type=None,
            note=None,
        ))
    return out


# ---------------------------------------------------------------- 上市每日行情
def twse_quotes(date_str):
    """date_str: YYYYMMDD。回傳當日全部上市權證收盤行情。"""
    out = []
    trade_date = "%s-%s-%s" % (date_str[:4], date_str[4:6], date_str[6:8])
    for t in TWSE_QUOTE_TYPES:
        url = TWSE_RWD + "?date=" + date_str + "&type=" + t + "&response=json"
        d = fetch(url)
        if d.get("stat") != "OK":
            continue
        for tbl in d.get("tables", []):
            f = tbl.get("fields") or []
            if "證券代號" not in f or "標的代號" not in f:
                continue
            ix = dict((k, i) for i, k in enumerate(f))

            def g(row, k):
                return row[ix[k]] if k in ix else None

            for row in tbl.get("data", []):
                out.append(dict(
                    trade_date=trade_date,
                    code=(g(row, "證券代號") or "").strip(),
                    market="TSE",
                    name=(g(row, "證券名稱") or "").strip(),
                    open=num(g(row, "開盤價")),
                    high=num(g(row, "最高價")),
                    low=num(g(row, "最低價")),
                    close=num(g(row, "收盤價")),
                    volume=num(g(row, "成交股數")),
                    turnover=num(g(row, "成交金額")),
                    trades=num(g(row, "成交筆數")),
                    bid=num(g(row, "最後揭示買價")),
                    bid_size=num(g(row, "最後揭示買量")),
                    ask=num(g(row, "最後揭示賣價")),
                    ask_size=num(g(row, "最後揭示賣量")),
                    underlying=(g(row, "標的代號") or "").strip(),
                    und_close=num(g(row, "標的收盤價/指數")),
                ))
        time.sleep(0.6)          # 對證交所友善一點
    return out


# ---------------------------------------------------------------- 上櫃每日行情
def tpex_quotes():
    rows = fetch(TPEX_OPENAPI + "/tpex_warrant_daily_quts")
    out = []
    for r in rows:
        out.append(dict(
            trade_date=roc_to_ad(r.get("Date")),
            code=(r.get("Code") or "").strip(),
            market="OTC",
            name=(r.get("Name") or "").strip(),
            open=num(r.get("Open")),
            high=num(r.get("High")),
            low=num(r.get("Low")),
            close=num(r.get("Close")),
            volume=num(r.get("TradeVol.")),
            turnover=num(r.get("TradeValue")),
            trades=num(r.get("No.OfTransactions")),
            bid=None, bid_size=None, ask=None, ask_size=None,
            underlying=(r.get("UnderlyingStockCode") or "").strip(),
            und_close=num(r.get("UnderlyingStockClosePrice")),
        ))
    return out


# ---------------------------------------------------------------- 寫入
def connect(path="warrant.db"):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "schema.sql"), encoding="utf-8") as fh:
        con.executescript(fh.read())
    return con


def upsert(con, table, rows):
    if not rows:
        return 0
    cols = list(rows[0].keys())
    ph = ",".join(["?"] * len(cols))
    sql = "INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (table, ",".join(cols), ph)
    con.executemany(sql, [[r[c] for c in cols] for r in rows])
    con.commit()
    return len(rows)


def backfill_underlying(con):
    """上市基本資料缺標的代號，用行情表的對應關係回填。"""
    con.execute("""
        UPDATE warrant_basic
           SET underlying = (SELECT q.underlying FROM warrant_quote q
                              WHERE q.code = warrant_basic.code
                                AND q.underlying <> ''
                              ORDER BY q.trade_date DESC LIMIT 1)
         WHERE underlying IS NULL OR underlying = ''
    """)
    con.commit()
