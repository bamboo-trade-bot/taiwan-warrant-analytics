# -*- coding: utf-8 -*-
"""權證資料擷取：證交所(上市) + 櫃買(上櫃)。純標準函式庫，無外部相依。"""
import json, gzip, sqlite3, time, os, datetime
import urllib.request
import notes as notes_mod

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


def latest_trading_date(max_back=10):
    """從今天往回找最近一個有權證收盤行情的交易日，回傳 YYYYMMDD。

    週末與國定假日沒有資料，排程跑在哪一天都能自己找到正確的交易日；
    收盤資料當天稍晚才上線，執行太早也會自動退回前一個交易日。
    """
    d = datetime.date.today()
    for _ in range(max_back):
        s = d.strftime("%Y%m%d")
        try:
            r = fetch(TWSE_RWD + "?date=" + s + "&type=0999&response=json", tries=1)
            for tbl in r.get("tables", []):
                if tbl.get("data"):
                    return s
        except Exception:
            pass
        d -= datetime.timedelta(days=1)
    return None


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
        note = (r.get("備註") or "").strip()[:2000]
        # 必須在寫入前就解析好。若留到事後用 UPDATE 補，去重比對時
        # 「已補」的舊列和「未補」的新列永遠不相等，去重就失效了。
        out_units, out_src = notes_mod.outstanding(note, issued)
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
            cancelled=None if out_units is None else (issued - out_units),
            outstanding=out_units,
            outstanding_src=out_src,
            settle_type=(r.get("結算方式(詳附註編號說明)") or "").strip(),
            note=note,
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
            outstanding_src="TPEX" if total else None,
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
    migrate(con)
    return con


def migrate(con):
    """補上新加的欄位。

    schema.sql 用的是 CREATE TABLE IF NOT EXISTS，對已存在的資料庫不會生效，
    而正式環境的資料庫是從 release 取回來的舊檔，只能靠 ALTER 補。
    """
    wanted = {
        "warrant_quote": [("quote_src", "TEXT")],
        "warrant_basic": [("outstanding_src", "TEXT")],
    }
    for table, cols in wanted.items():
        have = set(r[1] for r in con.execute("PRAGMA table_info(%s)" % table))
        for name, typ in cols:
            if name not in have:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, typ))
    con.commit()


def upsert(con, table, rows):
    if not rows:
        return 0
    cols = list(rows[0].keys())
    ph = ",".join(["?"] * len(cols))
    sql = "INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (table, ",".join(cols), ph)
    con.executemany(sql, [[r[c] for c in cols] for r in rows])
    con.commit()
    return len(rows)


def upsert_basic(con, rows):
    """只在內容和該檔的上一份快照不同時才寫入新的一列。

    權證基本資料每天有 5 萬列，但絕大多數逐日完全不變。全部照存，一年會
    長到十幾 GB；只記錄異動則一天通常只有數百列。查詢語意完全不變——
    「取小於等於交易日的最新一份快照」在稀疏快照下仍然正確。
    """
    if not rows:
        return (0, 0)
    cols = list(rows[0].keys())
    cmp_cols = [c for c in cols if c != "snapshot_date"]
    ci = cmp_cols.index("code")

    prev = {}
    sql = ("SELECT %s FROM warrant_basic b WHERE snapshot_date = "
           "(SELECT MAX(snapshot_date) FROM warrant_basic WHERE code = b.code)"
           % ",".join(cmp_cols))
    for row in con.execute(sql):
        prev[row[ci]] = row

    changed = [r for r in rows if prev.get(r["code"]) != tuple(r[c] for c in cmp_cols)]
    upsert(con, "warrant_basic", changed)
    return (len(changed), len(rows) - len(changed))


def upsert_adjustments(con, rows):
    """把備註解析出的除權息調整存成時間軸，回傳寫入筆數。

    交易所只提供「最新」履約價。要算某檔三個月前的隱含波動率，就必須知道
    當時的履約價，這張表就是為了回答那個問題。
    """
    data = []
    for r in rows:
        for d, kind, strike, ratio in notes_mod.adjustments(r.get("note")):
            data.append((r["code"], d, kind, strike, ratio))
    if data:
        con.executemany("INSERT OR REPLACE INTO warrant_adjustment "
                        "(code, adj_date, kind, strike, ratio) VALUES (?,?,?,?,?)", data)
        con.commit()
    return len(data)


def underlying_map(con):
    """從行情表取出每檔權證的標的代號。

    證交所的基本資料表只給標的「名稱」不給代號，得靠行情表補。必須在寫入
    基本資料之前就補好：若留到事後用 UPDATE 修改，下次比對時「已補」的舊
    列和「未補」的新列永遠不相等，去重就整個失效。
    """
    return dict(con.execute("""
        SELECT code, underlying FROM warrant_quote q
         WHERE underlying <> ''
           AND trade_date = (SELECT MAX(trade_date) FROM warrant_quote
                              WHERE code = q.code AND underlying <> '')
    """))
