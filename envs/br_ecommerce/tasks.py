"""Tasks of the Brazilian e-commerce environment, each with its ground-truth reasoning path.

Every task is answered from the records (out/br_ecommerce/world.duckdb), from public documents of the data publishers
and of Wikipedia, or from an existing benchmark (Spider 2.0-lite, whose questions and gold results are reused). A
forecast task states the period it may use and is scored against the recorded values. No record is planted.

A task lists the lower-level tasks it builds on (depends_on); a step that reuses one names it (uses).

    python -m envs.br_ecommerce.tasks       # writes out/br_ecommerce/tasks.json
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from datataskbench import stats as S
from datataskbench.paths import repo_path
from datataskbench.spider import spider_match
from datataskbench.taskkit import TEXT, Env, TaskSet, step
from datataskbench.taskkit import tab as _tab

from .build import CFG, OUT, SOURCES, documents
from .common import hav_km

SPIDER = repo_path(CFG["source"]["spider_gold"]) / "exec_result"
DOCS = documents()
DOCS["spherical"] = dict(document="Spherical law of cosines (Spider 2.0-lite, spherical_law.md)",
                         url="https://github.com/xlang-ai/Spider2/blob/main/spider2-lite/resource/documents/spherical_law.md",
                         text=["The distance d between two cities is d = 6371 × arccos(cos(lat1) × cos(lat2) × cos(lon2 − lon1) "
                               "+ sin(lat1) × sin(lat2)), with latitudes and longitudes in radians; 6371 is the Earth's "
                               "average radius in kilometers."])
TABLES = list(SOURCES)
T = TaskSet(TABLES, SOURCES)
task = T.task
DELIVERED = "order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL"
LATE = "(CAST(order_delivered_customer_date AS DATE) > CAST(order_estimated_delivery_date AS DATE))"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def tab(df, digits=8, limit=None):
    return _tab(df, digits, limit)


def quote(key, *idx):
    d = DOCS[key]
    return dict(document=d["document"], url=d["url"], text=" ".join(d["text"][i] for i in idx))


def spider_step(df, iid, cols, gold_cols=None, note=None):
    m = spider_match(df, iid, cols, SPIDER, gold_cols)
    return step(f"Compare with the Spider 2.0 gold result ({iid})", source="benchmark",
                result={"spider_instance": iid, "matches_spider_gold": m["match"], "gold_file": m.get("variant")},
                note=note or "Question and gold come from Spider 2.0-lite; this database holds the same records.")


def daily_orders(e, start="2016-09-04", end="2018-10-17") -> pd.Series:
    d = e.q("SELECT CAST(order_purchase_timestamp AS DATE) AS day, count(*) AS n FROM orders GROUP BY 1")
    s = pd.Series(d.n.values, index=pd.to_datetime(d.day)).sort_index()
    return s.reindex(pd.date_range(start, end), fill_value=0)


def weekday_median(s: pd.Series, weeks=4) -> pd.Series:
    """Expected count: median of the same weekday over the previous weeks (robust to one-off peaks)."""
    return pd.concat([s.shift(7 * k) for k in range(1, weeks + 1)], axis=1).median(axis=1)


# ====================================================================== Q1 Definition
@task("q1.customer_ids", "Q1.1",
      "What is the difference between customers.customer_id and customers.customer_unique_id? How many people placed "
      "more than one order, and what happens if repeat customers are counted with customer_id?", TEXT)
def customer_ids(e):
    sql1 = """SELECT (SELECT count(*) FROM orders) AS orders, count(*) AS customer_rows,
  count(DISTINCT customer_id) AS customer_ids, count(DISTINCT customer_unique_id) AS people
FROM customers"""
    sql2 = """WITH p AS (SELECT c.customer_unique_id, count(*) AS orders FROM orders o JOIN customers c USING (customer_id) GROUP BY 1)
SELECT count(*) AS people, count(*) FILTER (WHERE orders > 1) AS repeat_people,
  round(count(*) FILTER (WHERE orders > 1) * 100.0 / count(*), 1) AS pct_repeat, max(orders) AS max_orders
FROM p"""
    sql3 = """SELECT count(*) FILTER (WHERE n > 1) AS customer_ids_with_several_orders
FROM (SELECT customer_id, count(*) AS n FROM orders GROUP BY 1)"""
    a, b, c = e.q(sql1).iloc[0], e.q(sql2).iloc[0], e.q(sql3).iloc[0]
    steps = [step("Count orders, customer keys and people", sql=sql1, result=tab(e.q(sql1)),
                  note="There are as many customer_id values as orders: the key is created per order."),
             step("Count the people with more than one order (customer_unique_id)", sql=sql2, result=tab(e.q(sql2))),
             step("The same count with customer_id", sql=sql3, result=tab(e.q(sql3)))]
    gold = {"key_facts": [
        f"customer_id is a key per order: there are {int(a.customer_ids):,} customer_id values for {int(a.orders):,} orders.",
        f"customer_unique_id identifies the person: {int(a.people):,} people.",
        f"{int(b.repeat_people):,} people ({b.pct_repeat}%) ordered more than once; the most frequent customer placed "
        f"{int(b.max_orders)} orders.",
        f"Counted with customer_id, no customer ever orders twice ({int(c.customer_ids_with_several_orders)} keys with several "
        "orders), so repeat purchases must be measured with customer_unique_id."], "required": [0, 1, 3]}
    resp = (f"`customer_id` is created for every order ({int(a.customer_ids):,} keys for {int(a.orders):,} orders), while "
            f"`customer_unique_id` identifies the person ({int(a.people):,} people). {int(b.repeat_people):,} people "
            f"({b.pct_repeat}%) ordered more than once, one of them {int(b.max_orders)} times. Counting repeat customers with "
            "`customer_id` would find none, so retention and lifetime measures need `customer_unique_id`.")
    return steps, gold, resp


@task("q1.zip_prefix", "Q1.1",
      "What does customers.customer_zip_code_prefix hold, why do some values have only four digits, and how well does it "
      "match the geolocation table?", TEXT)
def zip_prefix(e):
    sql1 = """SELECT length(CAST(customer_zip_code_prefix AS VARCHAR)) AS digits, customer_state, count(*) AS customers,
  min(customer_zip_code_prefix) AS lowest, max(customer_zip_code_prefix) AS highest
FROM customers GROUP BY 1, 2 HAVING digits = 4 OR customer_state IN ('SP', 'RJ') ORDER BY 1, 2"""
    sql2 = """SELECT count(*) AS prefixes, round(avg(n_points), 1) AS points_per_prefix, max(n_points) AS max_points FROM zip_prefixes"""
    sql3 = """SELECT count(DISTINCT customer_zip_code_prefix) AS prefixes_without_points, count(*) AS customers_affected
FROM customers WHERE customer_zip_code_prefix NOT IN (SELECT zip_code_prefix FROM zip_prefixes)"""
    a, b, c = e.q(sql1), e.q(sql2).iloc[0], e.q(sql3).iloc[0]
    four = a[a.digits == 4]
    steps = [step("Number of digits of the prefix, by state", sql=sql1, result=tab(a),
                  note="All four-digit prefixes are in São Paulo state, whose CEPs start with 0."),
             step("Points per prefix in geolocation", sql=sql2, result=tab(e.q(sql2))),
             step("Customer prefixes with no geolocation point", sql=sql3, result=tab(e.q(sql3)))]
    gold = {"key_facts": [
        "It is the first five digits of the customer's ZIP code (CEP), not a full address.",
        f"It is stored as an integer, so the leading zero is lost: the {int(four.customers.sum()):,} customers with a four-digit "
        "prefix are all in São Paulo state (CEPs 01000-09999); pad to five digits before joining other CEP data.",
        f"geolocation has on average {b.points_per_prefix} points per prefix (up to {int(b.max_points):,}), so join it "
        "through one point per prefix (for example zip_prefixes), not row by row.",
        f"{int(c.prefixes_without_points)} customer prefixes ({int(c.customers_affected)} customers) have no point in geolocation."],
        "required": [0, 1, 2]}
    resp = (f"`customer_zip_code_prefix` is the first five digits of the CEP. It is stored as an integer, so prefixes that start "
            f"with 0 lose it: the {int(four.customers.sum()):,} customers with four-digit values are all in São Paulo state; "
            f"pad to five digits before matching other CEP data. geolocation holds about {b.points_per_prefix} points per prefix, "
            f"so it must be reduced to one point per prefix before a join (otherwise rows multiply). "
            f"{int(c.prefixes_without_points)} customer prefixes ({int(c.customers_affected)} customers) have no point at all.")
    return steps, gold, resp


@task("q1.review_survey", "Q1.2",
      "Many reviews were created before the order was delivered. Using the dataset description, explain when a review is "
      "created, and check the explanation against the records.", TEXT)
def review_survey(e):
    sql1 = f"""SELECT CASE WHEN o.order_delivered_customer_date IS NULL THEN 'never delivered'
  WHEN CAST(r.review_creation_date AS DATE) < CAST(o.order_delivered_customer_date AS DATE) THEN 'created before delivery'
  ELSE 'created after delivery' END AS review_timing,
  count(*) AS reviews, round(avg({LATE}::INT) * 100, 1) AS pct_late, round(avg(r.review_score), 2) AS avg_score
FROM order_reviews r JOIN orders o USING (order_id) GROUP BY 1 ORDER BY 2 DESC"""
    sql2 = """WITH x AS (SELECT 'after delivery' AS reference,
  date_diff('day', CAST(o.order_delivered_customer_date AS DATE), CAST(r.review_creation_date AS DATE)) AS days, count(*) AS reviews
FROM order_reviews r JOIN orders o USING (order_id)
WHERE CAST(r.review_creation_date AS DATE) >= CAST(o.order_delivered_customer_date AS DATE) GROUP BY 1, 2
UNION ALL
SELECT 'after promised date', date_diff('day', CAST(o.order_estimated_delivery_date AS DATE), CAST(r.review_creation_date AS DATE)), count(*)
FROM order_reviews r JOIN orders o USING (order_id)
WHERE o.order_delivered_customer_date IS NULL OR CAST(r.review_creation_date AS DATE) < CAST(o.order_delivered_customer_date AS DATE)
GROUP BY 1, 2)
SELECT * FROM x QUALIFY row_number() OVER (PARTITION BY reference ORDER BY reviews DESC, days) <= 3 ORDER BY 1, 3 DESC"""
    a = e.q(sql1).set_index("review_timing")
    b = e.q(sql2)
    after = b[b.reference == "after delivery"].iloc[0]
    prom = b[b.reference == "after promised date"].iloc[0]
    tot = int(e.v("SELECT count(*) FROM order_reviews"))
    steps = [step("Read how the satisfaction survey is triggered", doc=quote("dataset", 2, 3)),
             step("Classify reviews by creation day against the delivery day", sql=sql1, result=tab(a.reset_index())),
             step("Most common lag of the survey after delivery, and after the promised day for undelivered orders",
                  sql=sql2, result=tab(b))]
    bd = a.loc["created before delivery"]
    gold = {"key_facts": [
        "A survey is sent when the order is delivered or when the promised delivery date is due, whichever comes first "
        "(dataset description); review_creation_date is the day it was sent.",
        f"Most surveys go out {int(after.days)} day after delivery ({int(after.reviews):,} of {tot:,} reviews).",
        f"{int(bd.reviews):,} reviews were created before delivery; {bd.pct_late}% of these orders arrived late: the survey "
        f"went out about {int(prom.days)} days after the promised date while the order was still on its way.",
        f"These early reviews are much worse (average {bd.avg_score}) than reviews created after delivery "
        f"({a.loc['created after delivery'].avg_score})."], "required": [0, 2]}
    resp = (f"The survey is sent when the order is delivered or when the promised date is due (dataset description). "
            f"Usually it goes out {int(after.days)} day after delivery. For {int(bd.reviews):,} orders the promised date passed "
            f"first: {bd.pct_late}% of them arrived late, and the survey went out about {int(prom.days)} days after the promised "
            f"date, before the parcel. Those reviews average {bd.avg_score} stars against {a.loc['created after delivery'].avg_score} "
            "for reviews written after delivery.")
    return steps, gold, resp


@task("q1.rfm_document", "Q1.2",
      "According to the RFM definition document, how are the R, F and M scores assigned, which customers are Champions "
      "and which are Can't Lose Them, and in which segment is a customer with R = 1, F = 5 and M = 2? Is the frequency "
      "score informative for delivered orders in this data?", TEXT)
def rfm_document(e):
    sql = f"""WITH p AS (SELECT c.customer_unique_id, count(*) AS orders FROM orders o JOIN customers c USING (customer_id)
           WHERE o.order_status = 'delivered' GROUP BY 1)
SELECT orders AS delivered_orders, count(*) AS customers, round(count(*) * 100.0 / sum(count(*)) OVER (), 2) AS pct
FROM p GROUP BY 1 ORDER BY 1 LIMIT 4"""
    a = e.q(sql)
    one = a[a.delivered_orders == 1].iloc[0]
    steps = [step("Read the scoring and the segment rules", doc=quote("rfm", 0, 1, 2, 3, 4, 5, 6, 7)),
             step("Apply the rules to R = 1, F = 5, M = 2", source="calculation",
                  result={"rfm_cell": "152", "f_plus_m": 7, "segment": "Recent Users (Recency = 1, F + M between 7 and 8)"}),
             step("Distribution of delivered orders per customer", sql=sql, result=tab(a),
                  note="Quintiles of a variable with one value for most customers split tied customers arbitrarily.")]
    gold = {"key_facts": [
        "Each customer gets R, F and M scores from 1 to 5 by percentile; 1 is best (most recent, most frequent, highest "
        "spending) and 5 is worst.",
        "Champions: Recency = 1 and Frequency + Monetary between 1 and 4.",
        "Can't Lose Them: Recency 4 or 5 and Frequency + Monetary between 1 and 2.",
        "R = 1, F = 5, M = 2 is cell 152; F + M = 7, so the customer is a Recent User.",
        f"{one.pct}% of customers have exactly one delivered order, so frequency quintiles split tied customers arbitrarily "
        "and segment results depend on the tie-break (Spider 2.0 accepts four different gold answers for its RFM task, local003)."],
        "required": [0, 1, 2, 3]}
    resp = ("Scores run from 1 to 5 by percentile, 1 being the most recent, most frequent and highest spending. Champions have "
            "R = 1 and F + M between 1 and 4; Can't Lose Them have R of 4 or 5 and F + M between 1 and 2. R = 1, F = 5, M = 2 "
            f"is cell 152 with F + M = 7, a Recent User. In this data {one.pct}% of customers have a single delivered order, so the "
            "frequency score mostly reflects how ties are broken, not behaviour.")
    return steps, gold, resp


# ====================================================================== Q2 Retrieve
@task("q2.spider_local028", "Q2.2",
      "Could you generate a report that shows the number of delivered orders for each month in the years 2016, 2017, and "
      "2018? Each column represents a year, and each row represents a month.",
      {"type": "table", "columns": ["month", "2016", "2017", "2018"]},
      scoring={"metric": "table_match", "compare_columns": [1, 2, 3]})
def local028(e):
    sql = f"""SELECT strftime(order_delivered_customer_date, '%m') AS month,
  count(*) FILTER (WHERE year(order_delivered_customer_date) = 2016) AS "2016",
  count(*) FILTER (WHERE year(order_delivered_customer_date) = 2017) AS "2017",
  count(*) FILTER (WHERE year(order_delivered_customer_date) = 2018) AS "2018"
FROM orders WHERE {DELIVERED} GROUP BY 1 ORDER BY 1"""
    sql0 = """SELECT order_status, count(*) AS orders, count(order_delivered_customer_date) AS with_delivery_date
FROM orders GROUP BY 1 ORDER BY 2 DESC"""
    a = e.q(sql)
    steps = [step("Order statuses and delivery dates", sql=sql0, result=tab(e.q(sql0)),
                  note="8 orders have status delivered but no delivery date; they cannot be placed in a month."),
             step("Count delivered orders by month and year of delivery", sql=sql, result=tab(a),
                  note="The months are those of the delivery date, as in the Spider 2.0 gold result."),
             spider_step(a, "local028", [1, 2, 3])]
    gold = {"rows": tab(a)["rows"], "columns": list(a.columns)}
    pk = a.set_index("month")["2017"].idxmax()
    resp = (f"Delivered orders by month of delivery: {int(a['2016'].sum())} in 2016 (October to December), {int(a['2017'].sum()):,} in "
            f"2017 and {int(a['2018'].sum()):,} in 2018 (to October); the peak month of 2017 is {MONTHS[int(pk) - 1]} "
            f"({int(a.set_index('month')['2017'][pk]):,}). Full table in the result.")
    return steps, gold, resp


@task("q2.spider_local029", "Q2.2",
      "Please identify the top three customers, based on their customer_unique_id, who have the highest number of delivered "
      "orders, and provide the average payment value, city, and state for each of these customers.",
      {"type": "table", "columns": ["customer_unique_id", "avg_payment_value", "customer_city", "customer_state"]},
      depends_on=["q1.customer_ids"], scoring={"metric": "table_match", "compare_columns": [0, 1, 2]})
def local029(e):
    sql = """SELECT c.customer_unique_id, count(DISTINCT o.order_id) AS delivered_orders, avg(p.payment_value) AS avg_payment_value,
  any_value(c.customer_city) AS customer_city, any_value(c.customer_state) AS customer_state
FROM customers c JOIN orders o USING (customer_id) JOIN order_payments p USING (order_id)
WHERE o.order_status = 'delivered' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3"""
    a = e.q(sql)
    out = a[["customer_unique_id", "avg_payment_value", "customer_city", "customer_state"]]
    steps = [step("Count people, not order keys", uses="q1.customer_ids"),
             step("Delivered orders and average payment (over payment rows) per person", sql=sql, result=tab(a),
                  note="The average is over payments: an order paid in two parts counts twice."),
             spider_step(out, "local029", [1, 2], [1, 2])]
    gold = {"rows": tab(out)["rows"], "columns": list(out.columns)}
    resp = "; ".join(f"{r.customer_unique_id[:8]}… ({int(r.delivered_orders)} delivered orders, average payment R$ {r.avg_payment_value:.2f}, "
                     f"{r.customer_city} {r.customer_state})" for r in a.itertuples()) + "."
    return steps, gold, resp


@task("q2.spider_local032", "Q2.2",
      "Could you help me find the sellers who excel in the following categories, considering only delivered orders: the "
      "seller with the highest number of distinct customer unique IDs, the seller with the highest profit (calculated as "
      "price minus freight value), the seller with the highest number of distinct orders, and the seller with the most "
      "5-star ratings? For each category, please provide the seller ID and the corresponding value, labeling each row "
      "with a description of the achievement.", {"type": "table", "columns": ["achievement", "seller_id", "value"]},
      depends_on=["q1.customer_ids"], scoring={"metric": "table_match", "compare_columns": [1, 2]})
def local032(e):
    base = """WITH d AS (SELECT i.*, c.customer_unique_id FROM order_items i JOIN orders o USING (order_id)
           JOIN customers c USING (customer_id) WHERE o.order_status = 'delivered')"""
    sql = base + """
SELECT * FROM (
  (SELECT 'most distinct customers' AS achievement, seller_id, count(DISTINCT customer_unique_id)::DOUBLE AS value FROM d GROUP BY 2 ORDER BY 3 DESC LIMIT 1)
  UNION ALL (SELECT 'highest profit (price - freight)', seller_id, round(sum(price - freight_value), 2) FROM d GROUP BY 2 ORDER BY 3 DESC LIMIT 1)
  UNION ALL (SELECT 'most distinct orders', seller_id, count(DISTINCT order_id) FROM d GROUP BY 2 ORDER BY 3 DESC LIMIT 1)
  UNION ALL (SELECT 'most 5-star ratings', d.seller_id, count(DISTINCT d.order_id) FROM d JOIN order_reviews r USING (order_id)
             WHERE r.review_score = 5 GROUP BY 2 ORDER BY 3 DESC LIMIT 1))"""
    naive = base + """
SELECT d.seller_id, count(*) AS five_star_item_rows, count(DISTINCT d.order_id) AS five_star_orders
FROM d JOIN order_reviews r USING (order_id) WHERE r.review_score = 5 GROUP BY 1 ORDER BY 2 DESC LIMIT 3"""
    a = e.q(sql)
    steps = [step("Count people with customer_unique_id", uses="q1.customer_ids"),
             step("Counting 5-star ratings over item rows overcounts orders with several items", sql=naive, result=tab(e.q(naive)),
                  note="Counted per item row, another seller leads; counted per order (one rating per order), the leader matches Spider."),
             step("The four achievements on delivered orders", sql=sql, result=tab(a)),
             spider_step(a, "local032", [1, 2])]
    gold = {"rows": tab(a)["rows"], "columns": list(a.columns)}
    resp = "; ".join(f"{r.achievement}: {r.seller_id[:8]}… ({r.value:,.2f})" if r.value % 1 else
                     f"{r.achievement}: {r.seller_id[:8]}… ({int(r.value):,})" for r in a.itertuples()) + "."
    return steps, gold, resp


@task("q2.spider_local037", "Q2.2",
      "Identify the top three product categories whose most commonly used payment type has the highest number of payments "
      "across all categories, and specify the number of payments made in each category using that payment type.",
      {"type": "table", "columns": ["product_category_name", "payment_type", "payment_count"]},
      scoring={"metric": "table_match", "compare_columns": [2]})
def local037(e):
    naive = """SELECT p.product_category_name, pay.payment_type, count(*) AS joined_rows
FROM order_items i JOIN products p USING (product_id) JOIN order_payments pay USING (order_id)
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 3"""
    sql = """WITH x AS (SELECT DISTINCT p.product_category_name, pay.order_id, pay.payment_sequential, pay.payment_type
           FROM order_items i JOIN products p USING (product_id) JOIN order_payments pay USING (order_id)),
     c AS (SELECT product_category_name, payment_type, count(*) AS payment_count FROM x GROUP BY 1, 2),
     top AS (SELECT product_category_name, arg_max(payment_type, payment_count) AS payment_type, max(payment_count) AS payment_count
             FROM c GROUP BY 1)
SELECT * FROM top ORDER BY payment_count DESC LIMIT 3"""
    a = e.q(sql)
    steps = [step("Joining items and payments repeats each payment once per item of the order", sql=naive, result=tab(e.q(naive)),
                  note="Orders with several items are counted several times: the counts are too high."),
             step("Count each payment once per category, take each category's most used type, rank", sql=sql, result=tab(a)),
             spider_step(a, "local037", [2])]
    gold = {"rows": tab(a)["rows"], "columns": list(a.columns)}
    resp = ("Credit card is the most used payment type in the top categories: " + ", ".join(
        f"{r.product_category_name} ({int(r.payment_count):,} payments)" for r in a.itertuples())
        + ". Each payment is counted once per category; joining items to payments directly would count it once per item.")
    return steps, gold, resp


SP_LOCAL035 = """WITH g AS (SELECT geolocation_city AS city, geolocation_lat AS lat, geolocation_lng AS lng,
         row_number() OVER (ORDER BY geolocation_state, geolocation_city, geolocation_zip_code_prefix, geolocation_lat, geolocation_lng) AS rn
       FROM geolocation),
     d AS (SELECT lag(city) OVER (ORDER BY rn) AS prev_city, city, lag(lat) OVER (ORDER BY rn) AS prev_lat,
             lag(lng) OVER (ORDER BY rn) AS prev_lng, lat, lng FROM g)
SELECT prev_city, city, prev_lat, prev_lng, lat, lng,
  6371 * acos(least(1, cos(radians(prev_lat)) * cos(radians(lat)) * cos(radians(lng) - radians(prev_lng))
    + sin(radians(prev_lat)) * sin(radians(lat)))) AS distance_km
FROM d WHERE prev_city IS NOT NULL ORDER BY distance_km DESC LIMIT 3"""


@task("q2.spider_local035", "Q2.2",
      "In the geolocation table, please identify which two consecutive cities, when sorted by geolocation_state, "
      "geolocation_city, geolocation_zip_code_prefix, geolocation_lat, and geolocation_lng, have the greatest distance "
      "between them based on the difference in distance computed between each city and its immediate predecessor in that "
      "ordering.", {"type": "table", "columns": ["prev_city", "city"]},
      scoring={"metric": "table_match", "compare_columns": [0, 1]})
def local035(e):
    a = e.q(SP_LOCAL035)
    out = a[["prev_city", "city"]].head(1)
    steps = [step("Read the distance formula (spherical law of cosines)", doc=quote("spherical", 0)),
             step("Order the points, take each point's predecessor and compute the distance", sql=SP_LOCAL035, result=tab(a)),
             spider_step(out, "local035", [0, 1])]
    r = a.iloc[0]
    gold = {"rows": [{"prev_city": r.prev_city, "city": r.city}], "distance_km": round(float(r.distance_km), 1)}
    resp = (f"The largest jump is from {r.prev_city} to {r.city}: {r.distance_km:,.0f} km by the spherical law of cosines. "
            f"Note that the second point lies at latitude {r.lat:.2f}, longitude {r.lng:.2f}, far outside Brazil.")
    return steps, gold, resp


@task("q2.spider_local004", "Q2.2",
      "Could you tell me the number of orders, average payment per order and customer lifespan in weeks of the 3 customers "
      "with the highest average payment per order, where the lifespan is calculated by subtracting the earliest purchase "
      "date from the latest purchase date in days, dividing by seven, and if the result is less than seven days, setting "
      "it to 1.0?", {"type": "table", "columns": ["customer_unique_id", "num_orders", "avg_payment_per_order", "lifespan_weeks"]},
      depends_on=["q1.customer_ids"], scoring={"metric": "table_match", "compare_columns": [0, 1, 2]})
def local004(e):
    sql = """WITH c AS (SELECT customer_unique_id, count(DISTINCT order_id) AS num_orders, sum(payment_value) AS total_payment,
             min(order_purchase_timestamp) AS first_order, max(order_purchase_timestamp) AS last_order
           FROM customers JOIN orders USING (customer_id) JOIN order_payments USING (order_id) GROUP BY 1)
SELECT customer_unique_id, num_orders, round(total_payment / num_orders, 2) AS avg_payment_per_order,
  CASE WHEN epoch(last_order - first_order) / 86400 < 7 THEN 1.0 ELSE epoch(last_order - first_order) / 86400 / 7 END AS lifespan_weeks
FROM c ORDER BY avg_payment_per_order DESC LIMIT 3"""
    a = e.q(sql)
    steps = [step("Per person: orders, total paid, first and last purchase", uses="q1.customer_ids"),
             step("Average payment per order and lifespan, top three", sql=sql, result=tab(a)),
             spider_step(a, "local004", [0, 1, 2])]
    gold = {"rows": tab(a)["rows"], "columns": list(a.columns)}
    resp = ("The three customers each placed a single order, so their lifespan is set to 1 week: " + "; ".join(
        f"{r.customer_unique_id[:8]}… R$ {r.avg_payment_per_order:,.2f}" for r in a.itertuples()) + ".")
    return steps, gold, resp


@task("q2.status_conflicts", "Q2.1",
      "Which orders have a status that contradicts their dates: status delivered but no delivery date, or status canceled "
      "with a delivery date? List them with their status and dates.",
      {"type": "table", "columns": ["order_id", "order_status", "order_purchase_timestamp", "order_delivered_customer_date"]},
      scoring={"metric": "table_match", "compare_columns": [0, 1]})
def status_conflicts(e):
    sql = """SELECT order_id, order_status, order_purchase_timestamp, order_approved_at, order_delivered_carrier_date,
  order_delivered_customer_date
FROM orders
WHERE (order_status = 'delivered' AND order_delivered_customer_date IS NULL)
   OR (order_status = 'canceled' AND order_delivered_customer_date IS NOT NULL)
ORDER BY order_status, order_purchase_timestamp"""
    a = e.q(sql)
    n = a.order_status.value_counts()
    steps = [step("Filter the two contradictions", sql=sql, result=tab(a))]
    gold = {"rows": tab(a[["order_id", "order_status", "order_purchase_timestamp", "order_delivered_customer_date"]])["rows"],
            "columns": ["order_id", "order_status", "order_purchase_timestamp", "order_delivered_customer_date"]}
    resp = (f"{int(n.get('delivered', 0))} orders are marked delivered without a delivery date and {int(n.get('canceled', 0))} canceled "
            "orders have one; the list is in the result. Counts of delivered orders by delivery date leave out the first group.")
    return steps, gold, resp


@task("q2.monthly_orders", "Q2.2",
      "How many orders were purchased each month from January 2017 to August 2018, how many of them were delivered, and "
      "what was their gross merchandise value (item prices plus freight)?",
      {"type": "table", "columns": ["month", "orders", "delivered", "gmv"]},
      scoring={"metric": "table_match", "compare_columns": [0, 1, 2, 3]})
def monthly_orders(e):
    sql = """SELECT date_trunc('month', o.order_purchase_timestamp)::DATE AS month, count(*) AS orders,
  count(*) FILTER (WHERE o.order_status = 'delivered') AS delivered, round(sum(i.value), 2) AS gmv
FROM orders o LEFT JOIN (SELECT order_id, sum(price + freight_value) AS value FROM order_items GROUP BY 1) i USING (order_id)
WHERE o.order_purchase_timestamp >= '2017-01-01' AND o.order_purchase_timestamp < '2018-09-01'
GROUP BY 1 ORDER BY 1"""
    sql0 = """SELECT date_trunc('month', order_purchase_timestamp)::DATE AS month, count(*) AS orders FROM orders
WHERE order_purchase_timestamp < '2017-01-01' OR order_purchase_timestamp >= '2018-09-01' GROUP BY 1 ORDER BY 1"""
    a = e.q(sql)
    steps = [step("Months outside the question have few records", sql=sql0, result=tab(e.q(sql0)),
                  note="September 2016 to December 2016 and September to October 2018 are thin; November 2016 has no order."),
             step("Aggregate items to orders first, then count orders and sum value per purchase month", sql=sql, result=tab(a),
                  note="Joining items before counting would count an order once per item.")]
    gold = {"rows": tab(a)["rows"], "columns": list(a.columns)}
    pk = a.loc[a.orders.idxmax()]
    resp = (f"{int(a.orders.sum()):,} orders in 20 months, from {int(a.orders.iloc[0]):,} in January 2017 to a peak of "
            f"{int(pk.orders):,} in {str(pk.month)[:7]}; GMV R$ {a.gmv.sum() / 1e6:.2f} million. Full table in the result.")
    return steps, gold, resp


@task("q2.find_order", "Q2.3",
      "A customer from Picos (Piauí) writes: 'I bought garden tools in March 2018 and paid part of it with a voucher. It "
      "arrived late.' Which order is it? Give the order id, the promised and actual delivery days, how many days late it "
      "was, and what the freight cost compared with the price.",
      {"type": "json", "schema": {"order_id": "str", "promised": "date", "delivered": "date", "days_late": "int",
                                  "price": "float", "freight_value": "float"}},
      depends_on=["q1.customer_ids"],
      scoring={"metric": "fields", "fields": {"order_id": {"exact": True}, "days_late": {"exact": True}}})
def find_order(e):
    sql1 = """SELECT o.order_id, o.order_purchase_timestamp, o.order_status, string_agg(DISTINCT t.product_category_name_english, ', ') AS categories
FROM orders o JOIN customers c USING (customer_id) JOIN order_items i USING (order_id)
JOIN products p USING (product_id) LEFT JOIN product_category_name_translation t USING (product_category_name)
WHERE c.customer_city = 'picos' AND c.customer_state = 'PI' AND o.order_purchase_timestamp >= '2018-03-01' AND o.order_purchase_timestamp < '2018-04-01'
GROUP BY 1, 2, 3 ORDER BY 2"""
    a = e.q(sql1)
    cand = a[a.categories.str.contains("garden_tools", na=False)]
    sql2 = f"""SELECT order_id, payment_sequential, payment_type, payment_installments, payment_value FROM order_payments
WHERE order_id IN ({', '.join(repr(x) for x in cand.order_id)}) ORDER BY 1, 2"""
    b = e.q(sql2)
    oid = b[b.payment_type == "voucher"].order_id.unique()
    assert len(oid) == 1, oid
    oid = oid[0]
    sql3 = f"""SELECT o.order_id, CAST(o.order_estimated_delivery_date AS DATE) AS promised, CAST(o.order_delivered_customer_date AS DATE) AS delivered,
  date_diff('day', CAST(o.order_estimated_delivery_date AS DATE), CAST(o.order_delivered_customer_date AS DATE)) AS days_late,
  i.price, i.freight_value, s.seller_city, s.seller_state
FROM orders o JOIN order_items i USING (order_id) JOIN sellers s USING (seller_id) WHERE o.order_id = '{oid}'"""
    c = e.q(sql3).iloc[0]
    steps = [step("Orders of customers in Picos (PI) purchased in March 2018, with their categories", sql=sql1, result=tab(a)),
             step("Payments of the garden tools orders: only one includes a voucher", sql=sql2, result=tab(b)),
             step("Promised and actual delivery, price and freight of that order", sql=sql3, result=tab(e.q(sql3)))]
    gold = {"order_id": oid, "promised": str(c.promised)[:10], "delivered": str(c.delivered)[:10], "days_late": int(c.days_late),
            "price": float(c.price), "freight_value": float(c.freight_value)}
    resp = (f"Order {oid}: promised for {gold['promised']}, delivered on {gold['delivered']}, {gold['days_late']} days late. "
            f"The freight (R$ {c.freight_value:.2f}) was {c.freight_value / c.price * 100:.0f}% of the price (R$ {c.price:.2f}); the "
            f"seller is in {c.seller_city} ({c.seller_state}).")
    return steps, gold, resp


@task("q2.funnel_period", "Q2.4",
      "According to the description of the marketing funnel data, which period do the leads cover and how were they "
      "selected? Do the records agree, and how many leads per month are there?", TEXT)
def funnel_period(e):
    sql1 = "SELECT count(*) AS leads, min(first_contact_date) AS first, max(first_contact_date) AS last FROM leads_qualified"
    sql2 = """SELECT date_trunc('month', first_contact_date)::DATE AS month, count(*) AS leads FROM leads_qualified GROUP BY 1 ORDER BY 1"""
    a, b = e.q(sql1).iloc[0], e.q(sql2)
    m17 = b[b.month.astype(str) < "2018-01-01"].leads
    m18 = b[b.month.astype(str) >= "2018-01-01"].leads
    steps = [step("Read the description", doc=quote("funnel", 0, 1)),
             step("Range and number of the lead records", sql=sql1, result=tab(e.q(sql1))),
             step("Leads per month", sql=sql2, result=tab(b))]
    gold = {"key_facts": [
        "The description says the 8k marketing qualified leads requested contact between June 1, 2017 and June 1, 2018 and "
        "were randomly sampled from all leads.",
        f"The records agree: {int(a.leads):,} leads with first contact from {str(a['first'])[:10]} to {str(a['last'])[:10]}.",
        f"Leads per month rise sharply in 2018: {int(m17.min())}-{int(m17.max())} a month in 2017, {int(m18.min()):,}-{int(m18.max()):,} "
        "in 2018 (June 2017 has only a few days of leads)."], "required": [0, 1]}
    resp = (f"The funnel description says the {int(a.leads):,} leads asked for contact between June 1, 2017 and June 1, 2018 and are a "
            f"random sample of all leads. The records run from {str(a['first'])[:10]} to {str(a['last'])[:10]}. Monthly leads jump in "
            f"2018: {int(m17.min())}-{int(m17.max())} a month in 2017 against {int(m18.min()):,}-{int(m18.max()):,} in 2018.")
    return steps, gold, resp


# ====================================================================== Q3 Describe
def monthly(e, start="2017-01-01", end="2018-08-01"):
    a = e.q("""SELECT date_trunc('month', order_purchase_timestamp)::DATE AS month, count(*) AS orders FROM orders GROUP BY 1""")
    s = pd.Series(a.orders.values.astype(float), index=pd.to_datetime(a.month)).sort_index()
    return s[start:end]


@task("q3.order_growth", "Q3.1",
      "How did the number of orders develop from January 2017 to August 2018? Describe the trend, and say whether it "
      "continued in 2018.", TEXT, depends_on=["q2.monthly_orders"])
def order_growth(e):
    s = monthly(e)
    mk17 = S.mann_kendall(s["2017-01-01":"2017-12-01"].values)
    mk18 = S.mann_kendall(s["2018-01-01":"2018-08-01"].values)
    ja17, ja18 = s["2017-01-01":"2017-08-01"].sum(), s["2018-01-01":"2018-08-01"].sum()
    h1, h2 = s["2017-01-01":"2017-06-01"].mean(), s["2017-07-01":"2017-12-01"].mean()
    steps = [step("Monthly orders", uses="q2.monthly_orders",
                  result={"jan_2017": int(s.iloc[0]), "nov_2017": int(s["2017-11-01"]), "aug_2018": int(s.iloc[-1])}),
             step("Trend test within each year (Mann-Kendall)", source="calculation",
                  result={"tau_2017": round(mk17["tau"], 3), "p_2017": float(f"{mk17['p']:.2g}"),
                          "tau_2018": round(mk18["tau"], 3), "p_2018": float(f"{mk18['p']:.2g}")}),
             step("Compare January-August 2018 with January-August 2017", source="calculation",
                  result={"jan_aug_2017": int(ja17), "jan_aug_2018": int(ja18), "growth": round(ja18 / ja17 - 1, 3)})]
    gold = {"key_facts": [
        f"Orders grew fast through 2017, from {int(s.iloc[0]):,} in January to about {int(h2):,} a month in the second half "
        f"(Mann-Kendall tau {mk17['tau']:.2f}, p {mk17['p']:.1g}).",
        f"November 2017 is a peak ({int(s['2017-11-01']):,} orders) driven by Black Friday.",
        f"In 2018 growth stopped: {int(s['2018-01-01':'2018-08-01'].min()):,}-{int(s['2018-01-01':'2018-08-01'].max()):,} orders a "
        f"month with no upward trend (tau {mk18['tau']:.2f}, p {mk18['p']:.2f}), slightly falling after May.",
        f"January-August 2018 had {(ja18 / ja17 - 1) * 100:.0f}% more orders than January-August 2017 because 2018 kept the level "
        "reached at the end of 2017."],
        "required": [0, 2]}
    resp = (f"Orders rose steadily through 2017, from {int(s.iloc[0]):,} in January to about {int(h2):,} a month in the second half, "
            f"with a Black Friday peak of {int(s['2017-11-01']):,} in November. From January 2018 the level is flat at "
            f"{int(s['2018-01-01':'2018-08-01'].min()):,}-{int(s['2018-01-01':'2018-08-01'].max()):,} a month (no trend, tau "
            f"{mk18['tau']:.2f}) and eases after May. January-August 2018 is still {(ja18 / ja17 - 1) * 100:.0f}% above the same months of 2017.")
    return steps, gold, resp


@task("q3.weekly_cycle", "Q3.2",
      "Describe the weekly and daily cycle of purchases (January 2017 to August 2018): which weekdays and hours are busiest?",
      TEXT)
def weekly_cycle(e):
    sql1 = """SELECT dayofweek(order_purchase_timestamp) AS dow, dayname(order_purchase_timestamp) AS weekday, count(*) AS orders
FROM orders WHERE order_purchase_timestamp >= '2017-01-01' AND order_purchase_timestamp < '2018-09-01' GROUP BY 1, 2 ORDER BY 1"""
    sql2 = """SELECT hour(order_purchase_timestamp) AS hour, count(*) AS orders,
  round(count(*) * 100.0 / sum(count(*)) OVER (), 1) AS pct
FROM orders WHERE order_purchase_timestamp >= '2017-01-01' AND order_purchase_timestamp < '2018-09-01' GROUP BY 1 ORDER BY 1"""
    a, b = e.q(sql1), e.q(sql2)
    top, low = a.loc[a.orders.idxmax()], a.loc[a.orders.idxmin()]
    day = b[(b.hour >= 10) & (b.hour <= 22)].pct.sum()
    night = b[(b.hour >= 2) & (b.hour <= 6)].pct.sum()
    steps = [step("Orders by weekday", sql=sql1, result=tab(a)), step("Orders by hour of the day", sql=sql2, result=tab(b))]
    gold = {"key_facts": [
        f"{top.weekday} is the busiest weekday ({int(top.orders):,} orders) and {low.weekday} the quietest ({int(low.orders):,}, "
        f"{low.orders / top.orders * 100:.0f}% of {top.weekday}); orders decline from Monday to Saturday.",
        f"Purchases spread evenly from 10:00 to 22:00 ({day:.0f}% of orders), with no lunch or evening peak.",
        f"Few orders are placed at night: 2:00-6:59 has {night:.1f}% of orders."], "required": [0, 1]}
    resp = (f"Purchases fall through the week: {top.weekday} is the busiest day ({int(top.orders):,} orders) and {low.weekday} the "
            f"quietest ({low.orders / top.orders * 100:.0f}% of {top.weekday}). Within a day, orders form a plateau from 10:00 to 22:00 "
            f"({day:.0f}% of orders) and drop at night ({night:.1f}% between 2:00 and 6:59).")
    return steps, gold, resp


@task("q3.delivery_time", "Q3.3",
      "Describe the distribution of delivery times (purchase to delivery, delivered orders): typical value, spread, tail, "
      "and differences between regions.", TEXT)
def delivery_time(e):
    sql1 = f"""SELECT count(*) AS orders, round(avg(d), 1) AS mean_days, round(quantile_cont(d, 0.5), 1) AS median_days,
  round(quantile_cont(d, 0.9), 1) AS p90_days, round(quantile_cont(d, 0.99), 1) AS p99_days, round(max(d), 1) AS max_days,
  count(*) FILTER (WHERE d > 60) AS over_60_days
FROM (SELECT epoch(order_delivered_customer_date - order_purchase_timestamp) / 86400 AS d FROM orders WHERE {DELIVERED})"""
    sql2 = f"""SELECT s.region, count(*) AS orders,
  round(median(epoch(o.order_delivered_customer_date - o.order_purchase_timestamp) / 86400), 1) AS median_days,
  round(avg({LATE}::INT) * 100, 1) AS pct_late
FROM orders o JOIN customers c USING (customer_id) JOIN states s ON s.uf = c.customer_state
WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL GROUP BY 1 ORDER BY 3"""
    sql3 = f"""SELECT round(median(epoch(order_delivered_carrier_date - order_purchase_timestamp) / 86400), 1) AS median_days_to_carrier,
  round(median(epoch(order_delivered_customer_date - order_delivered_carrier_date) / 86400), 1) AS median_days_carrier_to_customer
FROM orders WHERE {DELIVERED} AND order_delivered_carrier_date IS NOT NULL"""
    a, b, c = e.q(sql1).iloc[0], e.q(sql2), e.q(sql3).iloc[0]
    fast, slow = b.iloc[0], b.iloc[-1]
    steps = [step("Summary of delivery times", sql=sql1, result=tab(e.q(sql1))),
             step("By region of the customer", sql=sql2, result=tab(b)),
             step("The two legs: seller to carrier, carrier to customer", sql=sql3, result=tab(e.q(sql3)))]
    gold = {"key_facts": [
        f"Typical delivery takes {a.median_days} days (median); the mean is {a.mean_days} days because the distribution is right-skewed.",
        f"90% of orders arrive within {a.p90_days} days and 99% within {a.p99_days}; {int(a.over_60_days)} orders took more than 60 "
        f"days (the longest {a.max_days:.0f}).",
        f"Delivery is fastest in the {fast.region} ({fast.median_days} days) and slowest in the {slow.region} ({slow.median_days} days).",
        f"Most of the time is transport: {c.median_days_to_carrier} days to the carrier and {c.median_days_carrier_to_customer} days "
        "from carrier to customer (medians)."], "required": [0, 1, 2]}
    resp = (f"Delivered orders take {a.median_days} days at the median and {a.mean_days} on average: the distribution has a long right "
            f"tail (90% within {a.p90_days} days, 99% within {a.p99_days}, {int(a.over_60_days)} beyond 60 days). Customers in the "
            f"{fast.region} wait {fast.median_days} days, in the {slow.region} {slow.median_days}. Most of it is transport "
            f"({c.median_days_carrier_to_customer} days from carrier to customer against {c.median_days_to_carrier} at the seller).")
    return steps, gold, resp


@task("q3.delay_and_reviews", "Q3.4",
      "How are review scores related to delivery against the promised date? Compare orders delivered early, on time and late.",
      TEXT, depends_on=["q1.review_survey"])
def delay_reviews(e):
    sql = """SELECT CASE WHEN dd <= -10 THEN '1: 10+ days early' WHEN dd <= 0 THEN '2: up to 9 days early or on the day'
  WHEN dd <= 3 THEN '3: 1-3 days late' WHEN dd <= 7 THEN '4: 4-7 days late' ELSE '5: 8+ days late' END AS delivery,
  count(*) AS reviews, round(avg(review_score), 2) AS avg_score, round(avg((review_score <= 2)::INT) * 100, 1) AS pct_1_or_2_stars
FROM (SELECT date_diff('day', CAST(o.order_estimated_delivery_date AS DATE), CAST(o.order_delivered_customer_date AS DATE)) AS dd, r.review_score
      FROM orders o JOIN order_reviews r USING (order_id) WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL)
GROUP BY 1 ORDER BY 1"""
    sql2 = """SELECT corr(dd, review_score) AS pearson_r FROM (SELECT date_diff('day', CAST(o.order_estimated_delivery_date AS DATE),
  CAST(o.order_delivered_customer_date AS DATE)) AS dd, r.review_score FROM orders o JOIN order_reviews r USING (order_id)
  WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL)"""
    a = e.q(sql)
    r = float(e.v(sql2))
    ontime = a[a.delivery.str.startswith(("1", "2"))]
    on_score = float((ontime.avg_score * ontime.reviews).sum() / ontime.reviews.sum())
    late8 = a[a.delivery.str.startswith("5")].iloc[0]
    late13 = a[a.delivery.str.startswith("3")].iloc[0]
    steps = [step("Surveys of late orders can be sent before delivery", uses="q1.review_survey"),
             step("Scores by days early or late", sql=sql, result=tab(a)),
             step("Linear correlation of days late and score", sql=sql2, result={"pearson_r": round(r, 3)},
                  note="The relation is a step, not a line: early or on-time orders score alike; scores fall once an order is late.")]
    gold = {"key_facts": [
        f"Orders delivered by the promised day score about {on_score:.1f} on average, whether 10 days early or on the day.",
        f"Scores drop as soon as an order is late: {late13.avg_score} for 1-3 days late and {late8.avg_score} for 8 or more days "
        f"late, where {late8.pct_1_or_2_stars}% of reviews give 1 or 2 stars.",
        f"The relation is a threshold, not linear (Pearson r {r:.2f} overall)."], "required": [0, 1]}
    resp = (f"Delivery against the promise is the main driver: orders delivered by the promised day average about {on_score:.1f} stars "
            f"no matter how early they come; 1-3 days late gives {late13.avg_score} and 8+ days late {late8.avg_score}, with "
            f"{late8.pct_1_or_2_stars}% one- or two-star reviews. It is a threshold effect (overall r = {r:.2f}).")
    return steps, gold, resp


# ====================================================================== Q4 Discover
@task("q4.unusual_days", "Q4.1",
      "Were there unusual days in the number of orders between November 2017 and June 2018? For each, say what happened.",
      {"type": "json", "schema": {"anomalies": [{"dates": ["date"], "direction": "str", "explanation": "str"}]}},
      depends_on=["q3.weekly_cycle"], scoring={"metric": "event_f1", "date_tol_days": 1})
def unusual_days(e):
    s = daily_orders(e)
    base = weekday_median(s)
    r = np.log((s + 1) / (base + 1))
    flags = []
    for d in s["2017-11-01":"2018-06-30"].index:
        hist = r[(r.index < d) & (r.index >= d - pd.Timedelta(days=56))].dropna()
        scale = max(1.4826 * np.median(np.abs(hist - hist.median())), 0.05)
        z = (r[d] - hist.median()) / scale
        ch = (s[d] + 1) / (base[d] + 1) - 1
        if abs(z) > 3.5 and abs(ch) > 0.4:
            flags.append(dict(date=str(d.date()), orders=int(s[d]), expected=float(base[d]), change_pct=round(ch * 100),
                              z=round(float(z), 1)))
    cal = e.q("SELECT date, name FROM calendar WHERE date BETWEEN '2017-11-01' AND '2018-06-30'")
    names = {str(x)[:10]: n for x, n in zip(cal.date, cal.name)}
    groups = []
    for f in flags:
        if groups and (pd.Timestamp(f["date"]) - pd.Timestamp(groups[-1][-1]["date"])).days <= 2 \
                and np.sign(f["change_pct"]) == np.sign(groups[-1][-1]["change_pct"]):
            groups[-1].append(f)
        else:
            groups.append([f])
    sql_c = """SELECT CAST(order_delivered_carrier_date AS DATE) AS day, count(*) AS handed_to_carrier FROM orders
WHERE order_delivered_carrier_date BETWEEN '2018-05-14' AND '2018-06-03' GROUP BY 1 ORDER BY 1"""

    def why(g):
        ds = [x["date"] for x in g]
        if any(names.get(x) == "Black Friday" for x in ds):
            return "Black Friday (November 24, 2017) and the days around it"
        if ds[0] >= "2018-05-20" and ds[-1] <= "2018-06-01":
            return "the truck drivers' strike that blocked roads from May 21, 2018"
        if any(x[5:] in ("12-24", "12-25") for x in ds):
            return "Christmas Eve and Christmas Day"
        hol = [names[x] for x in ds if x in names]
        if hol:
            return "holiday: " + ", ".join(hol)
        if all(x[5:] in ("12-24", "12-31") for x in ds):
            return "Christmas Eve or New Year's Eve"
        return "unexplained"
    anomalies = []
    for g in groups:
        a_ = dict(dates=[x["date"] for x in g], direction="up" if g[0]["change_pct"] > 0 else "down", explanation=why(g))
        if anomalies and anomalies[-1]["explanation"] == a_["explanation"] and anomalies[-1]["direction"] == a_["direction"] \
                and (pd.Timestamp(a_["dates"][0]) - pd.Timestamp(anomalies[-1]["dates"][-1])).days <= 7:
            anomalies[-1]["dates"] += a_["dates"]
        else:
            anomalies.append(a_)
    steps = [step("Purchases follow a weekly cycle: compare each day with the median of the same weekday in the four weeks before",
                  uses="q3.weekly_cycle"),
             step("Daily orders against that baseline; flag days more than 3.5 robust deviations and 40% away", source="db",
                  sql="SELECT CAST(order_purchase_timestamp AS DATE) AS day, count(*) AS orders FROM orders GROUP BY 1 ORDER BY 1",
                  result=tab(pd.DataFrame(flags))),
             step("Match the flagged days with the calendar", sql="SELECT date, name, kind FROM calendar WHERE date BETWEEN '2017-11-01' AND '2018-06-30' ORDER BY 1",
                  result=tab(pd.DataFrame(anomalies).assign(dates=lambda d: d.dates.map(", ".join)))),
             step("Read about Black Friday in Brazil", doc=quote("black_friday", 0, 1)),
             step("The May 2018 drop: read about the strike, and see hand-overs to carriers fall", doc=quote("strike", 0, 2),
                  sql=sql_c, result=tab(e.q(sql_c)))]
    gold = {"anomalies": anomalies}
    resp = " ".join(f"{', '.join(a_['dates'])}: {'more' if a_['direction'] == 'up' else 'fewer'} orders than usual; {a_['explanation']}."
                    for a_ in anomalies)
    return steps, gold, resp


@task("q4.promise_shift", "Q4.2",
      "Did the promised delivery time (purchase to estimated delivery date) change in 2018? Compare it with the actual "
      "delivery time and say what the change did to late deliveries.", TEXT, depends_on=["q3.delivery_time"])
def promise_shift(e):
    sql = f"""SELECT date_trunc('month', order_purchase_timestamp)::DATE AS month, count(*) AS orders,
  median(date_diff('day', CAST(order_purchase_timestamp AS DATE), CAST(order_estimated_delivery_date AS DATE))) AS promised_days_median,
  round(avg(date_diff('day', CAST(order_purchase_timestamp AS DATE), CAST(order_estimated_delivery_date AS DATE))), 1) AS promised_days_mean,
  round(avg(date_diff('day', CAST(order_purchase_timestamp AS DATE), CAST(order_delivered_customer_date AS DATE))), 1) AS actual_days_mean,
  round(avg({LATE}::INT) * 100, 1) AS pct_late
FROM orders WHERE {DELIVERED} AND order_purchase_timestamp >= '2017-06-01' AND order_purchase_timestamp < '2018-09-01'
GROUP BY 1 ORDER BY 1"""
    sql2 = """SELECT s.region, year(o.order_purchase_timestamp) AS year,
  round(avg(date_diff('day', CAST(o.order_purchase_timestamp AS DATE), CAST(o.order_estimated_delivery_date AS DATE))), 1) AS promised_days,
  round(avg(date_diff('day', CAST(o.order_purchase_timestamp AS DATE), CAST(o.order_delivered_customer_date AS DATE))), 1) AS actual_days
FROM orders o JOIN customers c USING (customer_id) JOIN states s ON s.uf = c.customer_state
WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL AND month(o.order_purchase_timestamp) = 8
GROUP BY 1, 2 ORDER BY 1, 2"""
    a = e.q(sql).set_index(e.q(sql).month.astype(str).str[:7])
    may, jun, aug, a17 = a.loc["2018-05"], a.loc["2018-06"], a.loc["2018-08"], a.loc["2017-08"]
    slack = lambda r: r.promised_days_mean - r.actual_days_mean
    steps = [step("Delivery times are mostly transport", uses="q3.delivery_time"),
             step("Promised and actual days by purchase month (delivered orders)", sql=sql, result=tab(a.reset_index(drop=True))),
             step("August 2017 against August 2018 by region", sql=sql2, result=tab(e.q(sql2)),
                  note="Promises were shortened in every region, more than delivery times fell.")]
    gold = {"key_facts": [
        f"Yes: the promised time was cut sharply in mid-2018, from a median of {int(jun.promised_days_median)} days for June "
        f"orders to {int(aug.promised_days_median)} days for August orders (it was {int(a17.promised_days_median)} days in August 2017).",
        f"Actual delivery also got faster ({jun.actual_days_mean} to {aug.actual_days_mean} days on average), but less: the slack between "
        f"promise and delivery shrank from {slack(jun):.1f} to {slack(aug):.1f} days.",
        f"With less slack, the share of late deliveries rose from {jun.pct_late}% (June orders) to {aug.pct_late}% (August orders).",
        f"June promises were themselves unusually long: the median rose from {int(may.promised_days_median)} days for May orders "
        f"to {int(jun.promised_days_median)} for June orders before the cut; by August it was far below its 2017 level.",
        "The change is in the promised dates set at purchase, not in how delivery dates are recorded."],
        "required": [0, 1, 2]}
    resp = (f"The promise changed: June 2018 orders were promised in a median {int(jun.promised_days_median)} days, August orders in "
            f"{int(aug.promised_days_median)} (August 2017: {int(a17.promised_days_median)}). Deliveries got faster too "
            f"({jun.actual_days_mean} to {aug.actual_days_mean} days) but not as much, so the slack fell from {slack(jun):.1f} to "
            f"{slack(aug):.1f} days and late deliveries rose from {jun.pct_late}% to {aug.pct_late}%. June itself was unusually long "
            f"(May orders: {int(may.promised_days_median)} days); by August the promise was well below its 2017 level.")
    return steps, gold, resp


@task("q4.demand_and_income", "Q4.3",
      "Across the 27 states, how is the number of orders per inhabitant in 2017 related to income (2017 GDP per capita "
      "from IBGE)? Which states depart from the pattern?",
      TEXT, depends_on=["q1.customer_ids"])
def demand_income(e):
    sql = """WITH g AS (SELECT uf, sum(gdp) * 1000 / sum(gdp * 1000 / gdp_per_capita) AS gdp_per_capita FROM municipal_gdp
           WHERE year = 2017 GROUP BY 1),
     o AS (SELECT c.customer_state AS uf, count(*) AS orders FROM orders o JOIN customers c USING (customer_id)
           WHERE year(o.order_purchase_timestamp) = 2017 GROUP BY 1)
SELECT s.uf, s.region, s.population_2018, o.orders, round(o.orders * 1e5 / s.population_2018, 1) AS orders_per_100k,
  round(g.gdp_per_capita) AS gdp_per_capita
FROM states s JOIN o USING (uf) JOIN g USING (uf) ORDER BY orders_per_100k DESC"""
    a = e.q(sql)
    from scipy import stats as st
    rho, p = st.spearmanr(a.orders_per_100k, a.gdp_per_capita)
    b = np.polyfit(np.log(a.gdp_per_capita), np.log(a.orders_per_100k), 1)
    a["residual"] = np.log(a.orders_per_100k) - np.polyval(b, np.log(a.gdp_per_capita))
    hi, lo = a.loc[a.residual.idxmax()], a.loc[a.residual.idxmin()]
    df = a.set_index("uf")
    steps = [step("Orders per 100,000 inhabitants and GDP per capita by state", sql=sql, result=tab(a.drop(columns="residual")),
                  note="State GDP per capita is the sum of municipal GDP over the population IBGE used (GDP / GDP per capita)."),
             step("Rank correlation and a log-log fit", source="calculation",
                  result={"spearman_rho": round(float(rho), 3), "p_value": float(f"{p:.2g}"), "elasticity": round(float(b[0]), 2)}),
             step("Largest departures from the fit", source="calculation",
                  result=tab(a.sort_values("residual")[["uf", "orders_per_100k", "gdp_per_capita", "residual"]].round(2)))]
    gold = {"key_facts": [
        f"Orders per inhabitant rise with income: Spearman rho {rho:.2f} (p {p:.1g}); a 1% higher GDP per capita goes with about "
        f"{b[0]:.1f}% more orders per inhabitant.",
        f"São Paulo and Rio de Janeiro order most ({df.loc['SP'].orders_per_100k} and {df.loc['RJ'].orders_per_100k} per 100,000); "
        f"the North orders least (Amazonas {df.loc['AM'].orders_per_100k}).",
        f"The Federal District has by far the highest GDP per capita (R$ {df.loc['DF'].gdp_per_capita:,.0f}) but orders less than "
        "São Paulo and Rio, so income is not the only factor.",
        f"Below the pattern: {lo.uf} ({lo.orders_per_100k} per 100,000); above it: {hi.uf} ({hi.orders_per_100k})."],
        "required": [0, 1]}
    resp = (f"Demand follows income: orders per 100,000 inhabitants and GDP per capita have a rank correlation of {rho:.2f}, and "
            f"orders rise about {b[0]:.1f}% for each 1% of income. São Paulo ({df.loc['SP'].orders_per_100k}) and Rio "
            f"({df.loc['RJ'].orders_per_100k}) lead; the North trails (Amazonas {df.loc['AM'].orders_per_100k}). The Federal District is "
            f"richest but orders less than SP and RJ. Furthest below the pattern is {lo.uf}, furthest above {hi.uf}.")
    return steps, gold, resp


@task("q4.open_review", "Q4.open",
      "Look at the marketplace records of January 2017 to August 2018 and report the most important things you notice "
      "about demand, delivery and customer satisfaction.", TEXT,
      depends_on=["q3.order_growth", "q3.delay_and_reviews", "q4.unusual_days", "q4.promise_shift", "q4.demand_and_income"])
def open_review(e):
    gr = order_growth(e)[1]["key_facts"]
    dr = delay_reviews(e)[1]["key_facts"]
    ud = unusual_days(e)[1]["anomalies"]
    ps = promise_shift(e)[1]["key_facts"]
    di = demand_income(e)[1]["key_facts"]
    steps = [step("Demand grew through 2017 and stopped growing in 2018", uses="q3.order_growth", result={"facts": gr[:3]}),
             step("Unusual days", uses="q4.unusual_days",
                  result={"anomalies": [f"{a_['dates'][0]} to {a_['dates'][-1]}: {a_['explanation']}" for a_ in ud]}),
             step("Lateness drives review scores", uses="q3.delay_and_reviews", result={"facts": dr[:2]}),
             step("The delivery promise was cut in mid-2018", uses="q4.promise_shift", result={"facts": ps[:3]}),
             step("Demand follows income across states", uses="q4.demand_and_income", result={"facts": di[:2]})]
    strike_ = next(a_ for a_ in ud if "strike" in a_["explanation"])
    bf = next(a_ for a_ in ud if "Black Friday" in a_["explanation"])
    day = lambda x: pd.Timestamp(x).strftime("%B %-d, %Y")
    gold = {"key_facts": [
        gr[0], gr[2],
        f"Orders peaked around Black Friday, from {day(bf['dates'][0])} to {day(bf['dates'][-1])}.",
        f"Orders, hand-overs and deliveries collapsed from {day(strike_['dates'][0])} to {day(strike_['dates'][-1])}, during the "
        "truck drivers' strike that blocked roads from May 21, 2018.",
        dr[1],
        ps[0].removeprefix("Yes: ")[0].upper() + ps[0].removeprefix("Yes: ")[1:], ps[2],
        di[0]],
        "required": [1, 3, 4, 5]}
    resp = " ".join(gold["key_facts"])
    return steps, gold, resp


# ====================================================================== Q5 Diagnose
@task("q5.strike_may_2018", "Q5.1",
      "In the last ten days of May 2018 orders, hand-overs to carriers and deliveries all fell. What happened? Rank the "
      "causes, and say what it meant for late deliveries.",
      {"type": "json", "schema": {"causes": [{"cause": "str", "evidence": "str"}]}},
      depends_on=["q4.unusual_days"], scoring={"metric": "cause_ranking", "judge": "llm"})
def strike(e):
    daily = """WITH o AS (SELECT CAST(order_purchase_timestamp AS DATE) AS day, count(*) AS n FROM orders GROUP BY 1),
     c AS (SELECT CAST(order_delivered_carrier_date AS DATE) AS day, count(*) AS n FROM orders GROUP BY 1),
     d AS (SELECT CAST(order_delivered_customer_date AS DATE) AS day, count(*) AS n FROM orders GROUP BY 1),
     days AS (SELECT range::DATE AS day FROM range(DATE '{a}', DATE '{b}', INTERVAL 1 DAY))
SELECT days.day, dayname(days.day) AS weekday, coalesce(o.n, 0) AS orders, coalesce(c.n, 0) AS handed_to_carrier,
  coalesce(d.n, 0) AS delivered
FROM days LEFT JOIN o USING (day) LEFT JOIN c USING (day) LEFT JOIN d USING (day)"""
    sql = daily.format(a="2018-05-14", b="2018-06-11") + "\nORDER BY 1"
    sql_p = ("WITH x AS (" + daily.format(a="2018-04-02", b="2018-06-30") + """)
SELECT CASE WHEN day <= DATE '2018-04-27' THEN '1: April 2-27' WHEN day BETWEEN DATE '2018-05-23' AND DATE '2018-05-30'
  THEN '2: May 23-30' WHEN day >= DATE '2018-06-04' THEN '3: June 4-29' END AS period, count(*) AS weekdays,
  round(avg(orders)) AS orders, round(avg(handed_to_carrier)) AS handed_to_carrier, round(avg(delivered)) AS delivered
FROM x WHERE dayofweek(day) BETWEEN 1 AND 5 AND period IS NOT NULL GROUP BY 1 ORDER BY 1""")
    sql2 = f"""SELECT date_trunc('week', order_purchase_timestamp)::DATE AS purchase_week, count(*) AS delivered_orders,
  round(avg(date_diff('day', CAST(order_purchase_timestamp AS DATE), CAST(order_estimated_delivery_date AS DATE))), 1) AS promised_days,
  round(avg(date_diff('day', CAST(order_purchase_timestamp AS DATE), CAST(order_delivered_customer_date AS DATE))), 1) AS actual_days,
  round(avg({LATE}::INT) * 100, 1) AS pct_late
FROM orders WHERE {DELIVERED} AND order_purchase_timestamp >= '2018-04-02' AND order_purchase_timestamp < '2018-06-18'
GROUP BY 1 ORDER BY 1"""
    a = e.q(sql)
    per = e.q(sql_p).set_index("period")
    c = e.q(sql2).set_index("purchase_week")
    c.index = c.index.astype(str)
    apr, dur, jun = per.loc["1: April 2-27"], per.loc["2: May 23-30"], per.loc["3: June 4-29"]
    chg = lambda k: (dur[k] / apr[k] - 1) * 100
    rec = lambda k: jun[k] / apr[k] * 100
    apr_late = float(c.loc[[k for k in c.index if k < "2018-05-01"]].pct_late.mean())
    pre, w28 = c.loc["2018-05-14"], c.loc["2018-05-28"]
    cal = e.q("SELECT date, name, kind FROM calendar WHERE date BETWEEN '2018-05-14' AND '2018-06-10'")
    steps = [step("The flagged days of late May 2018", uses="q4.unusual_days"),
             step("Read what happened from May 21, 2018", doc=quote("strike", 0, 1, 2, 3)),
             step("Orders, hand-overs to carriers and deliveries per day", sql=sql, result=tab(a)),
             step("Weekday averages: April, the strike, June", sql=sql_p, result=tab(per.reset_index()),
                  note="April is the baseline: the first half of May was raised by Mother's Day (May 13)."),
             step("Holidays and observances in the period", sql="SELECT date, name, kind FROM calendar WHERE date BETWEEN '2018-05-14' AND '2018-06-10' ORDER BY 1",
                  result=tab(cal)),
             step("Promised and actual delivery and late share by purchase week", sql=sql2, result=tab(c.reset_index()),
                  note="Orders bought just before the strike were on the road when it began; later orders got longer promises.")]
    gold = {"causes": [
        dict(cause="the truck drivers' strike: road blockades across Brazil from May 21, 2018",
             evidence=f"on the weekdays of May 23-30 hand-overs to carriers were {-chg('handed_to_carrier'):.0f}% and deliveries "
                      f"{-chg('delivered'):.0f}% below April; in June they were back to {rec('handed_to_carrier'):.0f}% and "
                      f"{rec('delivered'):.0f}% of April"),
        dict(cause="customers bought less during the strike",
             evidence=f"orders were {-chg('orders'):.0f}% below April, which a delivery problem alone does not explain; in June they "
                      f"were back to {rec('orders'):.0f}% of the April level"),
        dict(cause="Corpus Christi on Thursday May 31 (a secondary holiday)",
             evidence="almost no deliveries on May 31; it explains one day, not the ten-day drop")],
        "late_deliveries": [
            f"orders bought in the week before the strike (from May 14) were late {pre.pct_late}% of the time, against about "
            f"{apr_late:.0f}% in April: they were on the road when it began",
            f"orders bought during and right after the strike were rarely late ({w28.pct_late}% for the week of May 28) because "
            f"their promised delivery was lengthened ({w28.promised_days} days on average against about "
            f"{c.loc[[k for k in c.index if k < '2018-05-01']].promised_days.mean():.0f} in April)"],
        "not_causes": ["normal seasonality (no such drop in the weeks before or after)"]}
    resp = (f"1. The truck drivers' strike: roads were blocked from May 21, 2018. On the weekdays of May 23-30 hand-overs to carriers "
            f"were {-chg('handed_to_carrier'):.0f}% and deliveries {-chg('delivered'):.0f}% below April; in June both were back near "
            f"the April level ({rec('handed_to_carrier'):.0f}% and {rec('delivered'):.0f}%). "
            f"2. Demand fell too: {-chg('orders'):.0f}% fewer orders than in April; June was back to {rec('orders'):.0f}% of April. "
            f"3. Corpus Christi (May 31) explains the near-zero deliveries of that day. Late deliveries rose for orders bought just "
            f"before the strike ({pre.pct_late}% against about {apr_late:.0f}%), while orders bought during it were given much longer "
            f"promises ({w28.promised_days} days) and were rarely late.")
    return steps, gold, resp


@task("q5.late_spike_2018", "Q5.2",
      "Late deliveries rose from about 4% of orders bought in the first half of January 2018 to about 25% of orders bought "
      "from February 19 to March 18, 2018. How much of the rise came from shorter promises, and how much from slower "
      "transport?", {"type": "json", "schema": {"promise_points": "float", "transport_points": "float"}},
      depends_on=["q3.delivery_time"],
      scoring={"metric": "fields", "fields": {"promise_points": {"abs_tol": 3}, "transport_points": {"abs_tol": 3}}})
def late_spike(e):
    sql = f"""SELECT CASE WHEN order_purchase_timestamp < '2018-01-15' THEN 'Jan 1-14' ELSE 'Feb 19-Mar 18' END AS period, count(*) AS orders,
  round(avg({LATE}::INT) * 100, 1) AS pct_late,
  round(avg(date_diff('day', CAST(order_purchase_timestamp AS DATE), CAST(order_estimated_delivery_date AS DATE))), 2) AS promised_days,
  round(avg(date_diff('day', CAST(order_purchase_timestamp AS DATE), CAST(order_delivered_carrier_date AS DATE))), 2) AS days_to_carrier,
  round(avg(date_diff('day', CAST(order_delivered_carrier_date AS DATE), CAST(order_delivered_customer_date AS DATE))), 2) AS days_carrier_to_customer
FROM orders WHERE {DELIVERED} AND order_delivered_carrier_date IS NOT NULL
  AND (order_purchase_timestamp BETWEEN '2018-01-01' AND '2018-01-15' OR order_purchase_timestamp BETWEEN '2018-02-19' AND '2018-03-19')
GROUP BY 1 ORDER BY 1 DESC"""
    per = e.q(sql).set_index("period")
    o = e.q(f"""SELECT order_purchase_timestamp >= '2018-02-19' AS peak,
  date_diff('day', CAST(order_purchase_timestamp AS DATE), CAST(order_estimated_delivery_date AS DATE)) AS promised,
  date_diff('day', CAST(order_purchase_timestamp AS DATE), CAST(order_delivered_carrier_date AS DATE)) AS to_carrier,
  date_diff('day', CAST(order_delivered_carrier_date AS DATE), CAST(order_delivered_customer_date AS DATE)) AS transport
FROM orders WHERE {DELIVERED} AND order_delivered_carrier_date IS NOT NULL
  AND (order_purchase_timestamp BETWEEN '2018-01-01' AND '2018-01-15' OR order_purchase_timestamp BETWEEN '2018-02-19' AND '2018-03-19')""")
    pk = o[o.peak]
    base = o[~o.peak]
    dp = base.promised.mean() - pk.promised.mean()          # promises got shorter by dp days
    dt_ = pk.transport.mean() - base.transport.mean()       # transport got slower by dt days
    late = lambda p_add, t_sub: float(((pk.to_carrier + pk.transport - t_sub) > (pk.promised + p_add)).mean() * 100)
    L00, LP0, L0T, LPT = late(0, 0), late(dp, 0), late(0, dt_), late(dp, dt_)
    promise = ((L00 - LP0) + (L0T - LPT)) / 2
    transport = ((L00 - L0T) + (LP0 - LPT)) / 2
    lb = float(((base.to_carrier + base.transport) > base.promised).mean() * 100)
    other = (L00 - lb) - promise - transport
    gold = {"promise_points": round(promise, 1), "transport_points": round(transport, 1), "other_points": round(other, 1),
            "late_pct": {"jan_1_14": round(lb, 1), "feb_19_mar_18": round(L00, 1)}}
    steps = [step("Delivery time is mostly the carrier leg", uses="q3.delivery_time"),
             step("Compare the two periods: promise, seller handling and transport", sql=sql, result=tab(per.reset_index()),
                  note="Sellers handed orders to carriers as fast as before; promises got shorter and transport slower."),
             step("Counterfactual late shares of the peak orders (Shapley decomposition of two shifts)", source="calculation",
                  result={"promise_shortening_days": round(dp, 2), "transport_slowdown_days": round(dt_, 2),
                          "late_pct_actual": round(L00, 1), "with_january_promise": round(LP0, 1),
                          "with_january_transport": round(L0T, 1), "with_both": round(LPT, 1)}),
             step("Attribute the rise in percentage points", source="calculation", result=gold),
             step("What the two average shifts leave: transport also became more variable", source="calculation",
                  result={"transport_p90_days": {"jan_1_14": float(base.transport.quantile(0.9)), "feb_19_mar_18": float(pk.transport.quantile(0.9))},
                          "transport_sd_days": {"jan_1_14": round(float(base.transport.std()), 1), "feb_19_mar_18": round(float(pk.transport.std()), 1)}})]
    resp = (f"Late deliveries rose from {lb:.1f}% to {L00:.1f}% ({L00 - lb:+.1f} points). Promises got {dp:.1f} days shorter and "
            f"carrier transport {dt_:.1f} days slower on average, while sellers handed orders over as quickly as before. Shorter promises "
            f"account for about {promise:.1f} points and slower average transport for {transport:.1f}; the other {other:.1f} points come "
            f"from transport times becoming more spread out (90th percentile {base.transport.quantile(0.9):.0f} to "
            f"{pk.transport.quantile(0.9):.0f} days), so more orders overshoot their promise.")
    return steps, gold, resp


@task("q5.september_late", "Q5.1",
      "A dashboard of deliveries by month shows that 100% of the orders delivered in September 2018 were late. Did "
      "delivery performance collapse in September 2018?", {"type": "json", "schema": {"collapse": "bool", "explanation": "str"}},
      depends_on=["q2.spider_local028", "q3.delivery_time"], control="decoy",
      scoring={"metric": "fields+explanation", "fields": ["collapse"], "judge": "llm"})
def september_late(e):
    sql = f"""SELECT date_trunc('month', order_delivered_customer_date)::DATE AS delivery_month, count(*) AS delivered,
  round(avg({LATE}::INT) * 100, 1) AS pct_late, min(order_purchase_timestamp)::DATE AS oldest_purchase,
  max(order_purchase_timestamp)::DATE AS newest_purchase
FROM orders WHERE {DELIVERED} AND order_delivered_customer_date >= '2018-06-01' GROUP BY 1 ORDER BY 1"""
    sql2 = """SELECT max(order_purchase_timestamp) AS last_delivered_order_purchase,
  (SELECT count(*) FROM orders WHERE order_purchase_timestamp >= '2018-08-30') AS orders_after_aug_29,
  (SELECT count(*) FROM orders WHERE order_purchase_timestamp >= '2018-08-30' AND order_status = 'delivered') AS delivered_after_aug_29
FROM orders WHERE order_status = 'delivered'"""
    a, b = e.q(sql), e.q(sql2).iloc[0]
    sep = a[a.delivery_month.astype(str).str.startswith("2018-09")].iloc[0]
    aug = a[a.delivery_month.astype(str).str.startswith("2018-08")].iloc[0]
    steps = [step("Deliveries by month of delivery", uses="q2.spider_local028"),
             step("Delivered orders by delivery month, with the purchase dates behind them", sql=sql, result=tab(a)),
             step("Where the records end", sql=sql2, result=tab(e.q(sql2)),
                  note="No order bought after August 29 was delivered in the data: the records stop."),
             step("Delivery takes about 10 days; an order still on its way after the records stop is an old, delayed one",
                  uses="q3.delivery_time")]
    gold = {"collapse": False, "sep_delivered": int(sep.delivered), "aug_delivered": int(aug.delivered),
            "aug_pct_late": float(aug.pct_late)}
    resp = (f"No. September 2018 has only {int(sep.delivered)} deliveries against {int(aug.delivered):,} in August. The records end with "
            f"orders bought on {str(b.last_delivered_order_purchase)[:10]}, so the orders delivered later are the few old ones that were "
            f"stuck in transit (bought as early as {str(sep.oldest_purchase)[:10]}); they are late by selection. August, a full month, "
            f"had {aug.pct_late}% late deliveries.")
    return steps, gold, resp


@task("q5.geolocation_jump", "Q5.1",
      "The Spider 2.0 task local035 finds that the largest jump between consecutive geolocation points is about 18,000 km, "
      "from santa cruz do sul to santa lucia do piai. Is that a real distance between two Brazilian towns? What explains it, "
      "and what is the distance with corrected positions?",
      {"type": "json", "schema": {"real": "bool", "corrected_km": "float"}}, depends_on=["q2.spider_local035"],
      scoring={"metric": "fields", "fields": {"real": {"exact": True}, "corrected_km": {"rel_tol": 0.25}}})
def geolocation_jump(e):
    a = e.q(SP_LOCAL035).iloc[0]
    sql1 = """SELECT * FROM geolocation WHERE geolocation_city IN ('santa lucia do piai', 'santa cruz do sul')
  AND geolocation_state = 'RS' ORDER BY geolocation_city, geolocation_lat LIMIT 8"""
    sql2 = """SELECT count(*) AS points_outside_brazil_box, count(DISTINCT geolocation_zip_code_prefix) AS prefixes
FROM geolocation WHERE geolocation_lat NOT BETWEEN -34.0 AND 5.4 OR geolocation_lng NOT BETWEEN -74.1 AND -32.3"""
    sql3 = """SELECT z.zip_code_prefix, z.city, z.n_points, z.lat, z.lon, m.name AS municipality
FROM zip_prefixes z LEFT JOIN municipalities m USING (municipality_code)
WHERE z.zip_code_prefix BETWEEN 95100 AND 95149 ORDER BY 1"""
    sql4 = """SELECT name, uf, centroid_lat, centroid_lon FROM municipalities
WHERE (name = 'Caxias do Sul' OR name = 'Santa Cruz do Sul') AND uf = 'RS' ORDER BY 1"""
    pts = e.q(sql1)
    z = e.q(sql3)
    home = z[z.zip_code_prefix != 95130].municipality.mode().iloc[0]
    assert (z[z.zip_code_prefix != 95130].municipality == home).all(), z
    m = e.q(sql4).set_index("name")
    km = float(hav_km(m.loc["Santa Cruz do Sul"].centroid_lat, m.loc["Santa Cruz do Sul"].centroid_lon,
                      m.loc[home].centroid_lat, m.loc[home].centroid_lon))
    steps = [step("The pair and its coordinates", uses="q2.spider_local035",
                  result={"prev_city": a.prev_city, "city": a.city, "lat": round(float(a.lat), 4), "lng": round(float(a.lng), 4),
                          "distance_km": round(float(a.distance_km))}),
             step("The points of the two towns", sql=sql1, result=tab(pts),
                  note="One point of santa lucia do piai lies at about 14.6 N, 121.1 E, in the Philippines."),
             step("How common such points are", sql=sql2, result=tab(e.q(sql2))),
             step("Prefix 95130 has that single point; every other prefix from 95100 to 95149 lies in one municipality", sql=sql3, result=tab(z),
                  note=f"Place santa lucia do piai in {home}, the municipality of all neighbouring prefixes."),
             step("Distance between the municipality centroids (IBGE)", sql=sql4, result=tab(m.reset_index()),
                  note=f"Corrected distance: {km:.0f} km.")]
    gold = {"real": False, "corrected_km": round(km, 1)}
    resp = (f"No. Brazil is about 4,400 km across, so 18,000 km is impossible. The jump comes from a wrong point: santa lucia do piai "
            f"has a single geolocation row, at latitude {a.lat:.2f}, longitude {a.lng:.2f}, in the Philippines (42 points of 21 prefixes "
            f"lie outside Brazil). All other prefixes from 95100 to 95149 lie in {home}; between the "
            f"IBGE centroids of {home} and Santa Cruz do Sul the distance is about {km:.0f} km. The Spider answer measures a data error.")
    return steps, gold, resp


@task("q5.order_value_2018", "Q5.2",
      "The average order value (item prices plus freight) of January-August 2018 was about 1% higher than in January-August "
      "2017. How much of the change came from item prices and how much from freight? What was the change after inflation "
      "(IPCA), and in US dollars?",
      {"type": "json", "schema": {"price_share_pct": "float", "freight_share_pct": "float", "real_change_pct": "float",
                                  "usd_change_pct": "float"}},
      depends_on=["q2.monthly_orders"],
      scoring={"metric": "fields", "fields": {"freight_share_pct": {"abs_tol": 10}, "real_change_pct": {"abs_tol": 0.5},
                                              "usd_change_pct": {"abs_tol": 1.0}}})
def order_value(e):
    sql = """WITH o AS (SELECT o.order_id, year(o.order_purchase_timestamp) AS year, CAST(o.order_purchase_timestamp AS DATE) AS day,
             sum(i.price) AS price, sum(i.freight_value) AS freight
           FROM orders o JOIN order_items i USING (order_id)
           WHERE month(o.order_purchase_timestamp) <= 8 AND year(o.order_purchase_timestamp) IN (2017, 2018) GROUP BY 1, 2, 3)
SELECT o.year, count(*) AS orders, round(avg(o.price), 4) AS avg_price, round(avg(o.freight), 4) AS avg_freight,
  round(avg(o.price + o.freight), 4) AS avg_order_value, round(avg((o.price + o.freight) / r.usd_brl), 4) AS avg_order_value_usd
FROM o ASOF JOIN exchange_rates r ON o.day >= r.date GROUP BY 1 ORDER BY 1"""
    sql2 = """WITH i AS (SELECT month, exp(sum(ln(1 + change_pct / 100)) OVER (ORDER BY month)) AS level FROM ipca)
SELECT year(month) AS year, round(avg(level), 6) AS avg_index_jan_aug FROM i
WHERE month(month) <= 8 AND year(month) IN (2017, 2018) GROUP BY 1 ORDER BY 1"""
    a = e.q(sql).set_index("year")
    b = e.q(sql2).set_index("year")
    y7, y8 = a.loc[2017], a.loc[2018]
    d_price, d_freight = y8.avg_price - y7.avg_price, y8.avg_freight - y7.avg_freight
    d = y8.avg_order_value - y7.avg_order_value
    infl = b.loc[2018].avg_index_jan_aug / b.loc[2017].avg_index_jan_aug - 1
    nominal = y8.avg_order_value / y7.avg_order_value - 1
    real = (1 + nominal) / (1 + infl) - 1
    usd = y8.avg_order_value_usd / y7.avg_order_value_usd - 1
    fx = e.q("SELECT year(date) AS year, round(avg(usd_brl), 4) AS avg_usd_brl FROM exchange_rates WHERE month(date) <= 8 GROUP BY 1 ORDER BY 1")
    gold = {"price_share_pct": round(d_price / d * 100, 1), "freight_share_pct": round(d_freight / d * 100, 1),
            "nominal_change_pct": round(nominal * 100, 2), "inflation_pct": round(infl * 100, 2),
            "real_change_pct": round(real * 100, 2), "usd_change_pct": round(usd * 100, 2)}
    steps = [step("Orders and their value by month", uses="q2.monthly_orders"),
             step("Average price, freight and order value per order, in R$ and in US$ at the rate of the purchase day", sql=sql,
                  result=tab(a.reset_index()), note="Each order is converted with the last published rate on or before its purchase day."),
             step("Price level: average IPCA index over January-August of each year (December 2015 = 1)", sql=sql2, result=tab(b.reset_index())),
             step("Average exchange rate, January-August", sql="SELECT year(date) AS year, round(avg(usd_brl), 4) AS avg_usd_brl FROM exchange_rates WHERE month(date) <= 8 GROUP BY 1 ORDER BY 1",
                  result=tab(fx)),
             step("Split the change and deflate", source="calculation",
                  result=dict(gold, price_change_brl=round(d_price, 2), freight_change_brl=round(d_freight, 2)))]
    resp = (f"The average order value went from R$ {y7.avg_order_value:.2f} to R$ {y8.avg_order_value:.2f} ({nominal * 100:+.1f}%). "
            f"Item prices hardly moved ({d_price:+.2f} R$ per order) while freight rose {d_freight:+.2f} R$, so freight explains "
            f"{gold['freight_share_pct']:.0f}% of the change. With consumer prices up {infl * 100:.1f}% (IPCA), the order value fell "
            f"{-real * 100:.1f}% in real terms, and in US dollars it fell {-usd * 100:.1f}% because the real weakened.")
    return steps, gold, resp


# ====================================================================== Q6 Predict
@task("q6.july_orders", "Q6.1", "Using the orders purchased up to May 2018 only, forecast the number of orders purchased in "
      "July 2018. How close is the forecast to the recorded number?", {"type": "number"},
      depends_on=["q2.monthly_orders", "q3.order_growth"], scoring={"metric": "ape", "bands": [0.05, 0.1, 0.2]})
def july_orders(e):
    s = monthly(e, "2017-01-01", "2018-05-01")
    ref = float(s["2018-01-01":"2018-05-01"].mean())
    naive_trend = float(np.polyval(np.polyfit(np.arange(len(s)), s.values, 1), len(s) + 1))
    sql = "SELECT count(*) AS orders_july_2018 FROM orders WHERE order_purchase_timestamp >= '2018-07-01' AND order_purchase_timestamp < '2018-08-01'"
    actual = int(e.v(sql))
    steps = [step("Monthly orders up to May 2018", uses="q2.monthly_orders",
                  result={str(k.date())[:7]: int(v) for k, v in s["2018-01-01":].items()}),
             step("Growth stopped in 2018, so a 2017 trend line overshoots", uses="q3.order_growth", source="calculation",
                  result={"linear_trend_2017_2018": round(naive_trend)}),
             step("Forecast with the 2018 level: mean of January-May 2018", source="calculation", result={"forecast": round(ref)}),
             step("Compare with the orders recorded in July 2018", sql=sql,
                  result={"actual": actual, "error_of_level_forecast": round(ref / actual - 1, 4),
                          "error_of_trend_forecast": round(naive_trend / actual - 1, 4)})]
    gold = {"value": actual}
    resp = (f"About {ref:,.0f} orders: growth ended in 2018 and January-May 2018 averaged {ref:,.0f} a month (a straight trend over "
            f"2017-2018 would give {naive_trend:,.0f}, too high). Recorded: {actual:,}.")
    return steps, gold, resp


@task("q6.holidays_2018", "Q6.2",
      "Carnival Tuesday (February 13) and Good Friday (March 30) fall in 2018. From the 2017 holidays, how much lower should "
      "orders be on these two days than on a normal day of the same weekday? How large was the change on each day?",
      {"type": "json", "schema": {"change_pct_by_day": "dict[date, float]"}}, depends_on=["q3.weekly_cycle"],
      scoring={"metric": "fields", "fields": {"change_pct_by_day": {"abs_tol": 10.0}}})
def holidays_2018(e):
    s = daily_orders(e)
    base = weekday_median(s)
    cal = e.q("""SELECT date, name, kind FROM calendar WHERE kind IN ('national holiday', 'secondary holiday')
                 AND date BETWEEN '2017-02-01' AND '2018-04-30' ORDER BY 1""")
    rows = []
    for r in cal.itertuples():
        d = pd.Timestamp(r.date)
        rows.append(dict(date=str(d.date()), name=r.name, weekday=d.day_name(), orders=int(s[d]), expected=float(base[d]),
                         change_pct=round((s[d] / base[d] - 1) * 100, 1)))
    h = pd.DataFrame(rows)
    h17 = h[(h.date < "2018-01-01") & (h.name.isin(["Carnival (Fat Tuesday)", "Good Friday"]))]
    ref = float(h17.change_pct.mean())
    all17 = h[(h.date < "2018-01-01") & (h.date > "2017-01-31") & ~h.name.isin(["Christmas Day", "Ash Wednesday"])]
    target = h[h.date.isin(["2018-02-13", "2018-03-30"])]
    gold = {"change_pct_by_day": {r.date: r.change_pct for r in target.itertuples()}}
    steps = [step("Orders follow the weekday cycle: compare with the median of the same weekday in the four weeks before",
                  uses="q3.weekly_cycle"),
             step("Daily orders against that baseline on the 2017 and 2018 holidays", source="db",
                  sql="SELECT date, name, kind FROM calendar WHERE kind IN ('national holiday', 'secondary holiday') ORDER BY 1",
                  result=tab(h)),
             step("Expected effect from the same holidays in 2017", source="calculation",
                  result={"carnival_and_good_friday_2017_mean_pct": round(ref, 1),
                          "all_2017_weekday_holidays_mean_pct": round(float(all17.change_pct.mean()), 1)}),
             step("Change on the two days of 2018", source="calculation", result=gold["change_pct_by_day"])]
    resp = (f"In 2017 Carnival Tuesday and Good Friday had about {ref:+.0f}% orders against a normal day of the same weekday, so expect "
            f"roughly that. Recorded in 2018: " + ", ".join(f"{k} {v:+.0f}%" for k, v in gold["change_pct_by_day"].items()) + ".")
    return steps, gold, resp


@task("q6.promise_whatif", "Q6.3",
      "Orders bought in August 2018 were promised much faster delivery than before. If their promised dates had been 5 days "
      "later, what share of the delivered August orders would have been late, instead of the recorded share? And with the "
      "promise lengths of June 2018 orders?", {"type": "json", "schema": {"late_pct_plus5": "float", "late_pct_june_promise": "float"}},
      depends_on=["q4.promise_shift"],
      scoring={"metric": "fields", "fields": {"late_pct_plus5": {"abs_tol": 0.5}, "late_pct_june_promise": {"abs_tol": 1.0}}})
def promise_whatif(e):
    sql = f"""SELECT count(*) AS delivered, round(avg({LATE}::INT) * 100, 2) AS late_pct_recorded,
  round(avg((CAST(order_delivered_customer_date AS DATE) > CAST(order_estimated_delivery_date AS DATE) + 5)::INT) * 100, 2) AS late_pct_plus5
FROM orders WHERE {DELIVERED} AND order_purchase_timestamp >= '2018-08-01' AND order_purchase_timestamp < '2018-09-01'"""
    sql2 = f"""WITH j AS (SELECT c.customer_state, avg(date_diff('day', CAST(o.order_purchase_timestamp AS DATE), CAST(o.order_estimated_delivery_date AS DATE))) AS june_promise
           FROM orders o JOIN customers c USING (customer_id)
           WHERE o.order_purchase_timestamp >= '2018-06-01' AND o.order_purchase_timestamp < '2018-07-01' GROUP BY 1)
SELECT count(*) AS delivered,
  round(avg((date_diff('day', CAST(o.order_purchase_timestamp AS DATE), CAST(o.order_delivered_customer_date AS DATE)) > round(j.june_promise))::INT) * 100, 2) AS late_pct_june_promise
FROM orders o JOIN customers c USING (customer_id) JOIN j USING (customer_state)
WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_purchase_timestamp >= '2018-08-01' AND o.order_purchase_timestamp < '2018-09-01'"""
    a, b = e.q(sql).iloc[0], e.q(sql2).iloc[0]
    gold = {"late_pct_recorded": float(a.late_pct_recorded), "late_pct_plus5": float(a.late_pct_plus5),
            "late_pct_june_promise": float(b.late_pct_june_promise)}
    steps = [step("The promise was cut in mid-2018", uses="q4.promise_shift"),
             step("August orders: recorded late share, and with each promise 5 days later", sql=sql, result=tab(e.q(sql))),
             step("August orders judged against the June 2018 average promise of their state", sql=sql2, result=tab(e.q(sql2)),
                  note="The actual delivery days stay as recorded; only the promise changes.")]
    resp = (f"Recorded: {a.late_pct_recorded}% of delivered August 2018 orders were late. With promises 5 days later, "
            f"{a.late_pct_plus5}% would have been late; with the June 2018 promise lengths of each state, {b.late_pct_june_promise}%. "
            "The faster promise costs late deliveries, which the reviews punish.")
    return steps, gold, resp


# ====================================================================== Q7 Prescribe
@task("q7.delivery_hubs", "Q7.2",
      "We want to open 3 regional delivery hubs, each in the largest municipality of a state. Using the orders of January to "
      "August 2018 by customer municipality, choose the 3 municipalities that minimize the average great-circle distance from "
      "a customer's municipality to its nearest hub.",
      {"type": "json", "schema": {"hubs": "list[str]", "avg_km": "float"}}, depends_on=["q1.zip_prefix"],
      scoring={"metric": "optimality_gap", "field": "avg_km", "tol": 0.01})
def delivery_hubs(e):
    sql1 = """SELECT m.municipality_code, m.name, m.uf, m.centroid_lat AS lat, m.centroid_lon AS lon, count(*) AS orders
FROM orders o JOIN customers c USING (customer_id) JOIN zip_prefixes z ON z.zip_code_prefix = c.customer_zip_code_prefix
JOIN municipalities m USING (municipality_code)
WHERE o.order_purchase_timestamp >= '2018-01-01' AND o.order_purchase_timestamp < '2018-09-01' GROUP BY ALL ORDER BY orders DESC"""
    sql2 = """SELECT municipality_code, name, uf, centroid_lat AS lat, centroid_lon AS lon, population_2018 FROM municipalities m
WHERE population_2018 = (SELECT max(population_2018) FROM municipalities x WHERE x.uf = m.uf) ORDER BY uf"""
    dem, cand = e.q(sql1), e.q(sql2)
    tot = int(e.v("SELECT count(*) FROM orders WHERE order_purchase_timestamp >= '2018-01-01' AND order_purchase_timestamp < '2018-09-01'"))
    D = hav_km(dem.lat.values[:, None], dem.lon.values[:, None], cand.lat.values[None, :], cand.lon.values[None, :])
    w = dem.orders.values.astype(float)
    best = None
    for comb in itertools.combinations(range(len(cand)), 3):
        cost = float((D[:, comb].min(axis=1) * w).sum() / w.sum())
        if best is None or cost < best[0]:
            best = (cost, comb)
    greedy = []
    for _ in range(3):
        greedy.append(min((j for j in range(len(cand)) if j not in greedy),
                          key=lambda j: (D[:, greedy + [j]].min(axis=1) * w).sum()))
    gcost = float((D[:, greedy].min(axis=1) * w).sum() / w.sum())
    one = float((D.min(axis=1) * w).sum() / w.sum())
    hubs = cand.iloc[list(best[1])]
    assign = D[:, best[1]].argmin(axis=1)
    share = {f"{hubs.iloc[k]['name']} ({hubs.iloc[k].uf})": round(float(w[assign == k].sum() / w.sum() * 100), 1) for k in range(3)}
    gold = {"hubs": [f"{r.name} ({r.uf})" for r in hubs.itertuples()], "avg_km": round(best[0], 1),
            "greedy_hubs": [f"{cand.iloc[j]['name']} ({cand.iloc[j].uf})" for j in greedy], "greedy_avg_km": round(gcost, 1)}
    steps = [step("Customer ZIP prefixes give a municipality through one point per prefix", uses="q1.zip_prefix",
                  result={"orders_jan_aug_2018": tot, "orders_located": int(w.sum())}),
             step("Orders by customer municipality", sql=sql1, result=tab(dem.round(3), limit=10)),
             step("Candidates: the most populous municipality of each state", sql=sql2, result=tab(cand.round(3))),
             step("Evaluate all 2,925 sets of 3 candidates (exact p-median)", source="calculation",
                  result={"best_avg_km": gold["avg_km"], "hubs": gold["hubs"], "orders_served_pct": share,
                          "avg_km_with_a_hub_in_every_state": round(one, 1)}),
             step("Compare with adding hubs one at a time (greedy)", source="calculation",
                  result={"greedy_hubs": gold["greedy_hubs"], "greedy_avg_km": gold["greedy_avg_km"]},
                  note="Here the greedy choice reaches the optimum; the exhaustive search proves it.")]
    resp = (f"Open hubs in {', '.join(gold['hubs'])}: the average customer is then {gold['avg_km']} km from the nearest hub. "
            + "Orders served: " + ", ".join(f"{k} {v}%" for k, v in share.items())
            + f". Adding hubs one at a time gives {', '.join(gold['greedy_hubs'])} at {gold['greedy_avg_km']} km.")
    return steps, gold, resp


@task("q7.lead_channels", "Q7.1",
      "The marketing team recruits sellers through several channels. Which channels should get more budget and which less? "
      "Support the advice with the conversion of leads into sellers and the sales those sellers made.", TEXT,
      depends_on=["q2.funnel_period"])
def lead_channels(e):
    sql = """WITH s AS (SELECT i.seller_id, count(DISTINCT i.order_id) AS orders, sum(i.price) AS revenue FROM order_items i GROUP BY 1)
SELECT coalesce(q.origin, '(missing)') AS origin, count(*) AS leads, count(c.mql_id) AS closed,
  round(count(c.mql_id) * 100.0 / count(*), 1) AS conversion_pct, count(s.seller_id) AS sellers_with_sales,
  round(sum(coalesce(s.revenue, 0))) AS revenue, round(sum(coalesce(s.revenue, 0)) / count(*), 1) AS revenue_per_lead
FROM leads_qualified q LEFT JOIN leads_closed c USING (mql_id) LEFT JOIN s USING (seller_id)
GROUP BY 1 ORDER BY leads DESC"""
    sql2 = """SELECT q.origin, count(*) AS closed, median(date_diff('day', q.first_contact_date, CAST(c.won_date AS DATE))) AS median_days_to_close
FROM leads_closed c JOIN leads_qualified q USING (mql_id) GROUP BY 1 ORDER BY 2 DESC"""
    sql3 = """SELECT count(*) AS closed, count(*) FILTER (WHERE seller_id IN (SELECT seller_id FROM order_items)) AS with_sales,
  min(won_date)::DATE AS first_won, max(won_date)::DATE AS last_won FROM leads_closed"""
    a = e.q(sql).set_index("origin")
    c = e.q(sql3).iloc[0]
    tot = a.sum()
    steps = [step("The funnel: leads, closed deals, sellers", uses="q2.funnel_period"),
             step("Conversion and sales by channel", sql=sql, result=tab(a.reset_index())),
             step("Time to close by channel", sql=sql2, result=tab(e.q(sql2))),
             step("Most closed sellers have no sales yet in the order records", sql=sql3, result=tab(e.q(sql3)),
                  note="Deals closed until November 2018; the order records end in August 2018.")]
    gold = {"key_facts": [
        f"Search brings most leads and converts well: organic search {a.loc['organic_search'].conversion_pct}% and paid search "
        f"{a.loc['paid_search'].conversion_pct}%, with the most sales per lead among the tracked channels "
        f"(R$ {a.loc['organic_search'].revenue_per_lead} and R$ {a.loc['paid_search'].revenue_per_lead}); keep or grow them.",
        f"Social ({a.loc['social'].conversion_pct}%), email ({a.loc['email'].conversion_pct}%), display "
        f"({a.loc['display'].conversion_pct}%) and other publicities ({a.loc['other_publicities'].conversion_pct}%) convert poorly and "
        "bring little revenue per lead; cut or rework them.",
        f"The 'unknown' channel has the highest conversion ({a.loc['unknown'].conversion_pct}%) and revenue per lead "
        f"(R$ {a.loc['unknown'].revenue_per_lead}): fix the tracking before deciding, since budget cannot follow an untracked source.",
        f"Only {int(c.with_sales)} of {int(c.closed)} closed sellers have sales in the order records (deals run to "
        f"{str(c.last_won)[:10]}, orders end in August 2018), so revenue figures favour channels that closed early."],
        "required": [0, 1, 2]}
    resp = (f"Grow search: organic and paid search bring {int(a.loc['organic_search'].leads + a.loc['paid_search'].leads):,} of "
            f"{int(tot.leads):,} leads and convert at {a.loc['organic_search'].conversion_pct}% and {a.loc['paid_search'].conversion_pct}%, "
            f"with about R$ {a.loc['organic_search'].revenue_per_lead:.0f}-{a.loc['paid_search'].revenue_per_lead:.0f} of later sales per lead. "
            f"Cut or rework social ({a.loc['social'].conversion_pct}%), email ({a.loc['email'].conversion_pct}%) and display "
            f"({a.loc['display'].conversion_pct}%). The best-converting group is 'unknown' ({a.loc['unknown'].conversion_pct}%): fix "
            f"attribution first. Note that only {int(c.with_sales)} of {int(c.closed)} new sellers have sales yet in the records.")
    return steps, gold, resp


def build(build=OUT):
    return T.build(Env(build), build / "tasks.json")


if __name__ == "__main__":
    items = build()
    for x in items:
        print(f"{x['id']:28s} {x['task_type']:6s} steps={len(x['steps'])} deps={x['depends_on']}")
