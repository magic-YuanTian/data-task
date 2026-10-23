# Data dictionary - Brazilian e-commerce (world.duckdb)

All timestamps are as published, without a time zone (Brazilian local time). Money is in Brazilian reais (R$).
The marketplace records cover orders purchased from September 4, 2016 to October 17, 2018; few orders are recorded
before January 2017, none in November 2016, and the records thin out after August 29, 2018. Spatial columns hold WKT
text (longitude latitude, SIRGAS 2000, within a meter of WGS 84); load the DuckDB `spatial` extension and use
`ST_GeomFromText` to work with them. Identifiers are anonymized hashes. ZIP code prefixes are the first five digits of
the Brazilian CEP stored as integers, so prefixes that start with 0 (São Paulo state) have four digits.

## orders
One row per order. An order can have several items from several sellers (dataset description, catalog/documents.json).

| Column | Type | Description |
|---|---|---|
| order_id | VARCHAR | Order identifier. |
| customer_id | VARCHAR | Customer key of this order; every order has its own customer_id (see customers). |
| order_status | VARCHAR | delivered, shipped, canceled, unavailable, invoiced, processing, created or approved. |
| order_purchase_timestamp | TIMESTAMP | Time of purchase. |
| order_approved_at | TIMESTAMP | Time the payment was approved. |
| order_delivered_carrier_date | TIMESTAMP | Time the order was handed to the logistics partner. |
| order_delivered_customer_date | TIMESTAMP | Time the order was delivered to the customer. |
| order_estimated_delivery_date | TIMESTAMP | Delivery date promised to the customer at purchase (always midnight). |

## order_items
One row per item of an order. The same product can be sold by several sellers.

| Column | Type | Description |
|---|---|---|
| order_id | VARCHAR | Order. |
| order_item_id | INTEGER | Item number within the order, 1, 2, 3 and so on. |
| product_id | VARCHAR | Product. |
| seller_id | VARCHAR | Seller who fulfills the item. |
| shipping_limit_date | TIMESTAMP | Date by which the seller must hand the item to the logistics partner. |
| price | DOUBLE | Item price. |
| freight_value | DOUBLE | Freight charged for the item (an order's freight is split over its items). |

## order_payments
One row per payment of an order. An order can be paid in several parts, for example a voucher and a card.

| Column | Type | Description |
|---|---|---|
| order_id | VARCHAR | Order. |
| payment_sequential | INTEGER | Number of the payment within the order. |
| payment_type | VARCHAR | credit_card, boleto (bank slip), voucher, debit_card or not_defined. |
| payment_installments | INTEGER | Number of installments chosen by the customer. |
| payment_value | DOUBLE | Amount paid. |

## order_reviews
One row per review and order. A customer gets a satisfaction survey when the order is delivered or when the
promised date is due (dataset description). One review can cover several orders.

| Column | Type | Description |
|---|---|---|
| review_id | VARCHAR | Review identifier; it repeats when one review covers several orders. |
| order_id | VARCHAR | Order. |
| review_score | INTEGER | Score from 1 to 5. |
| review_comment_title | VARCHAR | Title written by the customer, in Portuguese (often empty). |
| review_comment_message | VARCHAR | Comment written by the customer, in Portuguese; names of stores and partners were replaced by names of Game of Thrones great houses. |
| review_creation_date | TIMESTAMP | Day the survey was sent, at midnight (at 01:00 on October 16, 2016 and October 15, 2017, the days daylight saving time began and midnight was skipped). |
| review_answer_timestamp | TIMESTAMP | Time the customer answered. |

## customers
One row per order customer key.

| Column | Type | Description |
|---|---|---|
| customer_id | VARCHAR | Key used by orders; one per order. |
| customer_unique_id | VARCHAR | Identifier of the person; it is the same for all orders of one customer. |
| customer_zip_code_prefix | INTEGER | First five digits of the customer's ZIP code (CEP). |
| customer_city | VARCHAR | City, lower case and without accents. |
| customer_state | VARCHAR | State (UF) abbreviation. |

## sellers
One row per seller with at least one item in order_items.

| Column | Type | Description |
|---|---|---|
| seller_id | VARCHAR | Seller. |
| seller_zip_code_prefix | INTEGER | First five digits of the seller's ZIP code. |
| seller_city | VARCHAR | City as typed by the seller (not cleaned: some values carry the state, a slash or an e-mail address). |
| seller_state | VARCHAR | State abbreviation. |

## products
One row per product sold in order_items.

| Column | Type | Description |
|---|---|---|
| product_id | VARCHAR | Product. |
| product_category_name | VARCHAR | Category, in Portuguese; NULL for 610 products. |
| product_name_lenght | DOUBLE | Number of characters of the product name (column name misspelled in the source). |
| product_description_lenght | DOUBLE | Number of characters of the product description (misspelled in the source). |
| product_photos_qty | DOUBLE | Number of product photos. |
| product_weight_g | DOUBLE | Weight in grams. |
| product_length_cm, product_height_cm, product_width_cm | DOUBLE | Package dimensions in centimeters. |

## product_category_name_translation
English names of 71 of the 73 product categories (pc_gamer and portateis_cozinha_e_preparadores_de_alimentos have none).

| Column | Type | Description |
|---|---|---|
| product_category_name | VARCHAR | Category in Portuguese. |
| product_category_name_english | VARCHAR | Category in English. |

## geolocation
Points of Brazilian ZIP codes, as published: on average 53 points per ZIP code prefix, with exact duplicates, city
names spelled in several ways (with and without accents) and a few points outside Brazil.

| Column | Type | Description |
|---|---|---|
| geolocation_zip_code_prefix | INTEGER | First five digits of a ZIP code. |
| geolocation_lat, geolocation_lng | DOUBLE | Latitude and longitude of a point in that ZIP code. |
| geolocation_city | VARCHAR | City. |
| geolocation_state | VARCHAR | State abbreviation. |

## zip_prefixes
Derived from geolocation: one row per ZIP code prefix.

| Column | Type | Description |
|---|---|---|
| zip_code_prefix | INTEGER | ZIP code prefix. |
| n_points | BIGINT | Rows of geolocation for the prefix. |
| lat, lon | DOUBLE | Median latitude and longitude of its points. |
| city | VARCHAR | Most frequent city name of its points. |
| state | VARCHAR | Most frequent state of its points. |
| municipality_code | INTEGER | IBGE municipality that contains the median point; NULL when it falls in no municipality. |
| in_state | BOOLEAN | True when that municipality lies in the prefix's state. |

## leads_qualified
Marketing qualified leads (MQLs): sellers who asked to sell on the marketplace, a random sample of the leads of
June 2017 to June 2018 (funnel description).

| Column | Type | Description |
|---|---|---|
| mql_id | VARCHAR | Lead. |
| first_contact_date | DATE | Day the lead signed up on a landing page. |
| landing_page_id | VARCHAR | Landing page. |
| origin | VARCHAR | Marketing channel the lead came from (organic_search, paid_search, social, direct_traffic, email, referral, display, other_publicities, other, unknown). |

## leads_closed
Leads that became sellers. seller_id joins to sellers and order_items.

| Column | Type | Description |
|---|---|---|
| mql_id | VARCHAR | Lead. |
| seller_id | VARCHAR | Seller account created for the lead. |
| sdr_id | VARCHAR | Sales development representative who contacted the lead. |
| sr_id | VARCHAR | Sales representative who closed the deal. |
| won_date | TIMESTAMP | Time the deal was closed. |
| business_segment | VARCHAR | Segment of the seller's business. |
| lead_type | VARCHAR | Kind of business (online_big, online_medium, online_small, online_beginner, online_top, industry, offline, other). |
| lead_behaviour_profile | VARCHAR | Behaviour profile assigned by the sales team (cat, eagle, wolf, shark, or two of them). |
| has_company | INTEGER | 1 if the lead has a company registration, 0 if not; mostly NULL. |
| has_gtin | INTEGER | 1 if the products have GTIN barcodes; mostly NULL. |
| average_stock | VARCHAR | Declared stock range; mostly NULL. |
| business_type | VARCHAR | reseller, manufacturer or other. |
| declared_product_catalog_size | DOUBLE | Declared number of products; mostly NULL. |
| declared_monthly_revenue | DOUBLE | Declared monthly revenue in R$; 0 for most leads. |

## states
The 27 federative units (26 states and the Federal District).

| Column | Type | Description |
|---|---|---|
| uf_code | INTEGER | IBGE code of the state. |
| uf | VARCHAR | Abbreviation, as in customer_state and seller_state. |
| name | VARCHAR | Name. |
| region | VARCHAR | Major region: Norte, Nordeste, Sudeste, Sul, Centro-Oeste. |
| population_2018 | BIGINT | Estimated resident population on July 1, 2018. |
| population_note | VARCHAR | IBGE note on the estimate (border changes between states). |
| area_km2 | DOUBLE | Area of the polygon on the ellipsoid, in km². |
| state_geom | VARCHAR | Polygon (WKT). |

## municipalities
The 5,570 municipalities of the 2018 mesh, plus two lagoons the mesh lists as units (Lagoa Mirim and Lagoa dos Patos, RS).

| Column | Type | Description |
|---|---|---|
| municipality_code | INTEGER | IBGE seven-digit code; the first two digits are the state code. |
| name | VARCHAR | Name, with accents. |
| uf | VARCHAR | State abbreviation. |
| population_2018 | BIGINT | Estimated population on July 1, 2018; NULL for the lagoons. |
| population_note | VARCHAR | IBGE note for 14 municipalities whose population is set by a court decision. |
| area_km2 | DOUBLE | Area in km². |
| centroid_lat, centroid_lon | DOUBLE | Centroid of the polygon. |
| municipality_geom | VARCHAR | Polygon (WKT). |

## municipal_gdp
IBGE municipal GDP, 2010-2017, one row per municipality and year (5,565 municipalities up to 2012, 5,570 from 2013).
Values in R$ 1,000 at current prices; gdp_per_capita in R$, computed by IBGE with the population estimate of July 1.
The region columns follow IBGE's regional divisions (mesoregion and microregion, immediate and intermediate
geographic regions, urban concentrations, population arrangements, urban hierarchy, rural regions); legal_amazon,
semiarid and sao_paulo_city_region are Sim (yes) or Não (no).

| Column | Type | Description |
|---|---|---|
| year | BIGINT | Year. |
| municipality_code, municipality, uf, uf_code, uf_name, region_code, region | | Municipality and its state and major region. |
| va_agriculture, va_industry, va_services, va_public_administration | DOUBLE | Gross value added by sector (services without public administration). |
| va_total | DOUBLE | Total gross value added. |
| taxes_on_products | DOUBLE | Taxes on products, net of subsidies. |
| gdp | DOUBLE | Gross domestic product. |
| gdp_per_capita | DOUBLE | GDP per inhabitant, in R$. |
| top_activity_1, top_activity_2, top_activity_3 | VARCHAR | Activities with the largest, second and third largest value added. |
| metropolitan_region | VARCHAR | Metropolitan region or integrated development region (RIDE) the municipality belongs to, if any. |
| mesoregion_code, mesoregion | | IBGE mesoregion (geographic division used until 2017). |
| microregion_code, microregion | | IBGE microregion. |
| immediate_region_code, immediate_region | | Immediate geographic region (division of 2017). |
| immediate_region_role | VARCHAR | Polo if the municipality is the center of its immediate region, do Entorno otherwise. |
| intermediate_region_code, intermediate_region | | Intermediate geographic region (division of 2017). |
| intermediate_region_role | VARCHAR | Polo or do Entorno, as for the immediate region. |
| urban_concentration_code, urban_concentration | | Urban concentration (large or medium city or population arrangement), if any. |
| urban_concentration_type | VARCHAR | Kind of urban concentration and the municipality's place in it. |
| population_arrangement_code, population_arrangement | | Population arrangement (municipalities forming one urban area), if any. |
| urban_hierarchy, urban_hierarchy_main | VARCHAR | Place in the urban hierarchy (REGIC), detailed and main categories. |
| rural_region_code, rural_region, rural_region_class | | Rural region and its class. |
| legal_amazon | VARCHAR | Sim if the municipality is in the Legal Amazon. |
| semiarid | VARCHAR | Sim if the municipality is in the semiarid region. |
| sao_paulo_city_region | VARCHAR | Sim if the municipality is in the São Paulo city-region. |

## exchange_rates
Banco Central do Brasil series 1: US dollar, selling rate, business days 2016-2018.

| Column | Type | Description |
|---|---|---|
| date | DATE | Business day. |
| usd_brl | DOUBLE | Reais per US dollar. |

## ipca
Banco Central do Brasil series 433: monthly change of the IPCA consumer price index, 2016-2018.

| Column | Type | Description |
|---|---|---|
| month | DATE | First day of the month. |
| change_pct | DOUBLE | Change of the index over the month, in %. |

## calendar
National holidays, secondary holidays (Carnival Tuesday, Ash Wednesday, Good Friday, Corpus Christi), observances
(Mother's Day, Father's Day, Dia dos Namorados, Children's Day, Festas Juninas) and Black Friday, 2016-2018.

| Column | Type | Description |
|---|---|---|
| date | DATE | Day. |
| name | VARCHAR | Name. |
| kind | VARCHAR | national holiday, secondary holiday, observance or shopping event. |
