"""Write the two FTC Horizontal Merger Investigation Data files that `pharos antitrust` lands (D175).

Reads the four report editions in <data root>/manual/antitrust/sources/ (fiscal years 1996-2003, -2005, -2007,
-2011) and writes ftc_hmid_hhi.csv and ftc_hmid_competitors.csv beside that folder: one row per cell of every table,
totals left out. Every table is tied to its own TOTAL row and column first; a table that does not tie stops the
script and nothing is written.

Needs PyMuPDF, which is not a Pharos dependency (only this script reads a PDF):
    uv run --with pymupdf python scripts/antitrust_hmid.py
Run it only when a report edition is added to sources/; then date the two files in config/sources.yaml and run
`uv run pharos antitrust`.
"""
import csv
import re

import fitz

from pharos.paths import data_root

FOLDER = data_root() / "manual" / "antitrust"
EDITIONS = {2003: "ftc_hmid_fy1996_2003.pdf", 2005: "ftc_hmid_fy1996_2005.pdf",
            2007: "ftc_hmid_fy1996_2007.pdf", 2011: "ftc_hmid_fy1996_2011.pdf"}
HHI = ["0 - 1,799", "1,800 - 1,999", "2,000 - 2,399", "2,400 - 2,999",
       "3,000 - 3,999", "4,000 - 4,999", "5,000 - 6,999", "7,000 +"]
DELTA = ["0 - 99", "100 - 199", "200 - 299", "300 - 499", "500 - 799",
         "800 - 1,199", "1,200 - 2,499", "2,500 +"]
COMP = ["2 to 1", "3 to 2", "4 to 3", "5 to 4", "6 to 5", "7 to 6", "8 to 7",
        "9 to 8", "10 to 9", "10 +"]
NOISE = ("FTC Horizontal Merger Investigations", "FY 1996", "HORIZONTAL MERGER",
         "FEDERAL TRADE", "POST MERGER HHI", "SIGNIFICANT COMPETITORS")


def num(s):
    return int(s.replace(",", ""))


def parse():
    hhi_rows, comp_rows, tables = [], [], 0
    for end_fy, fname in EDITIONS.items():
        doc = fitz.open(FOLDER / "sources" / fname)
        lines = [ln.strip() for p in doc for ln in p.get_text().split("\n")]
        starts = [i for i, ln in enumerate(lines) if re.fullmatch(r"Table \d+\.\d+", ln)]
        for k, i in enumerate(starts):
            block = lines[i:starts[k + 1] if k + 1 < len(starts) else len(lines)]
            table = block[0].split()[1]
            title = []
            for ln in block[1:]:
                if ln in ("Enforced/Closed", "Outcome"):
                    break
                if ln and not ln.startswith(NOISE) and not ln.isdigit() \
                        and "Post Merger HHI and Change" not in ln and "Number of Significant" not in ln:
                    title.append(ln.replace("�", '"').replace("“", '"').replace("”", '"'))
            title = " - ".join(title)
            if "Enforced/Closed" in block:
                cells = [c for c in block if re.fullmatch(r"[\d,]+/[\d,]+", c)]
                assert len(cells) == 81, (end_fy, table, len(cells))
                m = [[tuple(num(x) for x in cells[r * 9 + c].split("/")) for c in range(9)] for r in range(9)]
                for r in range(8):       # each row ties to its TOTAL cell
                    for j in (0, 1):
                        assert sum(m[r][c][j] for c in range(8)) == m[r][8][j], (end_fy, table, "row", r)
                for c in range(9):       # each column, and the grand total, ties to the TOTAL row
                    for j in (0, 1):
                        assert sum(m[r][c][j] for r in range(8)) == m[8][c][j], (end_fy, table, "col", c)
                for r in range(8):
                    for c in range(8):
                        hhi_rows.append([end_fy, table, title, HHI[r], DELTA[c], m[r][c][0], m[r][c][1]])
            else:
                got = {}
                for lab in COMP + ["TOTAL"]:
                    idx = [j for j, ln in enumerate(block[:-3])
                           if ln == lab and re.fullmatch(r"[\d,]+", block[j + 1])]
                    assert len(idx) == 1, (end_fy, table, lab, idx)
                    got[lab] = [num(block[idx[0] + d]) for d in (1, 2, 3)]
                for j in (0, 1):
                    assert sum(got[lab][j] for lab in COMP) == got["TOTAL"][j], (end_fy, table, "col", j)
                for lab in COMP + ["TOTAL"]:
                    assert got[lab][0] + got[lab][1] == got[lab][2], (end_fy, table, lab)
                for lab in COMP:
                    comp_rows.append([end_fy, table, title, lab, got[lab][0], got[lab][1]])
            tables += 1
    return hhi_rows, comp_rows, tables


def write(name, header, rows):
    with open(FOLDER / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"wrote {FOLDER / name}: {len(rows):,} rows")


if __name__ == "__main__":
    hhi_rows, comp_rows, tables = parse()
    print(f"{tables} tables parsed from {len(EDITIONS)} reports; every row and column total ties")
    write("ftc_hmid_hhi.csv", ["edition_end_fy", "table", "markets", "post_merger_hhi", "delta_hhi", "enforced", "closed"], hhi_rows)
    write("ftc_hmid_competitors.csv", ["edition_end_fy", "table", "markets", "significant_competitors", "enforced", "closed"], comp_rows)
