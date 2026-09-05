import sqlite3
import pandas as pd

conn = sqlite3.connect('data/retail.db')

q = pd.read_sql("""
SELECT store_id, product_id, product_name, stock, units_7d, units_30d,
       avg_daily_demand_30d, cover_days, lead_time_days, risk, risk_score,
       anomaly, overstock, dead_stock, transfer_flag, data_quality, history_days
FROM metrics_snapshot
WHERE product_id IN ('P-DEMO-001','P-DEMO-002','P-DEMO-003','P-DEMO-004','P-DEMO-005',
                     'P-DEMO-006','P-DEMO-007','P-DEMO-008','P-DEMO-009','P-DEMO-010','P-DEMO-011')
ORDER BY product_id, store_id""", conn)
pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 30)
print("DEMO PRODUCTS:")
print(q.to_string())
print()

print("SPIKE count:", pd.read_sql("SELECT COUNT(*) c FROM metrics_snapshot WHERE anomaly='SPIKE'", conn).iloc[0]['c'])
print("DROP count:", pd.read_sql("SELECT COUNT(*) c FROM metrics_snapshot WHERE anomaly='DROP'", conn).iloc[0]['c'])
print("CRITICAL count:", pd.read_sql("SELECT COUNT(*) c FROM metrics_snapshot WHERE risk='CRITICAL'", conn).iloc[0]['c'])
print("HIGH count:", pd.read_sql("SELECT COUNT(*) c FROM metrics_snapshot WHERE risk='HIGH'", conn).iloc[0]['c'])
print("Transfer flag pairs:", pd.read_sql("SELECT COUNT(*) c FROM metrics_snapshot WHERE transfer_flag=1", conn).iloc[0]['c'])

print()
print("Top margins:")
print(pd.read_sql("""
SELECT product_id, product_name, margin_pct FROM metrics_snapshot
WHERE margin_30d > 0 ORDER BY margin_pct DESC LIMIT 5""", conn).to_string())