"""Stage `mix`: band edges, unknown handling, top-N cities."""

from datetime import date, timedelta

import duckdb
import pandas as pd

from src import ingest, mix
from src.logs import sql_path
from tests.conftest import write_members_csv, write_trials

START = date(2016, 7, 1)


def lagged(days):
    return START - timedelta(days=days)


def test_mix_bands_and_unknowns(tmp_path):
    # (msno, city, bd, gender, registered_via, registration date)
    members = [
        ("age0_lag-1", 1, 0, "male", 7, lagged(-1)),
        ("age1_lag0", 1, 1, "female", 7, lagged(0)),
        ("age24_lag60", 1, 24, "MALE", 9, lagged(60)),
        ("age25_lag61", 1, 25, "other", 9, lagged(61)),
        ("age34_lag365", 1, 34, None, 4, lagged(365)),
        ("age35_lag366", 13, 35, "female", 4, lagged(366)),
        ("age80_lag1095", 13, 80, "female", 4, lagged(1095)),
        ("age81_lag1096", 13, 81, "female", 4, lagged(1096)),
        ("rare_city_noreg", 5, 30, "male", 3, None),
    ]
    ids = [m[0] for m in members] + ["no_member_row"]
    con = duckdb.connect()
    ingest.ingest_members(con, write_members_csv(tmp_path / "members_v3.csv", members), tmp_path / "members.parquet")
    write_trials(tmp_path / "trials.parquet", [(m, START) for m in ids])
    out = tmp_path / "mix.parquet"

    summary = mix.build_mix(
        con,
        tmp_path / "trials.parquet",
        tmp_path / "members.parquet",
        out,
        top_cities=2,
        min_age=1,
        max_age=80,
        age_upper_bounds=(24, 34),
        lag_lower_bounds=(0, 61, 366, 1096),
    )

    got = pd.read_parquet(out).set_index("msno")
    assert got["age_band"].to_dict() == {
        "age0_lag-1": "unknown", "age1_lag0": "<=24", "age24_lag60": "<=24", "age25_lag61": "25-34",
        "age34_lag365": "25-34", "age35_lag366": "35+", "age80_lag1095": "35+", "age81_lag1096": "unknown",
        "rare_city_noreg": "25-34", "no_member_row": "unknown",
    }
    assert got["reg_lag_band"].to_dict() == {
        "age0_lag-1": "missing", "age1_lag0": "0-60", "age24_lag60": "0-60", "age25_lag61": "61-365",
        "age34_lag365": "61-365", "age35_lag366": "366-1095", "age80_lag1095": "366-1095",
        "age81_lag1096": "1096+", "rare_city_noreg": "missing", "no_member_row": "missing",
    }
    assert got.loc["age24_lag60", "gender"] == "male"
    assert got.loc["age25_lag61", "gender"] == "unknown" and got.loc["age34_lag365", "gender"] == "unknown"
    assert got.loc["age1_lag0", "city"] == "1" and got.loc["age35_lag366", "city"] == "13"
    assert got.loc["rare_city_noreg", "city"] == "other"
    assert got.loc["no_member_row", ["registered_via", "city", "gender"]].tolist() == ["unknown"] * 3
    assert summary == {"trials": 10, "no_member_row": 1, "duplicate_member_rows": 0}
    assert con.execute(f"select count(*) from read_parquet({sql_path(out)})").fetchone() == (10,)
