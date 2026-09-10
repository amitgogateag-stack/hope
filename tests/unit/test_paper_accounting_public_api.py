import hope.application.paper as paper


def test_paper_public_api_exposes_authoritative_accounting_writer_only() -> None:
    assert paper.PaperAccountingWriter is not None
    assert "PaperAccountingWriter" in paper.__all__
    assert "PaperPnLWriter" not in paper.__all__
    assert not hasattr(paper, "PaperPnLWriter")
