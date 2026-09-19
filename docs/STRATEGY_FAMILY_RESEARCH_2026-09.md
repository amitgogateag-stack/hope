# HOPE Strategy Family Research — 2026-09

This note defines research priorities only. It does not promote, activate, optimize, or authorize live trading.

## Research conclusion

HOPE should enter strategy research with a balanced family set rather than ten unrelated technical rules.

Core offensive families:
- CROSS_SECTIONAL_MOMENTUM
- FIFTY_TWO_WEEK_HIGH
- TIME_SERIES_TREND

Core defensive families:
- LOW_VOLATILITY_DEFENSIVE
- QUALITY_DEFENSIVE
- MULTIFACTOR_QMV

Secondary diversification families:
- SECTOR_INDUSTRY_MOMENTUM
- SHORT_TERM_REVERSAL
- EARNINGS_DRIFT (USA first)
- VALUE_RECOVERY

## India emphasis

Indian evidence supports intermediate/long-horizon price momentum, with stronger momentum among liquid stocks. Recent research also finds a robust 52-week-high effect. NSE Indices maintains investable momentum, quality, low-volatility, and multifactor strategy indices, making these useful external benchmark families for HOPE research.

For bearish Indian equity regimes, HOPE should research long/flat trend logic, low-volatility, quality, and multifactor defensive approaches before stock-level short strategies. Indian short selling requires delivery; naked short selling is not permitted, so borrow availability and implementation mechanics must be modeled before any overnight stock-short design.

## USA emphasis

U.S. literature strongly supports conventional cross-sectional momentum, time-series trend following, low-beta/low-risk, quality, industry momentum, and post-earnings announcement drift as research families. Momentum crash literature requires explicit panic/rebound stress tests rather than treating momentum as universally safe.

## Regime intent

BULL_TREND: momentum, 52-week-high, trend, sector/industry momentum.
BEAR_TREND: trend, low-volatility, quality, multifactor defensive.
SIDEWAYS: low-volatility, quality, multifactor, selective short-term reversal and value.
RECOVERY: momentum, 52-week-high, quality, value, selective reversal.

Regime labels are evaluation slices, not automatic trading permissions.

## Literature and benchmark references

- Moskowitz, Ooi, Pedersen, "Time Series Momentum," Journal of Financial Economics, 2012.
- Daniel and Moskowitz, "Momentum Crashes," Journal of Financial Economics / NBER working-paper lineage.
- Chui, Ranganathan, Rohit, Veeraraghavan, "Momentum, reversals and liquidity: Indian evidence," Pacific-Basin Finance Journal, 2023.
- Raju, "The 52-Week High Effect and Momentum Investing: Evidence from India," SSRN, 2023.
- Medhat and Schmeling, "Short-term Momentum," Review of Financial Studies, 2022.
- Frazzini and Pedersen, "Betting Against Beta," Journal of Financial Economics / NBER.
- Asness, Frazzini, Pedersen, "Quality Minus Junk."
- Fink, "A review of the Post-Earnings-Announcement Drift," Journal of Behavioral and Experimental Finance, 2021.
- NSE Indices: Nifty200 Momentum 30, Nifty200 Quality 30, Nifty500 Low Volatility 50, and Nifty multifactor index methodologies.
- SEBI Circular SEBI/HO/MRD/MRD-PoD-3/P/CIR/2024/1, Framework for Short Selling.

## HOPE-specific research rules

Each family must still pass HOPE's predeclared control/variant protocol, PIT data requirements, execution-cost stress, regime analysis, parameter sensitivity, universe perturbation, and canonical evidence decision chain. External literature is a reason to test a family, not proof that HOPE can trade it profitably.
