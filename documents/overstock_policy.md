# Overstock Policy

## OVR-01: Overstock Threshold
A product-store combination is overstocked when days of cover exceeds 90 days, there is recent demand (units sold in the trailing 30 days > 0), and units sold in the trailing 30 days is less than 10% of current stock.

## OVR-02: Excess Calculation
Excess units equal current stock minus (target stock days × average daily demand), floored at zero. Report excess inventory value at cost for financial impact.

## OVR-03: Markdown Trigger
A product should be marked down when cover days exceed 90 days and the sales trend is negative (declining by more than 15% over the past 30 days versus the prior 30 days).

## OVR-04: Markdown Depth
The initial markdown should be 15-20% off selling price. If the item remains slow-moving for another 30 days, a second markdown of 25-35% may be applied.

## OVR-05: Margin Floor
No markdown should reduce the gross margin percentage below 5%. Products approaching this floor should be considered for liquidation or donation rather than further discounting.

## OVR-06: Promotion Timing
Markdowns should not overlap with planned promotions on similar products in the same category, to avoid internal cannibalisation.

## OVR-07: Conflicting Signal Handling
When a product has high inventory but also high recent demand, treat this as a conflicting signal. Explain both facts, verify whether the high demand is promotional, and do not blindly label the item as overstocked.