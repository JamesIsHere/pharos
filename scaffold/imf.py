"""IMF World Economic Outlook fetch for Scaffold pages (quick lane; nothing is stored).

fetch() reads annual series for every country from the IMF's SDMX API (api.imf.org, no key). latest_actual() maps a
country's LATEST_ACTUAL_ANNUAL_DATA label to a calendar year; years after it are IMF estimates or forecasts. The same
fiscal-year rule is written out in scaffold/weo.py, which predates this module and still carries its own copy."""
import re
import urllib.request
import xml.etree.ElementTree as XT

API = "https://api.imf.org/external/sdmx/2.1/"
UA = {"User-Agent": "pharos-scaffold"}


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def fetch(indicators):
    """({indicator: {imf_code: {year: value}}}, {indicator: {imf_code: group attributes}}) for the WEO dataflow.
    An indicator that comes back empty stops the build."""
    url = API + "data/IMF.RES,WEO/." + "+".join(indicators) + ".A"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
        root = XT.fromstring(r.read())
    data = {k: {} for k in indicators}
    meta = {k: {} for k in indicators}
    for e in root.iter():
        t = _local(e.tag)
        if t == "Series":
            data[e.get("INDICATOR")][e.get("COUNTRY")] = {
                int(o.get("TIME_PERIOD")): float(o.get("OBS_VALUE")) for o in e if _local(o.tag) == "Obs"}
        elif t == "Group" and e.get("COUNTRY") and e.get("INDICATOR") in meta:
            meta[e.get("INDICATOR")][e.get("COUNTRY")] = e.attrib
    for ind, m in data.items():
        if not m:
            raise SystemExit(f"imf: no series came back for {ind}")
    return data, meta


def latest_actual(meta, ind, c):
    """The calendar year of the latest actual, or None. A fiscal or solar year label (FY2024/25) maps by the rule the
    series' own notes state: FY(t-1/t) = CY(t) puts FY2024/25 in 2025; FY(t/t+1) = CY(t) puts it in 2024. No label,
    or no stated rule: None. A label of any other shape stops the build."""
    v = meta[ind].get(c, {}).get("LATEST_ACTUAL_ANNUAL_DATA") or ""
    if not v:
        return None
    if v.isdigit():
        return int(v)
    m = re.fullmatch(r"[FS]Y(\d{4})/(\d{2})", v)
    if not m:
        raise SystemExit(f"imf: {ind} {c} latest actual {v!r} is neither a year nor FYyyyy/yy")
    rules = set(re.findall(r"[FS]Y\s*\((t-1/t|t/t\+1)\)\s*=\s*CY\s*\(t\)", meta[ind][c].get("METHODOLOGY_NOTES") or ""))
    if len(rules) > 1:
        raise SystemExit(f"imf: {ind} {c} notes state two fiscal-year rules: {rules}")
    if not rules:
        return None
    return int(m.group(1)) + (1 if rules.pop() == "t-1/t" else 0)
