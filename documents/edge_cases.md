# Edge Cases and Data Quality Policy

## EDG-01: Insufficient History
When fewer than 21 days of sales data are available for a product-store combination, do not compute risk scores, anomaly classifications, or transfer recommendations. Return INSUFFICIENT_HISTORY.

## EDG-02: Contradictory Inventory
If closing stock is calculated as negative after accounting for sales, receipts, and adjustments, flag a DATA_QUALITY_ISSUE. Do not generate confidence-based recommendations from records with negative stock.

## EDG-03: Null or Missing Values
When critical fields (units_sold, unit_price, stock) are null, exclude the affected records from calculations and note the data gap in the response. If the gap exceeds 20% of the window, return INSUFFICIENT_DATA.

## EDG-04: Zero Sales
Zero units sold in a period is not automatically anomalous. It is only flagged as slow-moving or dead stock when combined with existing inventory levels exceeding thresholds defined in the slow-moving policy.

## EDG-05: Unanswerable Questions
When a question refers to data not present in the system (e.g., future predictions, competitor data, non-retail topics), explicitly state that the system cannot answer and explain what information is missing.

## EDG-06: Contradictory Sales-Inventory Match
When units sold in the sales table exceed the opening stock minus closing stock in the inventory table for the same product-store-date, flag as a data integrity issue. Investigate before acting on recommendations.

## EDG-07: Confidence Calibration
Never assign HIGH confidence to a recommendation that relies on data with more than 15% null records, or to a product with fewer than 21 days of history.
