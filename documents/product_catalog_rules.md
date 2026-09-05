# Product Catalog Rules

## PCT-01: Product Identification
Every product has a unique product_id and SKU. A product may be carried at multiple stores, but each product-store combination is tracked independently for inventory and metrics.

## PCT-02: SKU Naming Convention
SKUs follow the pattern CATEGORY-PREFIX-#### (for example BEV-COLA-001). Product names are human-readable descriptions including brand, type, variant, and size.

## PCT-03: Price and Cost Integrity
The selling price must never be below unit cost; the gross margin is selling price minus unit cost. Minimum observed markup in the catalogue is 40%.

## PCT-04: Shelf Life Handling
Shelf life is expressed in days. Items with shelf life above 60 days are eligible for inter-store transfer. Perishable items must retain at least 70% of remaining shelf life at a destination store.

## PCT-05: Category Assignment
Each product belongs to exactly one category. Categories include Beverages, Snacks, Personal Care, Household, Electronics Accessories, Stationery, Grocery, Beauty, Kitchen, Fitness, Baby Care, and Seasonal.

## PCT-06: Reorder Defaults
Each product defines a default reorder point (average daily demand × lead time × 1.3) and default safety stock (average daily demand × lead time × 0.3) used when no per-store record exists.

## PCT-07: Unknown Product
If a query references a product that does not exist in the catalogue, the system must state that the product cannot be found and must not guess a substitute.

## PCT-08: Ambiguous Product
If a query matches multiple products (for example "apple" matching different product records), the system must ask for clarification rather than pick one arbitrarily.