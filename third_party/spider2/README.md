# Spider 2.0-lite files

Files copied unchanged from https://github.com/xlang-ai/Spider2 at commit 01a4c67c1e3f6ab9032716b050a927abbb245f65
(MIT license, `LICENSE`):

- `spider2-lite/evaluation_suite/gold/exec_result/`: gold results of the Spider 2.0 tasks on the NYC bike share
  records (bq202, sf_bq050, sf_bq358, sf_bq426). The NYC bike share tasks `q2.spider_bq202` and `q2.spider_sf_bq358`
  check their answers against these files.
- the same folder: gold results of the Spider 2.0-lite tasks on the Brazilian e-commerce records (local002-local004,
  local028-local037); `spider2-lite/evaluation_suite/gold/sql/`: the gold SQL of local003, local004 and local029.
  The Brazilian e-commerce tasks `q2.spider_local*` check their answers against these files.
- `spider2-lite/resource/documents/`: RFM.md and spherical_law.md, the documents of local003 and local035, quoted by
  the tasks `q1.rfm_document` and `q2.spider_local035`.

Spider 2.0: Lei et al., "Spider 2.0: Evaluating Language Models on Real-World Enterprise Text-to-SQL Workflows",
ICLR 2025.
