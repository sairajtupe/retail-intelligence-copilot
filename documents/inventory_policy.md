# Inventory Management Policy

## INV-R-01: Reorder Point Calculation
The reorder point equals (average daily demand × supplier lead time in days) + safety stock. When current stock falls to or below this point, a replenishment order should be placed immediately.

## INV-R-02: Safety Stock Thresholds
Safety stock should be set to cover 50% of the lead time demand variance. Minimum safety stock is 5 units for any stocked item.

## INV-R-03: Maximum Stock Level
No individual SKU should exceed the target stock days multiplied by average daily demand by more than 150%. Anything beyond this triggers an overstock review.

## INV-R-04: Cycle Count Frequency
High-value items (unit cost > $100) require weekly cycle counts. Standard items require monthly counts.

## INV-R-05: New Product Stocking
New products launched within the last 30 days should be stocked at minimum order quantity only, with no automatic reordering until demand history exceeds 30 data points.

## INV-R-06: Seasonal Adjustments
For seasonal categories, reorder points must be adjusted using the seasonal demand multiplier for the relevant month, applied to the base average daily demand calculation.

## INV-R-07: Shrinkage Allowance
Expected shrinkage should not exceed 2% of throughput. When shrinkage exceeds this threshold, investigation is required before further replenishment is approved.
