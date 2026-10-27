from app.services.note_crosscheck import find_note_mismatches


def test_note_figure_mismatch_is_reported_with_its_source_page():
    warnings = find_note_mismatches(
        [
            (5, "Goodwill 669,550"),
            (8, "Goodwill of €669,500 was recognised on the acquisition."),
        ],
        {5},
        {"balance_sheet": {"Goodwill": "669550"}},
    )

    assert len(warnings) == 1
    assert warnings[0]["line_item"] == "Goodwill"
    assert warnings[0]["table_value"] == 669550.0
    assert warnings[0]["note_value"] == 669500.0
    assert warnings[0]["page"] == 8


def test_note_values_within_tolerance_are_not_reported():
    assert (
        find_note_mismatches(
            [(8, "Goodwill of €669,550 was recognised.")],
            {5},
            {"balance_sheet": {"Goodwill": "669550"}},
        )
        == []
    )
