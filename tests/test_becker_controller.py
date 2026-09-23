"""Tests for the bundled Becker controller command routing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.becker.pybecker import becker as becker_module
from custom_components.becker.pybecker.becker import Becker


def _controller() -> Becker:
    controller = Becker.__new__(Becker)
    controller.operation_lock = __import__("asyncio").Lock()
    controller.communicator = MagicMock()
    controller.db = MagicMock()
    return controller


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        ("1", (1, 1)),
        ("2:7", (2, 7)),
        ("0:15", (0, 15)),
    ],
)
def test_split_channel(channel: str, expected: tuple[int, int]) -> None:
    assert Becker._split_channel(channel) == expected


async def test_send_rejects_invalid_channel() -> None:
    controller = _controller()
    controller.run_codes = AsyncMock()

    await controller.send("1:9", "UP")

    controller.run_codes.assert_not_awaited()


async def test_send_routes_specific_unit() -> None:
    controller = _controller()
    controller.db.get_unit.return_value = ["1737b", 5, 1]
    controller.run_codes = AsyncMock()

    await controller.send("2:3", "DOWN")

    controller.db.get_unit.assert_called_once_with(2)
    controller.run_codes.assert_awaited_once_with(
        3, ["1737b", 5, 1], "DOWN", False
    )


async def test_send_routes_broadcast_to_all_units() -> None:
    controller = _controller()
    units = [["1737b", 1, 1], ["1737c", 2, 1]]
    controller.db.get_all_units.return_value = units
    controller.run_codes = AsyncMock()

    await controller.send("0:15", "HALT")

    assert controller.run_codes.await_count == 2
    controller.run_codes.assert_any_await(15, units[0], "HALT", False)
    controller.run_codes.assert_any_await(15, units[1], "HALT", False)


@pytest.mark.parametrize(
    ("method", "command"),
    [
        ("move_up", "UP"),
        ("move_up_intermediate", "UP2"),
        ("move_down", "DOWN"),
        ("move_down_intermediate", "DOWN2"),
        ("stop", "HALT"),
        ("pair", "TRAIN"),
    ],
)
async def test_command_helpers_delegate(method: str, command: str) -> None:
    controller = _controller()
    controller.send = AsyncMock()

    await getattr(controller, method)("2:4")

    controller.send.assert_awaited_once_with("2:4", command)


@pytest.mark.parametrize(
    ("command", "constant"),
    [
        ("UP", becker_module.COMMAND_UP),
        ("UP2", becker_module.COMMAND_UP5),
        ("HALT", becker_module.COMMAND_HALT),
        ("RELEASE", becker_module.COMMAND_RELEASE),
        ("DOWN", becker_module.COMMAND_DOWN),
        ("DOWN2", becker_module.COMMAND_DOWN5),
    ],
)
async def test_run_codes_simple_commands(command: str, constant: int) -> None:
    controller = _controller()
    controller.write = AsyncMock()
    unit = ["1737b", 10, 1]

    with patch(
        "custom_components.becker.pybecker.becker.generate_code",
        side_effect=lambda channel, current_unit, cmd: f"{channel}:{current_unit[1]}:{cmd}",
    ):
        await controller.run_codes(2, unit, command, False)

    controller.write.assert_awaited_once_with([f"2:10:{constant}"])
    assert unit[1] == 11
    controller.db.set_unit.assert_called_once_with(unit, False)


async def test_run_codes_ignores_unconfigured_unit_except_pairing() -> None:
    controller = _controller()
    controller.write = AsyncMock()
    unit = ["1737b", 10, 0]

    await controller.run_codes(1, unit, "UP", False)

    controller.write.assert_not_awaited()
    controller.db.set_unit.assert_not_called()


async def test_train_sequence_configures_unit_and_preserves_code_order() -> None:
    controller = _controller()
    controller.write = AsyncMock()
    unit = ["1737b", 20, 0]

    with patch(
        "custom_components.becker.pybecker.becker.generate_code",
        side_effect=lambda channel, current_unit, cmd: (current_unit[1], cmd),
    ):
        await controller.run_codes(1, unit, "TRAIN", False)

    assert controller.write.await_args.args[0] == [
        (20, becker_module.COMMAND_PAIR),
        (21, becker_module.COMMAND_PAIR2),
        (22, 0x00),
        (23, becker_module.COMMAND_PAIR),
        (24, becker_module.COMMAND_PAIR2),
        (25, 0x00),
    ]
    assert unit == ["1737b", 26, 1]
    controller.db.set_unit.assert_called_once_with(unit, False)


async def test_timed_move_sends_move_then_stop() -> None:
    controller = _controller()
    controller.write = AsyncMock()
    unit = ["1737b", 30, 1]

    with (
        patch(
            "custom_components.becker.pybecker.becker.generate_code",
            side_effect=lambda channel, current_unit, cmd: (current_unit[1], cmd),
        ),
        patch(
            "custom_components.becker.pybecker.becker.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
    ):
        await controller.run_codes(1, unit, "UP:2", False)

    assert controller.write.await_args_list[0].args[0] == [
        (30, becker_module.COMMAND_UP)
    ]
    assert controller.write.await_args_list[1].args[0] == [
        (31, becker_module.COMMAND_HALT)
    ]
    # The final empty write comes from the common command-list path.
    assert controller.write.await_args_list[2].args[0] == []
    sleep.assert_awaited_once_with(2)
    assert unit[1] == 32


async def test_list_units_returns_database_units() -> None:
    controller = _controller()
    units = [["1737b", 1, 1]]
    controller.db.get_all_units.return_value = units

    assert await controller.list_units() == units


async def test_init_unconfigured_unit_initializes_and_syncs() -> None:
    controller = _controller()
    unit = ["1737b", 0, 0]
    controller.db.get_unit.return_value = unit
    controller.stop = AsyncMock()

    with (
        patch("custom_components.becker.pybecker.becker.randrange", return_value=20),
        patch(
            "custom_components.becker.pybecker.becker.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
    ):
        await controller.init_unconfigured_unit("2:3", name="Kitchen")

    assert unit == ["1737b", 20, 1]
    controller.db.set_unit.assert_called_once_with(unit)
    assert controller.stop.await_count == 5
    controller.stop.assert_awaited_with("2:1")
    assert sleep.await_count == 5


async def test_init_configured_unit_is_noop() -> None:
    controller = _controller()
    controller.db.get_unit.return_value = ["1737b", 10, 1]
    controller.stop = AsyncMock()

    await controller.init_unconfigured_unit("1")

    controller.db.set_unit.assert_not_called()
    controller.stop.assert_not_awaited()



def test_constructor_builds_components_and_starts_communicator() -> None:
    communicator = MagicMock()
    database = MagicMock()
    database.get_all_units.return_value = []

    with (
        patch(
            "custom_components.becker.pybecker.becker.BeckerCommunicator",
            return_value=communicator,
        ) as communicator_cls,
        patch(
            "custom_components.becker.pybecker.becker.Database",
            return_value=database,
        ) as database_cls,
    ):
        controller = Becker(
            device_name="/dev/ttyUSB0",
            init_dummy=True,
            db_filename="state.db",
            callback=MagicMock(),
            availability_callback=MagicMock(),
            queue_size=50,
            retry_max=4,
            retry_delay=0.25,
        )

    communicator_cls.assert_called_once()
    database_cls.assert_called_once_with("state.db")
    database.init_dummy.assert_called_once()
    communicator.start.assert_called_once()
    assert controller.communicator is communicator
    assert controller.db is database


def test_constructor_skips_dummy_when_units_exist() -> None:
    communicator = MagicMock()
    database = MagicMock()
    database.get_all_units.return_value = [["1737b", 1, 1]]

    with (
        patch(
            "custom_components.becker.pybecker.becker.BeckerCommunicator",
            return_value=communicator,
        ),
        patch(
            "custom_components.becker.pybecker.becker.Database",
            return_value=database,
        ),
    ):
        Becker(init_dummy=True)

    database.init_dummy.assert_not_called()


def test_close_closes_communicator_and_database() -> None:
    controller = _controller()

    controller.close()

    controller.communicator.close.assert_called_once()
    controller.db.close.assert_called_once()


async def test_write_finalizes_and_queues_codes() -> None:
    controller = _controller()

    with patch(
        "custom_components.becker.pybecker.becker.asyncio.to_thread",
        new=AsyncMock(),
    ) as to_thread:
        await controller.write(["ABC", "DEF"])

    assert to_thread.await_count == 2
    assert to_thread.await_args_list[0].args[1] == b"\x02ABC\x03"
    assert to_thread.await_args_list[1].args[1] == b"\x02DEF\x03"


@pytest.mark.parametrize(
    ("command", "constants"),
    [
        (
            "CLEARPOS",
            [
                becker_module.COMMAND_PAIR,
                becker_module.COMMAND_CLEARPOS,
                becker_module.COMMAND_CLEARPOS2,
                becker_module.COMMAND_CLEARPOS3,
                becker_module.COMMAND_CLEARPOS4,
            ],
        ),
        (
            "REMOVE",
            [
                becker_module.COMMAND_PAIR2,
                0x00,
                becker_module.COMMAND_PAIR2,
                becker_module.COMMAND_PAIR3,
                becker_module.COMMAND_PAIR4,
            ],
        ),
        (
            "TRAINMASTER",
            [
                becker_module.COMMAND_PAIR,
                becker_module.COMMAND_PAIR2,
                becker_module.COMMAND_PAIR3,
                becker_module.COMMAND_PAIR4,
            ],
        ),
    ],
)
async def test_run_codes_extended_sequences(
    command: str, constants: list[int]
) -> None:
    controller = _controller()
    controller.write = AsyncMock()
    unit = ["1737b", 40, 1]

    with patch(
        "custom_components.becker.pybecker.becker.generate_code",
        side_effect=lambda channel, current_unit, cmd: (current_unit[1], cmd),
    ):
        await controller.run_codes(1, unit, command, False)

    sent = controller.write.await_args.args[0]
    assert [cmd for _, cmd in sent] == constants
    if command == "TRAINMASTER":
        assert unit[2] == 1


async def test_timed_down_move_sends_down_then_stop() -> None:
    controller = _controller()
    controller.write = AsyncMock()
    unit = ["1737b", 50, 1]

    with (
        patch(
            "custom_components.becker.pybecker.becker.generate_code",
            side_effect=lambda channel, current_unit, cmd: (current_unit[1], cmd),
        ),
        patch(
            "custom_components.becker.pybecker.becker.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
    ):
        await controller.run_codes(1, unit, "DOWN:3", False)

    assert controller.write.await_args_list[0].args[0] == [
        (50, becker_module.COMMAND_DOWN)
    ]
    assert controller.write.await_args_list[1].args[0] == [
        (51, becker_module.COMMAND_HALT)
    ]
    sleep.assert_awaited_once_with(3)
