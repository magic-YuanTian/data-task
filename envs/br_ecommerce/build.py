"""Build the Brazilian e-commerce environment from real records only.

    python -m envs.br_ecommerce.build        # writes out/br_ecommerce/world.duckdb and catalog/

The marketplace tables are the public order records and marketing funnel of a Brazilian marketplace, as distributed
with Spider 2.0-lite (E_commerce.sqlite). All rows are kept, duplicates and inconsistencies included. The only changes
are types: text timestamps become TIMESTAMP or DATE. The public context comes from IBGE (states, municipalities,
population, municipal GDP), Banco Central do Brasil (exchange rate, inflation) and the holiday calendar. Every
table, and every change, is listed in SOURCES.
"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from datataskbench.paths import OUT as OUT_ROOT
from datataskbench.paths import repo_path

from .common import brazil_calendar

HERE = Path(__file__).parent
CFG = yaml.safe_load(open(HERE / "config.yaml"))
OUT = OUT_ROOT / "br_ecommerce"
KAGGLE = "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce"
FUNNEL = "https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist"
SPIDER = "Spider 2.0-lite local database E_commerce.sqlite (tasks local002, local003, local004); the same order tables " \
         "are Brazilian_E_Commerce.sqlite (local028-local037), with an olist_ prefix"
OLIST = dict(source="Brazilian E-Commerce Public Dataset by Olist (Kaggle, version 2), as distributed with Spider 2.0-lite",
             url=KAGGLE, benchmark=SPIDER)
SOURCES = {
    "orders": dict(OLIST, modified="timestamps stored as TIMESTAMP (text in the source)"),
    "order_items": dict(OLIST, modified="shipping_limit_date stored as TIMESTAMP"),
    "order_payments": dict(OLIST),
    "order_reviews": dict(OLIST, modified="review_creation_date and review_answer_timestamp stored as TIMESTAMP"),
    "customers": dict(OLIST),
    "sellers": dict(OLIST),
    "products": dict(OLIST, modified="none (the misspelled column names product_name_lenght and "
                                     "product_description_lenght are kept as published)"),
    "product_category_name_translation": dict(OLIST),
    "geolocation": dict(OLIST, modified="none: all 1,000,163 rows, duplicates included"),
    "leads_qualified": dict(source="Marketing Funnel by Olist (Kaggle), as distributed with Spider 2.0-lite", url=FUNNEL,
                            benchmark="Spider 2.0-lite local database E_commerce.sqlite",
                            modified="first_contact_date stored as DATE"),
    "leads_closed": dict(source="Marketing Funnel by Olist (Kaggle), as distributed with Spider 2.0-lite", url=FUNNEL,
                         benchmark="Spider 2.0-lite local database E_commerce.sqlite", modified="won_date stored as TIMESTAMP"),
    "zip_prefixes": dict(source="derived from geolocation; municipality by spatial join with the IBGE 2018 municipal mesh",
                         modified="one row per ZIP code prefix: number of points, median position, most frequent city and "
                                  "state, and the municipality that contains the median position"),
    "states": dict(source="IBGE: 2018 state mesh (1:250,000) and 2018 population estimates (DOU, 2018-10-19)",
                   url="https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2018/Brasil/BR/",
                   modified="names, regions and polygons from the mesh; population from the estimates"),
    "municipalities": dict(source="IBGE: 2018 municipal mesh (1:250,000) and 2018 population estimates (DOU, 2018-10-19)",
                           url="https://ftp.ibge.gov.br/Estimativas_de_Populacao/Estimativas_2018/",
                           modified="population footnotes (court decisions) kept in population_note"),
    "municipal_gdp": dict(source="IBGE: PIB dos Municípios, base de dados 2010-2017 (released 2019)",
                          url="https://ftp.ibge.gov.br/Pib_Municipios/2017/base/",
                          modified="the fixed-width text file split into columns; values in R$ 1,000 (per capita in R$)"),
    "exchange_rates": dict(source="Banco Central do Brasil, SGS series 1: US dollar exchange rate (selling, free market), daily",
                           url="https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados?formato=json"),
    "ipca": dict(source="Banco Central do Brasil, SGS series 433: IPCA consumer price index, monthly change in %",
                 url="https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados?formato=json"),
    "calendar": dict(source="Wikipedia, Public holidays in Brazil (revision 1363742709) and Black Friday (shopping) "
                            "(revision 1375327511)",
                     url="https://en.wikipedia.org/wiki/Public_holidays_in_Brazil",
                     modified="dates computed for 2016-2018 from the rules on those pages (Easter for movable days); "
                              "Black Consciousness Day left out (national only from 2024)"),
}


def _wiki(name):
    d = json.load(open(repo_path(CFG["source"]["raw"]) / name))
    if "parse" in d:
        return d["parse"]["wikitext"]["*"]
    return next(iter(d["query"]["pages"].values()))["extract"]


def _kaggle(name):
    return json.load(open(repo_path(CFG["source"]["raw"]) / name))["description"]


def _sentences(text, *starts):
    """The sentences (or lines) of a source text that start with the given words, verbatim."""
    out = []
    for s in starts:
        m = re.search(re.escape(s) + r".*?(?:\.(?=\s)|\n|$)", text, re.S)
        assert m, s
        out.append(" ".join(m.group(0).split()))
    return out


def documents() -> dict:
    k1, k2 = _kaggle("kaggle_brazilian_ecommerce_metadata.json"), _kaggle("kaggle_marketing_funnel_metadata.json")
    strike, bf = _wiki("wikipedia_2018_brazil_truck_drivers_strike.json"), _wiki("wikipedia_black_friday_shopping.json")
    docs_dir = repo_path(CFG["source"]["spider_docs"])
    rfm = (docs_dir / "RFM.md").read_text()
    return {
        "dataset": dict(document="Brazilian E-Commerce Public Dataset by Olist: dataset description (Kaggle)", url=KAGGLE,
                        text=_sentences(k1, "The dataset has information of 100k orders", "This is real commercial data",
                                        "After a customer purchases the product", "Once the customer receives the product",
                                        "1. An order might have multiple items.", "2. Each item might be fulfilled",
                                        "We also released a geolocation dataset")),
        "funnel": dict(document="Marketing Funnel by Olist: dataset description (Kaggle)", url=FUNNEL,
                       text=_sentences(k2, "The dataset has information of 8k Marketing Qualified Leads",
                                       "This is real data, it has been anonymized", "1. Sign-up at a landing page.",
                                       "1. Get contacted by a Sales development Representative",
                                       "1. Consultancy is made by a Sales Representative",
                                       "1. Lead becomes a seller", "1. A seller MQL might come from multiple sources",
                                       "This dataset can also be linked")),
        "rfm": dict(document="RFM definition document (Spider 2.0-lite, RFM.md)",
                    url="https://github.com/xlang-ai/Spider2/blob/main/spider2-lite/resource/documents/RFM.md",
                    text=_sentences(rfm, "Each customer’s R, F, and M scores", "1. Determine each customer’s recency score",
                                    "2. Determine each customer’s frequency score", "3. Determine each customer’s monetary score",
                                    "- **Champions**", "  - Criteria: Recency = 1 and Frequency + Monetary score between 1 and 4.",
                                    "- **Can't Lose Them**",
                                    "  - Criteria: Recency = 4 or 5 and Frequency + Monetary score between 1 and 2.")),
        "strike": dict(document="Wikipedia: 2018 Brazil truck drivers' strike (revision 1374527943)",
                       url="https://en.wikipedia.org/wiki/2018_Brazil_truck_drivers%27_strike",
                       text=_sentences(strike, "The 2018 Brazil truck drivers' strike", "The nationwide paralysis of roads",
                                       "During the first day of the strike", "At this time there were still 556 active blockades",
                                       "At this time there were 3 complete blockades")),
        "black_friday": dict(document="Wikipedia: Black Friday (shopping) (revision 1375327511)",
                             url="https://en.wikipedia.org/wiki/Black_Friday_(shopping)",
                             text=_sentences(bf, "Black Friday is the Friday after Thanksgiving in the United States.",
                                             "Black Friday has been increasingly adopted by stores in Brazil since 2010")),
    }


def load_marketplace(con, cfg):
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{repo_path(cfg['source']['sqlite'])}' AS src (TYPE sqlite, READ_ONLY)")
    ts = lambda c: f"CAST({c} AS TIMESTAMP) AS {c}"
    con.execute(f"""CREATE TABLE orders AS SELECT order_id, customer_id, order_status, {ts('order_purchase_timestamp')},
        {ts('order_approved_at')}, {ts('order_delivered_carrier_date')}, {ts('order_delivered_customer_date')},
        {ts('order_estimated_delivery_date')} FROM src.orders ORDER BY order_purchase_timestamp, order_id""")
    con.execute(f"""CREATE TABLE order_items AS SELECT order_id, CAST(order_item_id AS INTEGER) AS order_item_id, product_id,
        seller_id, {ts('shipping_limit_date')}, price, freight_value FROM src.order_items ORDER BY order_id, order_item_id""")
    con.execute("""CREATE TABLE order_payments AS SELECT order_id, CAST(payment_sequential AS INTEGER) AS payment_sequential,
        payment_type, CAST(payment_installments AS INTEGER) AS payment_installments, payment_value
        FROM src.order_payments ORDER BY order_id, payment_sequential""")
    con.execute(f"""CREATE TABLE order_reviews AS SELECT review_id, order_id, CAST(review_score AS INTEGER) AS review_score,
        review_comment_title, review_comment_message, {ts('review_creation_date')}, {ts('review_answer_timestamp')}
        FROM src.order_reviews ORDER BY review_creation_date, review_id, order_id""")
    con.execute("""CREATE TABLE customers AS SELECT customer_id, customer_unique_id,
        CAST(customer_zip_code_prefix AS INTEGER) AS customer_zip_code_prefix, customer_city, customer_state
        FROM src.customers ORDER BY customer_id""")
    con.execute("""CREATE TABLE sellers AS SELECT seller_id, CAST(seller_zip_code_prefix AS INTEGER) AS seller_zip_code_prefix,
        seller_city, seller_state FROM src.sellers ORDER BY seller_id""")
    con.execute("CREATE TABLE products AS SELECT * FROM src.products ORDER BY product_id")
    con.execute("CREATE TABLE product_category_name_translation AS SELECT * FROM src.product_category_name_translation ORDER BY 1")
    con.execute("""CREATE TABLE geolocation AS SELECT CAST(geolocation_zip_code_prefix AS INTEGER) AS geolocation_zip_code_prefix,
        geolocation_lat, geolocation_lng, geolocation_city, geolocation_state FROM src.geolocation
        ORDER BY ALL""")
    con.execute("""CREATE TABLE leads_qualified AS SELECT mql_id, CAST(first_contact_date AS DATE) AS first_contact_date,
        landing_page_id, origin FROM src.leads_qualified ORDER BY first_contact_date, mql_id""")
    con.execute(f"""CREATE TABLE leads_closed AS SELECT mql_id, seller_id, sdr_id, sr_id, {ts('won_date')}, business_segment,
        lead_type, lead_behaviour_profile, CAST(has_company AS INTEGER) AS has_company, CAST(has_gtin AS INTEGER) AS has_gtin,
        average_stock, business_type, declared_product_catalog_size, declared_monthly_revenue
        FROM src.leads_closed ORDER BY won_date, mql_id""")
    con.execute("DETACH src")


# PIB dos Municípios 2010-2017: fixed-width text; each field runs from its start to the next field's start
GDP_FIELDS = [
    (0, "year", "INTEGER"), (5, "region_code", "INTEGER"), (7, "region", None), (20, "uf_code", "INTEGER"), (23, "uf", None),
    (26, "uf_name", None), (46, "municipality_code", "INTEGER"), (54, "municipality", None), (95, "metropolitan_region", None),
    (206, "mesoregion_code", "INTEGER"), (211, "mesoregion", None), (252, "microregion_code", "INTEGER"),
    (258, "microregion", None), (299, "immediate_region_code", "INTEGER"), (306, "immediate_region", None),
    (373, "immediate_region_role", None), (384, "intermediate_region_code", "INTEGER"), (389, "intermediate_region", None),
    (425, "intermediate_region_role", None), (436, "urban_concentration_code", "INTEGER"), (444, "urban_concentration", None),
    (512, "urban_concentration_type", None), (600, "population_arrangement_code", "INTEGER"),
    (608, "population_arrangement", None), (695, "urban_hierarchy", None), (728, "urban_hierarchy_main", None),
    (747, "rural_region_code", "INTEGER"), (752, "rural_region", None), (844, "rural_region_class", None),
    (888, "legal_amazon", None), (893, "semiarid", None), (898, "sao_paulo_city_region", None),
    (902, "va_agriculture", "DOUBLE"), (922, "va_industry", "DOUBLE"), (941, "va_services", "DOUBLE"),
    (960, "va_public_administration", "DOUBLE"), (979, "va_total", "DOUBLE"), (998, "taxes_on_products", "DOUBLE"),
    (1017, "gdp", "DOUBLE"), (1036, "gdp_per_capita", "DOUBLE"), (1055, "top_activity_1", None),
    (1150, "top_activity_2", None), (1245, "top_activity_3", None),
]


def read_gdp(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(".txt"))
        lines = z.read(name).decode("latin1").splitlines()
    starts = [s for s, _, _ in GDP_FIELDS] + [None]
    rows = [[ln[a:b].strip() or None for a, b in zip(starts[:-1], starts[1:])] for ln in lines if ln.strip()]
    df = pd.DataFrame(rows, columns=[n for _, n, _ in GDP_FIELDS])
    for _, n, t in GDP_FIELDS:
        if t == "INTEGER":
            df[n] = pd.to_numeric(df[n]).astype("Int64")
        elif t == "DOUBLE":
            df[n] = pd.to_numeric(df[n])
    return df


def _population(con, ods):
    """Municipal and state population estimates of July 1, 2018; footnote markers split off."""
    m = con.execute(f"SELECT * FROM ST_Read('{ods}', layer='Municípios', open_options=['HEADERS=DISABLE'])").df()
    m = m[m.Field2.astype(str).str.fullmatch(r"\d\d") & m.Field3.notna()].copy()
    notes = {}
    for t in con.execute(f"SELECT Field1 FROM ST_Read('{ods}', layer='Municípios', open_options=['HEADERS=DISABLE'])").fetchall():
        mm = re.match(r"^\((\d+)\)\s*(.*)", str(t[0]))
        if mm:
            notes[mm.group(1)] = " ".join(mm.group(2).split())
    pop = m.Field5.astype(str).str.extract(r"^([\d.]+)\s*(?:\((\d+)\))?$")
    m["population_2018"] = pop[0].str.replace(".", "", regex=False).astype(int)
    m["population_note"] = pop[1].map(notes)
    m["municipality_code"] = (m.Field2.astype(str) + m.Field3.astype(str)).astype(int)
    m = m.rename(columns={"Field1": "uf", "Field4": "name"})[["municipality_code", "name", "uf", "population_2018", "population_note"]]
    s = con.execute(f"SELECT * FROM ST_Read('{ods}', layer='BRASIL_E_UFs', open_options=['HEADERS=DISABLE'])").df()
    s = s[s.Field3.notna() & ~s.Field1.astype(str).str.startswith(("Brasil", "Região", "BRASIL", "ESTIMATIVAS"))].copy()
    sp = s.Field3.astype(str).str.extract(r"^([\d.]+)\s*(\(\*+\))?$")
    s["population_2018"] = sp[0].str.replace(".", "", regex=False).astype(int)
    s["population_note"] = sp[1]
    for t in con.execute(f"SELECT Field1 FROM ST_Read('{ods}', layer='BRASIL_E_UFs', open_options=['HEADERS=DISABLE'])").fetchall():
        mm = re.match(r"^\((\*+)\)\s*(.*)", str(t[0]))
        if mm:
            s.loc[s.population_note == f"({mm.group(1)})", "population_note"] = " ".join(mm.group(2).split())
    return m, s.rename(columns={"Field1": "name"})[["name", "population_2018", "population_note"]]


def load_context(con, cfg):
    raw = repo_path(cfg["source"]["raw"])
    con.execute("INSTALL spatial; LOAD spatial;")
    mun_pop, st_pop = _population(con, raw / "estimativa_dou_2018_20181019.ods")
    con.register("_mpop", mun_pop)
    con.register("_spop", st_pop)
    con.execute(f"""CREATE TABLE states AS
        WITH g AS (SELECT CAST(CD_GEOCUF AS INTEGER) AS uf_code, NM_ESTADO AS name_upper, NM_REGIAO AS region_upper, geom
                   FROM ST_Read('/vsizip/{raw}/br_unidades_da_federacao.zip/BRUFE250GC_SIR.shp')),
        u AS (SELECT DISTINCT floor(municipality_code / 100000)::INTEGER AS uf_code, uf FROM _mpop)
        SELECT g.uf_code, u.uf, p.name, CASE g.region_upper WHEN 'NORTE' THEN 'Norte' WHEN 'NORDESTE' THEN 'Nordeste'
          WHEN 'SUDESTE' THEN 'Sudeste' WHEN 'SUL' THEN 'Sul' WHEN 'CENTRO-OESTE' THEN 'Centro-Oeste' END AS region,
          p.population_2018, p.population_note, ST_Area_Spheroid(ST_FlipCoordinates(g.geom)) / 1e6 AS area_km2,
          ST_AsText(g.geom) AS state_geom
        FROM g JOIN u USING (uf_code) JOIN _spop p ON upper(strip_accents(p.name)) = upper(strip_accents(g.name_upper))
        ORDER BY 1""")
    n = con.execute("SELECT count(*) FROM states").fetchone()[0]
    assert n == 27, n
    con.execute(f"""CREATE TABLE municipalities AS
        WITH g AS (SELECT CAST(CD_GEOCMU AS INTEGER) AS municipality_code, NM_MUNICIP AS name_upper, geom
                   FROM ST_Read('/vsizip/{raw}/br_municipios.zip/BRMUE250GC_SIR.shp'))
        SELECT g.municipality_code, coalesce(p.name, g.name_upper) AS name, coalesce(p.uf, s.uf) AS uf, p.population_2018,
          p.population_note, ST_Area_Spheroid(ST_FlipCoordinates(g.geom)) / 1e6 AS area_km2,
          ST_Y(ST_Centroid(g.geom)) AS centroid_lat, ST_X(ST_Centroid(g.geom)) AS centroid_lon,
          ST_AsText(g.geom) AS municipality_geom
        FROM g LEFT JOIN _mpop p USING (municipality_code)
        LEFT JOIN states s ON s.uf_code = floor(g.municipality_code / 100000)
        ORDER BY 1""")
    con.execute(f"""CREATE TEMP TABLE _mun_geom AS SELECT CAST(CD_GEOCMU AS INTEGER) AS municipality_code, geom
        FROM ST_Read('/vsizip/{raw}/br_municipios.zip/BRMUE250GC_SIR.shp')""")
    gdp = read_gdp(raw / "base_de_dados_2010_2017_txt.zip")
    con.register("_gdp", gdp)
    con.execute("CREATE TABLE municipal_gdp AS SELECT * FROM _gdp ORDER BY year, municipality_code")
    fx = pd.DataFrame(json.load(open(raw / "bcb_sgs1_usd_brl_2016_2018.json")))
    con.register("_fx", fx)
    con.execute("""CREATE TABLE exchange_rates AS SELECT strptime(data, '%d/%m/%Y')::DATE AS date,
        CAST(valor AS DOUBLE) AS usd_brl FROM _fx ORDER BY 1""")
    ip = pd.DataFrame(json.load(open(raw / "bcb_sgs433_ipca_2016_2018.json")))
    con.register("_ip", ip)
    con.execute("""CREATE TABLE ipca AS SELECT strptime(data, '%d/%m/%Y')::DATE AS month,
        CAST(valor AS DOUBLE) AS change_pct FROM _ip ORDER BY 1""")
    con.register("_cal", brazil_calendar(cfg["holiday_years"]))
    con.execute("CREATE TABLE calendar AS SELECT CAST(date AS DATE) AS date, name, kind FROM _cal ORDER BY date, name")


def load_zip_prefixes(con):
    """One row per ZIP code prefix of geolocation: points, median position, usual city and state, and municipality."""
    con.execute("""CREATE TABLE zip_prefixes AS
        WITH p AS (SELECT geolocation_zip_code_prefix AS zip_code_prefix, count(*) AS n_points,
                     median(geolocation_lat) AS lat, median(geolocation_lng) AS lon,
                     mode(geolocation_state ORDER BY geolocation_state) AS state FROM geolocation GROUP BY 1),
        c AS (SELECT geolocation_zip_code_prefix AS zip_code_prefix,
                arg_max(geolocation_city, (n, geolocation_city)) AS city FROM (
                  SELECT geolocation_zip_code_prefix, geolocation_city, count(*) AS n FROM geolocation GROUP BY 1, 2) GROUP BY 1),
        m AS (SELECT p.zip_code_prefix, g.municipality_code, mu.uf
              FROM p JOIN _mun_geom g ON ST_Within(ST_Point(p.lon, p.lat), g.geom)
              JOIN municipalities mu USING (municipality_code))
        SELECT p.zip_code_prefix, p.n_points, p.lat, p.lon, c.city, p.state, m.municipality_code,
          m.uf IS NOT NULL AND m.uf = p.state AS in_state
        FROM p JOIN c USING (zip_code_prefix) LEFT JOIN m USING (zip_code_prefix) ORDER BY 1""")


def write_catalog(out: Path):
    cat = out / "catalog"
    if cat.exists():
        shutil.rmtree(cat)
    shutil.copytree(HERE / "catalog", cat)
    (cat / "sources.json").write_text(json.dumps(SOURCES, indent=1, ensure_ascii=False) + "\n")
    (cat / "documents.json").write_text(json.dumps(documents(), indent=1, ensure_ascii=False) + "\n")


def build(out: Path = OUT):
    out.mkdir(parents=True, exist_ok=True)
    path = out / "world.duckdb"
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    con.execute("SET threads=16")
    load_marketplace(con, CFG)
    load_context(con, CFG)
    load_zip_prefixes(con)
    con.execute("CHECKPOINT")
    con.close()
    write_catalog(out)


if __name__ == "__main__":
    build()
    print(f"wrote {OUT}")
