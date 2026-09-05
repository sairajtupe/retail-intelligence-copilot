# Recommendation Policy

## REC-01: Issue-Led Recommendations
Every recommendation must be tied to a detected issue type: STOCKOUT_RISK, STOCKED_OUT, OVERSTOCK, SLOW_MOVING, DEAD_STOCK, SALES_SPIKE, SALES_DROP, PROMO_OVERLAP, DATA_QUALITY_ISSUE, TRANSFER_OPPORTUNITY, or INSUFFICIENT_HISTORY.

## REC-02: Actionable Content
Each recommendation must contain: recommended action (what to do), reason (why), metrics (the actual numbers), assumptions (what was assumed), source data (where the numbers came from), and a policy citation.

## REC-03: Evidence Requirement
No recommendation may be issued without supporting figures. Every claim must reference either a calculated metric from the analytics engine or a retrieved policy document.

## REC-04: Reorder Sizing
The reorder quantity equals max(minimum order quantity, target stock level − current stock), where target stock level = target stock days × average daily demand.

## REC-05: Confidence Rules
Confidence is HIGH only when history ≥ 21 days, data quality is clean, and the signal magnitude is large. Confidence is MEDIUM or LOW otherwise. Never assign HIGH confidence to a product with fewer than 21 days of history.

## REC-06: No-Invented-Issues
If no issue passes the configured thresholds in the requested scope, the recommendation engine must return an empty issue list with a clear "no high-priority issues found" statement. Inventing an issue is a hard failure.

## REC-07: Severity Ordering
Attention items are ordered by severity: CRITICAL, then HIGH, then MEDIUM. Within the same severity, sort by risk score descending, then by financial impact. LOW severity items are only surfaced when explicitly requested.

## REC-08: Suppression Rules
Recommendations are suppressed when: the product-store has insufficient history (< 21 days), the record has a data quality issue (negative closing stock), or the product is not carried at the store.