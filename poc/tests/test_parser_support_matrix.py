from scripts import check_parser_support_matrix


def test_parser_support_matrix_matches_router(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_parser_support_matrix.py",
            "--matrix",
            "docs/parser_support_matrix.json",
            "--pyproject",
            "pyproject.toml",
        ],
    )

    assert check_parser_support_matrix.main() == 0
