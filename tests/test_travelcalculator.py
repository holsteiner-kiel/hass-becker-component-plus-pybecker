"""Tests for cover travel-time position estimation."""

from unittest.mock import patch

from custom_components.becker.travelcalculator import TravelCalculator, TravelStatus


def test_set_position_and_basic_state_helpers() -> None:
    tc = TravelCalculator(20, 10)
    tc.set_position(100)

    assert tc.current_position() == 100
    assert tc.is_closed() is True
    assert tc.is_open() is False
    assert tc.position_reached() is True
    assert tc.is_traveling() is False


def test_stop_with_unknown_position_is_noop() -> None:
    tc = TravelCalculator(20, 10)

    tc.stop()

    assert tc.current_position() is None
    assert tc.travel_direction is TravelStatus.STOPPED


def test_start_travel_unknown_position_sets_target_directly() -> None:
    tc = TravelCalculator(20, 10)

    tc.start_travel(25)

    assert tc.current_position() == 25
    assert tc.position_reached() is True


def test_start_travel_sets_direction_up_and_down() -> None:
    tc = TravelCalculator(20, 10)
    tc.set_position(50)

    tc.start_travel(100)
    assert tc.travel_direction is TravelStatus.DIRECTION_DOWN
    assert tc.is_closing() is True

    tc.set_position(50)
    tc.start_travel(0)
    assert tc.travel_direction is TravelStatus.DIRECTION_UP
    assert tc.is_opening() is True


def test_start_travel_up_and_down_helpers() -> None:
    tc = TravelCalculator(20, 10)
    tc.set_position(50)

    tc.start_travel_up()
    assert tc._travel_to_position == 0

    tc.set_position(50)
    tc.start_travel_down()
    assert tc._travel_to_position == 100


def test_current_position_interpolates_during_downward_travel() -> None:
    tc = TravelCalculator(20, 10)
    with patch(
        "custom_components.becker.travelcalculator.time.time",
        side_effect=[100.0, 100.0, 105.0, 105.0],
    ):
        tc.set_position(0)
        tc.start_travel(100)
        assert tc.current_position() == 25


def test_current_position_interpolates_during_upward_travel() -> None:
    tc = TravelCalculator(20, 10)
    with patch(
        "custom_components.becker.travelcalculator.time.time",
        side_effect=[100.0, 100.0, 102.5, 102.5],
    ):
        tc.set_position(100)
        tc.start_travel(0)
        assert tc.current_position() == 75


def test_current_position_returns_target_after_elapsed_time() -> None:
    tc = TravelCalculator(20, 10)
    with patch(
        "custom_components.becker.travelcalculator.time.time",
        side_effect=[100.0, 100.0, 121.0],
    ):
        tc.set_position(0)
        tc.start_travel(100)
        assert tc.current_position() == 100


def test_position_reached_or_exceeded_short_circuit() -> None:
    tc = TravelCalculator(20, 10)
    tc._last_known_position = 80
    tc._travel_to_position = 50
    tc._position_confirmed = False
    tc.travel_direction = TravelStatus.DIRECTION_DOWN

    assert tc.current_position() == 50

    tc._last_known_position = 20
    tc._travel_to_position = 50
    tc.travel_direction = TravelStatus.DIRECTION_UP
    assert tc.current_position() == 50


def test_stop_freezes_current_position() -> None:
    tc = TravelCalculator(20, 10)
    with patch(
        "custom_components.becker.travelcalculator.time.time",
        side_effect=[100.0, 100.0, 105.0, 105.0, 105.0],
    ):
        tc.set_position(0)
        tc.start_travel(100)
        tc.stop()

    assert tc.current_position() == 25
    assert tc.is_traveling() is False
    assert tc.travel_direction is TravelStatus.STOPPED


def test_calculate_travel_time_uses_direction_specific_duration() -> None:
    tc = TravelCalculator(20, 10)

    assert tc.calculate_travel_time(0, 50) == 10
    assert tc.calculate_travel_time(100, 50) == 5


def test_equality_compares_internal_state() -> None:
    first = TravelCalculator(20, 10)
    second = TravelCalculator(20, 10)

    assert first == second
    second.set_position(50)
    assert first != second
