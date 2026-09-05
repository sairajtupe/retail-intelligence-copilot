# Sales Anomaly and Promotion Policy

## ANM-D-01: Spike Detection
A sales spike is defined as a period-over-period increase in daily sales exceeding 2 standard deviations above the 28-day rolling average. The comparison period must have at least 7 days of data.

## ANM-D-02: Drop Detection
A sales drop is defined as a period-over-period decrease exceeding 40% relative to the prior 14-day baseline. At least 14 days of prior history is required.

## ANM-D-03: New Product Exclusion
Products with fewer than 21 days of sales history are excluded from automatic anomaly detection and flagged as INSUFFICIENT_HISTORY rather than as anomalies.

## ANM-D-04: Promotion Differentiation
When a sales spike coincides with a known promotion period, attribute the spike to the promotion and do not flag it as an unusual anomaly unless the spike exceeds 500% of baseline. Always state whether the change is promotion-driven or natural.

## ANM-D-05: Data Quality Suppression
If more than 10% of the data points in the detection window are null or zero, suppress the anomaly classification and return INSUFFICIENT_DATA instead.

## ANM-D-06: Investigation Threshold
Any drop exceeding 60% is classified as HIGH severity regardless of statistical confidence, as this may indicate a supply or data integrity issue.

## PRM-01: Promotion Eligibility
Products with stock cover days between 14 and 45 are eligible for promotional activity. Products below 14 days of cover should not be promoted to avoid accelerating stock-outs.

## PRM-02: Post-Promotion Demand
After a promotion ends, demand typically returns to baseline. Replenishment plans should pre-position stock at 150% of projected promotional demand and not mistake the post-promotion dip for a structural sales drop.

## PRM-03: Promotion Effectiveness
After a promotion ends, compare the incremental revenue (promotion-period revenue minus baseline expected revenue) against the margin reduction. A promotion is effective if incremental revenue exceeds the margin loss by at least 20%.

## PRM-04: Recency Constraint
The same product should not be promoted more than 4 times per 12-month period, and promotions should be spaced at least 45 days apart to avoid consumer fatigue.