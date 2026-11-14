"""Quarterly revenue per company from SEC Company Facts, for Scaffold comparison pages (qfr.py, housing.py).

One figure per company and calendar quarter: the largest of the total-revenue concepts it reported for that quarter
(SEC's calendar frame, CYyyyyQn). Few companies file their fiscal fourth quarter as a quarter, so the missing quarter
is worked out from each annual fact (frame CYyyyy) by its own dates: when exactly three reported quarters lie inside
the annual period and they are its first three, the fourth is the annual figure less their sum, dated to the calendar
quarter holding its midpoint (the annual end date less 45 days), the way SEC's frames place a fiscal quarter. That
holds for any fiscal year end (D.R. Horton's ends in September, Toll Brothers' in October). Only quarters from `start` on are kept."""

REV = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax",
       "SalesRevenueNet", "SalesRevenueGoodsNet")
# First calendar quarter every SEC filer had to report in XBRL. The last phase-in group starts with a 10-Q "for a fiscal
# period ending on or after June 15, 2011" (SEC Release 33-9002, 74 FR 6776, read 2026-11-14). The quarter before is
# partial: a fiscal quarter ending 2011-06-30 is covered, one ending 2011-05-31 is not.
XBRL_ALL = "2011-07-01"


def build(con, serving, sics, table, start=XBRL_ALL):
    """Create <table>(cik, name, q, v, derived) in con. derived is true only when the figure kept (the largest across
    the revenue concepts) is a worked-out fourth quarter, not when a reported figure was kept beside one. Stops if a
    company/concept/frame carries two facts."""
    cf = f"read_parquet('{(serving / 'company_facts.parquet').as_posix()}/**/*.parquet', hive_partitioning=false)"
    sd = f"read_parquet('{(serving / 'submission_details.parquet').as_posix()}')"
    s = ", ".join(f"'{x}'" for x in sics)
    r = ", ".join(f"'{x}'" for x in REV)
    con.execute(f"""CREATE TABLE {table}_f AS SELECT cik, d.name, concept, frame, start_date, end_date, val
                    FROM {cf} AS f JOIN {sd} AS d USING (cik)
                    WHERE sic IN ({s}) AND taxonomy = 'us-gaap' AND unit = 'USD' AND concept IN ({r})
                      AND regexp_matches(frame, '^CY\\d{{4}}(Q[1-4])?$')""")
    dup = con.execute(f"SELECT count(*) FROM (SELECT cik, concept, frame FROM {table}_f GROUP BY ALL HAVING count(*) > 1)").fetchone()[0]
    if dup:
        raise SystemExit(f"Company Facts: {dup} company/concept/frame keys carry more than one fact")
    con.execute(f"""CREATE TABLE {table} AS
      WITH qq AS (SELECT cik, concept, start_date, end_date, val,
                         make_date(substr(frame, 3, 4)::INT, (substr(frame, 8, 1)::INT - 1) * 3 + 1, 1) AS q
                  FROM {table}_f WHERE length(frame) = 8),
           yy AS (SELECT cik, concept, start_date, end_date, val FROM {table}_f WHERE length(frame) = 6),
           q4 AS (SELECT yy.cik, yy.concept, date_trunc('quarter', yy.end_date - INTERVAL 45 DAY)::DATE AS q,
                         yy.val - sum(qq.val) AS val
                  FROM yy JOIN qq ON qq.cik = yy.cik AND qq.concept = yy.concept
                   AND qq.start_date >= yy.start_date - INTERVAL 7 DAY AND qq.end_date <= yy.end_date + INTERVAL 7 DAY
                  GROUP BY yy.cik, yy.concept, yy.start_date, yy.end_date, yy.val
                  HAVING count(*) = 3 AND max(qq.q) = date_trunc('quarter', yy.end_date - INTERVAL 45 DAY) - INTERVAL 3 MONTH),
           allq AS (SELECT cik, concept, q, val, false AS derived FROM qq
                    UNION ALL SELECT cik, concept, q, val, true FROM q4 WHERE NOT EXISTS
                      (SELECT 1 FROM qq WHERE qq.cik = q4.cik AND qq.concept = q4.concept AND qq.q = q4.q))
      SELECT cik, (SELECT any_value(name) FROM {table}_f n WHERE n.cik = allq.cik) AS name, q,
             max(val) AS v, coalesce(max(val) FILTER (WHERE NOT derived) < max(val), true) AS derived
      FROM allq WHERE q >= DATE '{start}' GROUP BY cik, q""")


def same_company_growth(con, table):
    """Growth over the same quarter a year earlier, counting only companies with a figure in both quarters."""
    return con.execute(f"""SELECT a.q, (sum(a.v) / sum(b.v) - 1) * 100 FROM {table} a JOIN {table} b
                           ON b.cik = a.cik AND b.q = a.q - INTERVAL 1 YEAR GROUP BY a.q ORDER BY a.q""").fetchall()
