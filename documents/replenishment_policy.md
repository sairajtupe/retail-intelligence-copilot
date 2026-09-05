# Replenishment and Stock-Out Policy

## REP-A-01: Automatic Replenishment Trigger
Automatic replenishment should trigger when closing stock reaches the reorder point and no outstanding purchase order exists for the product-store combination.

## REP-A-02: Order Quantity
Order quantity should cover enough units to reach the target stock level (target stock days × average daily demand) minus current stock, subject to the minimum order quantity constraint.

## REP-A-03: Lead Time Handling
When supplier lead time exceeds 14 days, the replenishment order must be placed at 150% of the normal trigger point to buffer against extended delivery windows.

## REP-A-04: Supplier Reliability
If a supplier has delivered late on more than 20% of orders in the past 90 days, add 5 business days to their stated lead time for planning purposes.

## REP-A-05: Minimum Order Commitments
Orders must meet the supplier's minimum order quantity. If the calculated replenishment quantity falls below this threshold, consider combining with another product from the same supplier.

## REP-A-06: Urgent Replenishment
When stock cover days fall below the supplier lead time with no incoming purchase order, this is classified as an urgent replenishment need and should be escalated.

## STK-01: Risk Classification
Stock-out risk is classified as: CRITICAL (days of cover ≤ lead time), HIGH (days of cover ≤ lead time + 3 days), MEDIUM (days of cover ≤ lead time × 1.5), LOW (days of cover > lead time × 1.5).

## STK-02: Demand Trend Adjustment
When the 7-day rolling average demand exceeds the 30-day rolling average by more than 20%, the risk classification should be elevated by one level, as demand is accelerating.

## STK-03: Incoming PO Offset
If a purchase order is scheduled to arrive before the calculated stock-out date and covers the projected deficit, reduce the risk classification accordingly.

## STK-04: Minimum Coverage
All products currently in stock should have a minimum of 3 days of cover. Products below this threshold with no incoming order are automatically CRITICAL.

## STK-05: Newly Launched Products
Products launched within the last 21 days are classified as INSUFFICIENT_HISTORY, not as CRITICAL, to allow time for demand pattern stabilisation.

## STK-06: Zero-Stock Items
Products with zero current stock and no incoming purchase order are classified as STOCKED_OUT and require immediate action.
