# Sources of the Brazilian e-commerce environment

All files are public. Small files are kept in `raw/`. The marketplace database, the IBGE meshes and the municipal GDP
file are downloaded by `scripts/fetch_sources.py br_ecommerce` (with SHA-256 checks) and are not in the repository.

| Kept here | Downloaded by the scripts |
|---|---|
| `raw/estimativa_dou_2018_20181019.ods`, `raw/bcb_*.json`, `raw/kaggle_*_metadata.json`, `raw/wikipedia_*.json` | `raw/E_commerce.sqlite` (from the Spider 2.0-lite archive), `raw/br_municipios.zip`, `raw/br_unidades_da_federacao.zip`, `raw/base_de_dados_2010_2017_txt.zip` |

| File | Source | Use | License or terms |
|---|---|---|---|
| `raw/E_commerce.sqlite` | Spider 2.0-lite local databases, archive linked from the spider2-lite README (Google Drive id `1coEVsCZq-Xvj9p2TnhBFoFTsY-UoYGmG`); only this file is extracted | orders, order_items, order_payments, order_reviews, customers, sellers, products, product_category_name_translation, geolocation, leads_qualified, leads_closed | the records are the Brazilian E-Commerce Public Dataset by Olist and the Marketing Funnel by Olist (Kaggle), CC BY-NC-SA 4.0; Spider 2.0 is MIT |
| `raw/br_unidades_da_federacao.zip`, `raw/br_municipios.zip` | IBGE 2018 meshes at 1:250,000, `https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2018/Brasil/BR/` | states, municipalities, ZIP prefix to municipality | IBGE data, free use with citation of the source |
| `raw/estimativa_dou_2018_20181019.ods` | IBGE population estimates of July 1, 2018 (published in the DOU on October 19, 2018), `https://ftp.ibge.gov.br/Estimativas_de_Populacao/Estimativas_2018/` | population of states and municipalities | IBGE |
| `raw/base_de_dados_2010_2017_txt.zip` | IBGE PIB dos Municípios 2010-2017, `https://ftp.ibge.gov.br/Pib_Municipios/2017/base/` (fixed-width text; the layout file in the archive gives the fields) | municipal_gdp | IBGE |
| `raw/bcb_sgs1_usd_brl_2016_2018.json`, `raw/bcb_sgs433_ipca_2016_2018.json` | Banco Central do Brasil SGS API, series 1 and 433, 2016-2018 (fetched October 2026) | exchange_rates, ipca | public data |
| `raw/kaggle_brazilian_ecommerce_metadata.json`, `raw/kaggle_marketing_funnel_metadata.json` | Kaggle dataset metadata (`https://www.kaggle.com/api/v1/datasets/view/olistbr/...`), fetched October 2026 | the dataset descriptions quoted by the tasks | CC BY-NC-SA 4.0, as the datasets |
| `raw/wikipedia_*.json` | Wikipedia API extracts: 2018 Brazil truck drivers' strike (revision 1374527943), Black Friday (shopping) (revision 1375327511), Public holidays in Brazil (revision 1363742709) | quoted documents; the holiday rules of `calendar` | CC BY-SA 4.0 |

## Checks

- Spider 2.0-lite has two copies of the order records: `E_commerce.sqlite` (tasks local002-local004, with the marketing
  funnel) and `Brazilian_E_Commerce.sqlite` (tasks local028-local037, tables with an `olist_` prefix and an extra
  duplicate `olist_products_dataset`). Their order tables hold the same rows (compared row by row), so the environment
  uses the first one and both sets of Spider tasks run on it.
- Row counts: 99,441 orders, 112,650 items, 103,886 payments, 99,224 reviews, 32,951 products, 3,095 sellers,
  1,000,163 geolocation points, 8,000 leads and 842 closed deals, in line with the dataset descriptions ("100k
  orders", "8k Marketing Qualified Leads").
- On these records the Spider 2.0-lite tasks local004, local028, local029, local030, local031, local032, local034,
  local035 and local037 reproduce their gold results (local034 has two gold variants, with and without the products
  that have no category). local002 and local003 accept several gold answers that disagree with each other; the task
  q1.rfm_document shows why for local003 (frequency quintiles split tied customers arbitrarily).
- The IBGE file `base_de_dados_2010_2016_txt.zip` of the 2016 release contains the 2002-2009 data despite its name,
  so the 2017 release (2010-2017) is used. The São Paulo GDP of 2017 in the file, R$ 699,288,352 thousand, matches
  IBGE's published figure.
