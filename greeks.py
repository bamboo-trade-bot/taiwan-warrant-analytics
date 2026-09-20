# -*- coding: utf-8 -*-
"""Black-Scholes 定價與隱含波動率反解（含台灣權證的行使比例處理）。

台股權證報價是「一單位權證」的價格，而一單位權證只能換到 ratio 股標的，
所以  權證價 = 行使比例 x BS理論價。反解 IV 前必須先把權證價除以行使比例。
"""
import math

SQRT2 = math.sqrt(2.0)
SQRT2PI = math.sqrt(2.0 * math.pi)

# 上下限型（牛熊證）是障礙式選擇權，BS 會高估其價值，計算時標記排除
BARRIER_HINTS = ("上限型", "下限型", "牛證", "熊證")


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x):
    return math.exp(-0.5 * x * x) / SQRT2PI


def bs_price(S, K, T, sigma, r=0.016, q=0.0, kind="CALL"):
    """一股標的的歐式選擇權理論價。"""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        # 到期或無波動時退化為內含價值
        intrinsic = (S - K) if kind == "CALL" else (K - S)
        return max(intrinsic, 0.0)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    df_q = math.exp(-q * T)
    df_r = math.exp(-r * T)
    if kind == "CALL":
        return S * df_q * norm_cdf(d1) - K * df_r * norm_cdf(d2)
    return K * df_r * norm_cdf(-d2) - S * df_q * norm_cdf(-d1)


def bs_greeks(S, K, T, sigma, r=0.016, q=0.0, kind="CALL"):
    """回傳 (delta, gamma, vega, theta_per_day)，皆以「一股標的」為單位。"""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    st = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / st
    d2 = d1 - st
    df_q = math.exp(-q * T)
    df_r = math.exp(-r * T)
    pdf = norm_pdf(d1)

    gamma = df_q * pdf / (S * st)
    vega = S * df_q * pdf * math.sqrt(T) / 100.0          # 每 1% 波動率
    common = -(S * df_q * pdf * sigma) / (2.0 * math.sqrt(T))
    if kind == "CALL":
        delta = df_q * norm_cdf(d1)
        theta = common - r * K * df_r * norm_cdf(d2) + q * S * df_q * norm_cdf(d1)
    else:
        delta = -df_q * norm_cdf(-d1)
        theta = common + r * K * df_r * norm_cdf(-d2) - q * S * df_q * norm_cdf(-d1)
    return (delta, gamma, vega, theta / 365.0)


def implied_vol(price, S, K, T, r=0.016, q=0.0, kind="CALL",
                lo=1e-4, hi=5.0, tol=1e-6, max_iter=100):
    """用二分法反解 IV。price 為「一股標的」的選擇權價（權證價 / 行使比例）。"""
    if price is None or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max((S - K) if kind == "CALL" else (K - S), 0.0)
    if price < intrinsic * math.exp(-r * T) - 1e-9:
        return None                      # 價格低於內含價值，無解（多半是流動性斷層）
    f_lo = bs_price(S, K, T, lo, r, q, kind) - price
    f_hi = bs_price(S, K, T, hi, r, q, kind) - price
    if f_lo > 0:
        return None                      # 低於理論下界
    if f_hi < 0:
        return None                      # IV 超過 500%，視為異常報價
    a, b = lo, hi
    for _ in range(max_iter):
        m = 0.5 * (a + b)
        fm = bs_price(S, K, T, m, r, q, kind) - price
        if abs(fm) < tol or (b - a) < tol:
            return m
        if fm > 0:
            b = m
        else:
            a = m
    return 0.5 * (a + b)


def is_barrier(category):
    """牛熊證 / 上下限型：BS 不適用。"""
    c = category or ""
    return any(h in c for h in BARRIER_HINTS)


def analyse(warrant_price, S, K, T, ratio, kind="CALL", r=0.016, q=0.0):
    """單檔權證的完整指標。warrant_price 是市場上的權證報價。

    回傳 dict：iv, delta, gamma, vega, theta（皆已乘上行使比例，
    即「每一單位權證」的敏感度）、leverage 實質槓桿、premium_pct 溢價比。
    """
    out = dict(iv=None, delta=None, gamma=None, vega=None, theta=None,
               leverage=None, premium_pct=None, moneyness=None)
    if not all(x is not None for x in (warrant_price, S, K, T, ratio)):
        return out
    if warrant_price <= 0 or S <= 0 or K <= 0 or ratio <= 0:
        return out

    out["moneyness"] = (S / K) if kind == "CALL" else (K / S)

    # 溢價比：還要漲/跌多少%，到期才損益兩平
    unit = warrant_price / ratio
    if kind == "CALL":
        out["premium_pct"] = (K + unit - S) / S
    else:
        out["premium_pct"] = (S + unit - K) / S

    iv = implied_vol(unit, S, K, T, r, q, kind)
    if iv is None:
        return out
    out["iv"] = iv

    delta, gamma, vega, theta = bs_greeks(S, K, T, iv, r, q, kind)
    out["delta"] = delta * ratio
    out["gamma"] = gamma * ratio
    out["vega"] = vega * ratio
    out["theta"] = theta * ratio
    out["leverage"] = out["delta"] * S / warrant_price
    return out
