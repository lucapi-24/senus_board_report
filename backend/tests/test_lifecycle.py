import pytest

from app.services import lifecycle


def test_happy_path_transitions():
    status = lifecycle.UPLOADED
    for target in (
        lifecycle.PROCESSING,
        lifecycle.EXTRACTED,
        lifecycle.IN_REVIEW,
        lifecycle.PUBLISHED,
    ):
        status = lifecycle.advance(status, target)
    assert status == lifecycle.PUBLISHED


def test_extracted_can_publish_directly():
    assert lifecycle.advance(lifecycle.EXTRACTED, lifecycle.PUBLISHED) == lifecycle.PUBLISHED


def test_error_can_be_retried():
    assert lifecycle.advance(lifecycle.PROCESSING, lifecycle.ERROR) == lifecycle.ERROR
    assert lifecycle.advance(lifecycle.ERROR, lifecycle.PROCESSING) == lifecycle.PROCESSING


def test_illegal_transition_is_rejected():
    assert not lifecycle.can_transition(lifecycle.UPLOADED, lifecycle.PUBLISHED)
    with pytest.raises(lifecycle.InvalidTransition):
        lifecycle.advance(lifecycle.UPLOADED, lifecycle.PUBLISHED)


def test_unknown_state_has_no_transitions():
    assert not lifecycle.can_transition("nonsense", lifecycle.PROCESSING)
