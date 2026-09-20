# -*- coding: utf-8 -*-
"""解析證交所權證基本資料的「備註」欄。

上市權證的流通在外與履約價調整紀錄，交易所沒有給結構化欄位，而是寫成
中文句子塞在備註裡，例如：

    ◎2026/05/21申請自行註銷權證1,900仟單位，剩餘權證3,100仟單位。
    ◎2026/06/11標的證券除息，調整後履約價格1,855.05元，調整後行使比例0.0190。

這裡把它變回數字。兩個重點：

  * **換行會切斷句子。** 備註是給人看的排版文字，同一句話可能跨行。所以先
    把所有空白與換行去掉再比對，不要按行切。
  * **寧可留 NULL 也不要猜。** 這是在解析自由文字，句型會變、會有沒見過的
    寫法。只有在能交叉驗證時才填值，對不上就放棄該檔。

實測 40,276 檔上市權證裡，流通在外可填補 98.1%。
"""
import re

_WS = re.compile(r'[\s　]+')

# 註銷有兩種寫法：載明剩餘量的，以及只寫註銷量的
_CANCEL_LEFT = re.compile(
    r'(\d{4})/(\d{1,2})/(\d{1,2})\D{0,14}?註銷\D{0,8}?([\d,]+)仟單位\D{0,8}?剩餘\D{0,8}?([\d,]+)仟單位')
_CANCEL_ANY = re.compile(
    r'(\d{4})/(\d{1,2})/(\d{1,2})\D{0,14}?註銷\D{0,8}?([\d,]+)仟單位')
# 增額上市會讓流通量增加，有它就不能單純用「發行量減註銷量」
_ADD = re.compile(
    r'(\d{4})/(\d{1,2})/(\d{1,2})\D{0,10}?增額\D{0,10}?([\d,]+)仟單位')

# 除權息調整，新舊兩種措辭
_ADJ = [
    re.compile(r'(\d{4})/(\d{1,2})/(\d{1,2})\D{0,20}?除([權息、]{1,3})\D{0,10}?'
               r'調整後履約價格([\d,.]+)元\D{0,12}?調整後行使比例([\d.]+)'),
    re.compile(r'(\d{4})/(\d{1,2})/(\d{1,2})\D{0,20}?除([權息、]{1,3})\D{0,10}?'
               r'新履約價[格]?([\d,.]+)元\D{0,12}?新行使比例([\d.]+)'),
]


def _num(s):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _date(y, m, d):
    return "%s-%02d-%02d" % (y, int(m), int(d))


def normalize(note):
    return _WS.sub("", note or "")


def outstanding(note, issued_units):
    """回傳 (流通在外張數, 來源)，判斷不了就回 (None, None)。

    來源分三種，可信度由高到低：
      NOTE_LEFT  交易所自己載明的剩餘量，直接採用，不必自行計算
      NOTE_CALC  只給註銷量，且確定沒有增額，才用發行量相減
      ISSUED     從未註銷也未增額，流通量就等於發行量
    """
    t = normalize(note)
    if not issued_units or issued_units <= 0:
        return (None, None)

    left_events = _CANCEL_LEFT.findall(t)
    if left_events:
        # 備註裡的日期未必照時序排；同一天有多筆時，剩餘量較小的是較晚那筆
        left_events.sort(key=lambda e: (_date(e[0], e[1], e[2]), -(_num(e[4]) or 0)))
        v = _num(left_events[-1][4])
        if v is not None and 0 <= v <= issued_units:
            return (v, "NOTE_LEFT")
        return (None, None)

    has_add = bool(_ADD.search(t))
    cancels = _CANCEL_ANY.findall(t)
    if cancels and not has_add:
        v = issued_units - sum(_num(x[3]) or 0 for x in cancels)
        if 0 <= v <= issued_units:
            return (v, "NOTE_CALC")
        return (None, None)
    if not cancels and not has_add:
        return (issued_units, "ISSUED")
    return (None, None)


def adjustments(note):
    """回傳除權息造成的履約價／行使比例調整紀錄。

    [(日期, 類別, 履約價, 行使比例), ...] 依日期排序。這是把歷史隱波算回去
    的關鍵——要算某檔八月的隱波，就得知道八月當時的履約價，不是今天的。
    """
    t = normalize(note)
    out = {}
    for rx in _ADJ:
        for y, m, d, kind, strike, ratio in rx.findall(t):
            k, r = _num(strike), _num(ratio)
            if k is None or r is None or k <= 0 or r <= 0:
                continue
            out[_date(y, m, d)] = ("除" + kind, k, r)
    return [(d, v[0], v[1], v[2]) for d, v in sorted(out.items())]
