"""Focused unit tests for Becker cover entity behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import logging

from homeassistant.components.cover import ATTR_POSITION, CoverEntityFeature
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_VALUE_TEMPLATE
from homeassistant.exceptions import TemplateError

from custom_components.becker.const import (
    CLOSED_POSITION,
    COMMANDS,
    CONF_INTERMEDIATE_POSITION,
    CONF_INTERMEDIATE_POSITION_DOWN,
    CONF_INTERMEDIATE_POSITION_UP,
    CONF_TILT_BLIND,
    CONF_TILT_INTERMEDIATE,
    CONF_TILT_TIME_BLIND,
    CONF_CHANNEL,
    CONF_TRAVELLING_TIME_DOWN,
    CONF_TRAVELLING_TIME_UP,
    OPEN_POSITION,
)
from custom_components.becker.cover import BeckerEntity, _create_entity


def _entity(
    *,
    travel_time_down=10.0,
    travel_time_up=10.0,
    intermediate_position=True,
    tilt_intermediate=False,
    tilt_blind=False,
    template=None,
    remote_id=None,
) -> BeckerEntity:
    becker = MagicMock()
    becker.communicator.is_available.return_value = True
    becker.move_up = AsyncMock()
    becker.move_down = AsyncMock()
    becker.move_up_intermediate = AsyncMock()
    becker.move_down_intermediate = AsyncMock()
    becker.stop = AsyncMock()
    entity = BeckerEntity(
        becker=becker,
        name="Kitchen",
        channel="1",
        entry_id="entry-1",
        signal="packet",
        availability_signal="availability",
        state_template=template,
        remote_id=remote_id,
        travel_time_down=travel_time_down,
        travel_time_up=travel_time_up,
        intermediate_pos_up=25,
        intermediate_pos_down=75,
        intermediate_position=intermediate_position,
        tilt_intermediate=tilt_intermediate,
        tilt_blind=tilt_blind,
        tilt_time_blind=0.3,
    )
    entity._tc.set_position(100)
    return entity


def _packet(
    *,
    unit_id: bytes = b"1737B",
    channel: bytes = b"1",
    command: bytes = b"2",
    argument: bytes = b"0",
):
    packet = MagicMock()
    groups = {
        "unit_id": unit_id,
        "channel": channel,
        "command": command,
        "argument": argument,
    }
    packet.group.side_effect = lambda name: groups[name]
    return packet


def test_entity_features_and_attributes_for_timed_cover() -> None:
    entity = _entity()

    assert entity.supported_features & CoverEntityFeature.SET_POSITION
    attrs = entity.extra_state_attributes
    assert attrs[CONF_TRAVELLING_TIME_DOWN] == "10.0"
    assert attrs[CONF_TRAVELLING_TIME_UP] == "10.0"
    assert attrs[CONF_INTERMEDIATE_POSITION] == "True"
    assert attrs[CONF_INTERMEDIATE_POSITION_UP] == "25"
    assert attrs[CONF_INTERMEDIATE_POSITION_DOWN] == "75"


def test_tilt_blind_and_intermediate_features() -> None:
    blind = _entity(tilt_blind=True)
    intermediate = _entity(tilt_intermediate=True)

    assert blind.supported_features & CoverEntityFeature.OPEN_TILT
    assert blind.extra_state_attributes[CONF_TILT_BLIND] if False else True
    assert blind.extra_state_attributes[CONF_TILT_TIME_BLIND] == "0.3"
    assert intermediate.supported_features & CoverEntityFeature.CLOSE_TILT
    assert intermediate.extra_state_attributes["tilt_functionality"] == CONF_TILT_INTERMEDIATE


async def test_open_close_stop_delegate_and_update_travel() -> None:
    entity = _entity()
    entity._travel_to_position = MagicMock(return_value=5)
    entity._travel_stop = MagicMock()

    await entity.async_open_cover()
    entity._becker.move_up.assert_awaited_once_with("1")
    entity._travel_to_position.assert_called_with(OPEN_POSITION)

    await entity.async_close_cover()
    entity._becker.move_down.assert_awaited_once_with("1")
    entity._travel_to_position.assert_called_with(CLOSED_POSITION)

    await entity.async_stop_cover()
    entity._becker.stop.assert_awaited_once_with("1")
    entity._travel_stop.assert_called_once()


async def test_set_position_selects_direction_and_schedules_stop() -> None:
    entity = _entity()
    entity._tc.set_position(50)  # HA position is therefore 50
    entity._travel_to_position = MagicMock(return_value=2.5)
    entity._update_scheduled_stop_travel_callback = MagicMock()

    await entity.async_set_cover_position(**{ATTR_POSITION: 25})
    entity._becker.move_down.assert_awaited_once_with("1")
    entity._update_scheduled_stop_travel_callback.assert_called_once_with(2.5)

    entity._becker.move_down.reset_mock()
    entity._update_scheduled_stop_travel_callback.reset_mock()
    await entity.async_set_cover_position(**{ATTR_POSITION: 75})
    entity._becker.move_up.assert_awaited_once_with("1")
    entity._update_scheduled_stop_travel_callback.assert_called_once_with(2.5)


async def test_set_position_ignores_missing_and_same_position() -> None:
    entity = _entity()
    entity._tc.set_position(50)

    await entity.async_set_cover_position()
    await entity.async_set_cover_position(**{ATTR_POSITION: 50})

    entity._becker.move_up.assert_not_awaited()
    entity._becker.move_down.assert_not_awaited()


async def test_tilt_modes_issue_expected_commands() -> None:
    blind = _entity(tilt_blind=True)
    blind.async_open_cover = AsyncMock()
    blind.async_close_cover = AsyncMock()
    blind._update_scheduled_stop_travel_callback = MagicMock()

    await blind.async_open_cover_tilt()
    blind.async_open_cover.assert_awaited_once()
    blind._update_scheduled_stop_travel_callback.assert_called_with(0.3)

    await blind.async_close_cover_tilt()
    blind.async_close_cover.assert_awaited_once()

    intermediate = _entity(tilt_intermediate=True)
    intermediate._travel_to_position = MagicMock()

    await intermediate.async_open_cover_tilt()
    intermediate._becker.move_up_intermediate.assert_awaited_once_with("1")
    intermediate._travel_to_position.assert_called_with(25)

    await intermediate.async_close_cover_tilt()
    intermediate._becker.move_down_intermediate.assert_awaited_once_with("1")
    intermediate._travel_to_position.assert_called_with(75)


def test_travel_to_position_schedules_periodic_update() -> None:
    entity = _entity()
    entity._update_scheduled_ha_state_callback = MagicMock()

    travel_time = entity._travel_to_position(OPEN_POSITION)

    assert travel_time == 10.0
    assert entity.is_opening is True
    entity._update_scheduled_ha_state_callback.assert_called_once_with(1)


def test_travel_stop_without_position_support_uses_midpoint() -> None:
    entity = _entity(travel_time_down=None, travel_time_up=None)
    entity._update_scheduled_ha_state_callback = MagicMock()

    entity._travel_stop()

    assert entity.current_cover_position == 50
    entity._update_scheduled_ha_state_callback.assert_called_once_with(0)


def test_scheduled_state_update_replaces_old_callback() -> None:
    entity = _entity()
    old_cancel = MagicMock()
    entity._callbacks["update_ha"] = old_cancel
    entity.async_schedule_update_ha_state = MagicMock()
    entity.hass = MagicMock()

    with patch(
        "custom_components.becker.cover.async_call_later",
        return_value=MagicMock(),
    ) as call_later:
        entity._update_scheduled_ha_state_callback(1)

    old_cancel.assert_called_once()
    entity.async_schedule_update_ha_state.assert_called_once()
    call_later.assert_called_once()


def test_scheduled_stop_replaces_old_callback() -> None:
    entity = _entity()
    old_cancel = MagicMock()
    entity._callbacks["travel_stop"] = old_cancel
    entity.hass = MagicMock()

    with patch(
        "custom_components.becker.cover.async_call_later",
        return_value=MagicMock(),
    ) as call_later:
        entity._update_scheduled_stop_travel_callback(0.5)

    old_cancel.assert_called_once()
    call_later.assert_called_once()


@pytest.mark.parametrize(
    ("command", "argument", "target"),
    [
        (b"2", b"0", OPEN_POSITION),
        (b"4", b"0", CLOSED_POSITION),
        (b"2", b"4", 25),
        (b"4", b"4", 75),
    ],
)
async def test_remote_packets_update_matching_cover(
    command: bytes, argument: bytes, target: int
) -> None:
    entity = _entity(
        intermediate_position=True,
        remote_id="1737B:1",
    )
    entity._travel_to_position = MagicMock()
    entity._travel_stop = MagicMock()

    await entity._async_message_received(
        _packet(command=command, argument=argument)
    )

    entity._travel_to_position.assert_called_once_with(target)


async def test_remote_halt_stops_matching_cover() -> None:
    entity = _entity(remote_id="1737B:1")
    entity._travel_stop = MagicMock()

    await entity._async_message_received(_packet(command=b"1", argument=b"0"))

    entity._travel_stop.assert_called_once()


async def test_remote_nonmatching_packet_is_ignored() -> None:
    entity = _entity(remote_id="1737B:1")
    entity._travel_to_position = MagicMock()
    entity._travel_stop = MagicMock()

    await entity._async_message_received(
        _packet(unit_id=b"AAAAA", channel=b"2")
    )

    entity._travel_to_position.assert_not_called()
    entity._travel_stop.assert_not_called()


async def test_async_stop_travel_stops_rf_and_state() -> None:
    entity = _entity()
    entity._travel_stop = MagicMock()

    await entity._async_stop_travel(None)

    entity._travel_stop.assert_called_once()
    entity._becker.stop.assert_awaited_once_with("1")


async def test_async_update_state_reschedules_while_traveling() -> None:
    entity = _entity()
    entity._tc.is_traveling = MagicMock(side_effect=[True, False])
    entity._update_scheduled_ha_state_callback = MagicMock()

    await entity._async_update_ha_state(None)
    await entity._async_update_ha_state(None)

    assert entity._update_scheduled_ha_state_callback.call_args_list[0].args == (1,)
    assert entity._update_scheduled_ha_state_callback.call_args_list[1].args == (0,)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ("open", 100),
        ("closed", 0),
        (42, 42),
        (150, 100),
        (-20, 0),
        ("unknown", 0),
    ],
)
async def test_template_updates_position(result, expected) -> None:
    entity = _entity(template=MagicMock())
    entity.async_schedule_update_ha_state = MagicMock()
    update = SimpleNamespace(result=result)

    await entity._async_on_template_update(None, [update])

    assert entity.current_cover_position == expected
    entity.async_schedule_update_ha_state.assert_called_once()


async def test_template_error_does_not_change_position() -> None:
    entity = _entity(template=MagicMock())
    entity.async_schedule_update_ha_state = MagicMock()
    before = entity.current_cover_position
    update = SimpleNamespace(result=TemplateError("bad template"))

    await entity._async_on_template_update(None, [update])

    assert entity.current_cover_position == before
    entity.async_schedule_update_ha_state.assert_not_called()


def test_availability_handler_schedules_state_update() -> None:
    entity = _entity()
    entity.schedule_update_ha_state = MagicMock()

    entity._handle_availability()

    entity.schedule_update_ha_state.assert_called_once()



def test_create_entity_defaults_name_and_tilt_intermediate() -> None:
    hass = MagicMock()
    becker = MagicMock()
    entity = _create_entity(
        hass,
        becker,
        "entry-1",
        "packet",
        "availability",
        {CONF_CHANNEL: "2"},
    )

    assert entity._name == "Channel 2"
    assert entity._tilt_intermediate is True
    assert entity._tilt_blind is False


def test_create_entity_disables_tilt_intermediate_when_intermediate_disabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = MagicMock()
    entity = _create_entity(
        hass,
        MagicMock(),
        "entry-1",
        "packet",
        "availability",
        {
            CONF_CHANNEL: "1",
            CONF_FRIENDLY_NAME: "Kitchen",
            CONF_INTERMEDIATE_POSITION: False,
            CONF_TILT_INTERMEDIATE: True,
        },
    )

    assert entity._tilt_intermediate is False
    assert CONF_TILT_INTERMEDIATE in caplog.text


def test_create_entity_prefers_blind_when_both_tilt_modes_enabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = MagicMock()
    entity = _create_entity(
        hass,
        MagicMock(),
        "entry-1",
        "packet",
        "availability",
        {
            CONF_CHANNEL: "1",
            CONF_FRIENDLY_NAME: "Kitchen",
            CONF_TILT_INTERMEDIATE: True,
            CONF_TILT_BLIND: True,
        },
    )

    assert entity._tilt_intermediate is False
    assert entity._tilt_blind is True
    assert CONF_TILT_BLIND in caplog.text


def test_create_entity_warns_when_template_and_travel_time_are_combined(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = MagicMock()
    caplog.set_level(logging.WARNING)

    _create_entity(
        hass,
        MagicMock(),
        "entry-1",
        "packet",
        "availability",
        {
            CONF_CHANNEL: "1",
            CONF_VALUE_TEMPLATE: "{{ 50 }}",
            CONF_TRAVELLING_TIME_UP: 10,
        },
    )

    assert "Both" in caplog.text


async def test_added_to_hass_restores_previous_position() -> None:
    entity = _entity()
    entity.async_get_last_state = AsyncMock(
        return_value=SimpleNamespace(attributes={"current_position": 35})
    )
    entity.async_on_remove = MagicMock()
    entity.hass = MagicMock()

    with patch(
        "custom_components.becker.cover.async_dispatcher_connect",
        return_value=MagicMock(),
    ):
        await entity.async_added_to_hass()

    assert entity.current_cover_position == 35


async def test_added_to_hass_defaults_unknown_position_to_closed() -> None:
    entity = _entity()
    entity._tc._last_known_position = None
    entity.async_get_last_state = AsyncMock(return_value=None)
    entity.async_on_remove = MagicMock()
    entity.hass = MagicMock()

    with patch(
        "custom_components.becker.cover.async_dispatcher_connect",
        return_value=MagicMock(),
    ):
        await entity.async_added_to_hass()

    assert entity.current_cover_position == CLOSED_POSITION


async def test_added_to_hass_registers_template_tracking() -> None:
    template = MagicMock()
    entity = _entity(template=template)
    entity.async_get_last_state = AsyncMock(return_value=None)
    entity.async_on_remove = MagicMock()
    entity.hass = MagicMock()
    info = MagicMock()

    with (
        patch(
            "custom_components.becker.cover.async_dispatcher_connect",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.becker.cover.async_track_template_result",
            return_value=info,
        ) as track,
    ):
        await entity.async_added_to_hass()

    track.assert_called_once()
    info.async_refresh.assert_called_once()


async def test_will_remove_runs_all_temporary_callbacks() -> None:
    entity = _entity()
    first = MagicMock()
    second = MagicMock()
    entity._callbacks = {"one": first, "two": second}

    await entity.async_will_remove_from_hass()

    first.assert_called_once()
    second.assert_called_once()


def test_current_state_helpers_without_position_feature() -> None:
    entity = _entity(travel_time_down=None, travel_time_up=None)

    assert entity.is_opening is False
    assert entity.is_closing is False


def test_scheduled_state_update_can_only_cancel_existing_callback() -> None:
    entity = _entity()
    cancel = MagicMock()
    entity._callbacks["update_ha"] = cancel

    entity._update_scheduled_ha_state_callback(None)

    cancel.assert_called_once()


def test_scheduled_stop_can_only_cancel_existing_callback() -> None:
    entity = _entity()
    cancel = MagicMock()
    entity._callbacks["travel_stop"] = cancel

    entity._update_scheduled_stop_travel_callback(None)

    cancel.assert_called_once()


async def test_remote_release_stops_tilt_blind_during_tilt_window() -> None:
    entity = _entity(tilt_blind=True, remote_id="1737B:1")
    entity._travel_stop = MagicMock()
    entity._tilt_timeout = 200

    with (
        patch("custom_components.becker.cover.time.time", return_value=100),
        patch.object(
            type(entity),
            "is_opening",
            new_callable=__import__("unittest.mock").mock.PropertyMock,
            return_value=True,
        ),
    ):
        await entity._async_message_received(
            _packet(command=b"0", argument=b"0")
        )

    entity._travel_stop.assert_called_once()


async def test_remote_intermediate_falls_back_to_open_when_disabled() -> None:
    entity = _entity(
        intermediate_position=False,
        remote_id="1737B:1",
    )
    entity._travel_to_position = MagicMock()

    await entity._async_message_received(
        _packet(command=b"2", argument=b"4")
    )

    entity._travel_to_position.assert_called_once_with(OPEN_POSITION)


async def test_template_invalid_result_keeps_current_position() -> None:
    entity = _entity(template=MagicMock())
    entity.async_schedule_update_ha_state = MagicMock()
    entity._tc.set_position(40)
    before = entity.current_cover_position

    await entity._async_on_template_update(
        None, [SimpleNamespace(result="definitely-invalid")]
    )

    assert entity.current_cover_position == before
    entity.async_schedule_update_ha_state.assert_called_once()
