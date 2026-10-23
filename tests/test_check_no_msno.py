"""check_no_msno: user-ID-shaped strings are caught; base64 image data is not.

Test strings are built at runtime from synthetic characters, so this file never
contains an ID-shaped literal (or a real user ID) for the scanner to find.
"""

from src import check_no_msno

FAKE_ID = "Zz" * 21 + "q="  # 43 base64 characters + "=": the msno shape, not a real ID


def test_finds_an_id_in_text_and_csv():
    text = f"campaign,msno\n2016-06,{FAKE_ID}\n"
    assert check_no_msno.find_ids(text) == [(2, FAKE_ID)]


def test_ignores_the_tail_of_a_long_base64_blob():
    blob = "A" * 200 + "B" * 43 + "="
    assert check_no_msno.find_ids(f'"image/png": "{blob}"') == []


def test_ignores_strings_of_other_lengths():
    assert check_no_msno.find_ids("a" * 42 + "= and " + "b" * 44 + "=") == []


def test_main_exits_nonzero_and_masks_the_id(tmp_path, capsys):
    bad, good = tmp_path / "bad.csv", tmp_path / "good.md"
    bad.write_text(f"msno\n{FAKE_ID}\n")
    good.write_text("Conversion was 8.01% in 2016-06.\n")

    assert check_no_msno.main([str(good)]) == 0
    assert check_no_msno.main([str(bad)]) == 1
    out = capsys.readouterr().out
    assert FAKE_ID not in out and "ZzZz...zq=" in out
