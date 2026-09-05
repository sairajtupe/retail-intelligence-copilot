# Data Definitions

This document defines every metric used by the analytics engine. These definitions are authoritative; the LLM must never recompute or contradict them.

## DEF-01: Average Daily Sales (ADS)
ADS = units sold over a window ÷ number of days in that window. The engine reports ADS_7D (7-day window) and ADS_30D (30-day window).

## DEF-02: Days of Supply / Cover
cover_days = current stock ÷ ADS_30D. A higher value means more days until stock-out at the current run rate.

## DEF-03: Lead Time Demand
lead_time_demand = ADS_30D × supplier lead_time_days. This is the expected consumption during a replenishment cycle.

## DEF-04: Reorder Point
reorder_point = lead_time_demand + safety_stock. When closing stock reaches this level, a replenishment order should be placed.

## DEF-05: Safety Stock
safety_stock = ADS_30D × lead_time_days × 0.3, with a floor of 5 units, unless a per-store value is recorded.

## DEF-06: Stock-Out Risk Score
risk_score is 0.0–1.0 and is derived from cover_days relative to lead time, adjusted upward when demand is accelerating. Conservative when data is thin.

## DEF-07: Risk Classification
CRITICAL cover ≤ lead time; HIGH cover ≤ lead time + 3 days; MEDIUM cover ≤ lead time × 1.5; LOW otherwise; STOCKED_OUT when stock ≤ 0; INSUFFICIENT_HISTORY when the pair has fewer than 21 days of history.

## DEF-08: Demand Trend
demand_trend_pct = (ADS_7D − ADS_30D) ÷ ADS_30D × 100. Positive means accelerating demand.

## DEF-09: Inventory Turnover (annualised)
turnover = COGS over the trailing 30 days × 12 ÷ average inventory value at cost over the trailing 30 days.

## DEF-10: Sell-Through Rate
sell_through = units_sold ÷ (units_sold + closing_stock) over the window. Higher is healthier.

## DEF-11: Gross Margin and Margin %
gross_margin = revenue − cost. margin_pct = gross_margin ÷ revenue × 100.

## DEF-12: Slow Moving
Slow moving when units_sold(30d) < 10% of current stock AND cover_days > 90.

## DEF-13: Dead Stock
Dead stock when units_sold(30d) = 0 AND current stock > minimum order quantity.

## DEF-14: Overstock
Overstock when cover_days > 90 AND units_sold(30d) > 0 AND units_sold(30d) < 10% of current stock.

## DEF-15: Promotional Lifts
Sales spikes that overlap a promotion window in the promotions table are classified as promotion-driven, not as natural anomalies.

## DEF-16: History Requirement
All risk, anomaly, recommendation, and transfer calculations require at least 21 days of sales history for the product-store combination.