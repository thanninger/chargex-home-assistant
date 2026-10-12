"""Protocol and HA tests: simulated chargers, real HA and TCP backend."""

import ast
import asyncio
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from modbus_connection import (
    IllegalDataAddressError,
    ModbusExceptionError,
    ModbusTcpParams,
)
from modbus_connection.tmodbus import ModbusConnection

from custom_components.chargex.api import ChargeXApi, InvalidDevice, InvalidResponse
from custom_components.chargex.binary_sensor import MODULE as FLAGS
from custom_components.chargex.binary_sensor import ChargeXBinarySensor
from custom_components.chargex.coordinator import ChargeXCoordinator
from custom_components.chargex.diagnostics import async_get_config_entry_diagnostics
from custom_components.chargex.sensor import MODULE, ChargeXSensor


def registers(count=2):
    result = {}

    def number(addr, value):
        result[addr], result[addr + 1] = value >> 16, value & 65535

    def string(addr, value):
        data = value.encode().ljust(16, b" ")
        result.update(
            {addr + i: int.from_bytes(data[i * 2 : i * 2 + 2], "big") for i in range(8)}
        )

    number(2, 10)
    for addr, value in (
        (4, "ChargeX"),
        (12, "Aqueduct 2.0"),
        (20, "2.1.0"),
        (28, "CXTEST0001"),
    ):
        string(addr, value)
    for i, value in enumerate(
        (7200, 6900, 12000, 11000, 10000, 2, 1, count, 43, 16000)
    ):
        number(36 + i * 2, value)
    for index in range(count):
        for i, value in enumerate(
            (
                3600 + index * 100,
                12000,
                0,
                0,
                29 if index == 0 else 40,
                0 if index == 0 else 1 << 16,
            )
        ):
            number(100 + index * 12 + i * 2, value)
    for addr, value in (
        (56, 1),
        (58, int(datetime.now(UTC).timestamp())),
        (60, 1700000000),
        (500, 1200),
        (502, 11000),
        (504, 8000),
        (506, 1),
        (62, 0),
    ):
        number(addr, value)
    return result


class FakeUnit:
    def __init__(self, values=None):
        self.values = values if values is not None else registers()
        self.calls = []
        self.failures = {}
        self.disconnected = 0

    async def read_holding_registers(self, address, count):
        self.calls.append((address, count))
        if address in self.failures:
            raise self.failures[address]
        return [self.values[address + i] for i in range(count)]

    async def disconnect(self):
        self.disconnected += 1


def entry():
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain="chargex",
        title="Testsystem",
        data={
            "host": "127.0.0.1",
            "port": 1502,
            "unit_id": 10,
            "point_names": "Ladepunkt 1,Ladepunkt 2",
        },
        options={},
        source="user",
        unique_id="CXTEST0001",
        discovery_keys={},
        subentries_data=[],
        state=ConfigEntryState.SETUP_IN_PROGRESS,
    )


@pytest.mark.asyncio
async def test_decoding():
    unit = FakeUnit()
    api = ChargeXApi(unit)
    identity = await api.identity()
    assert identity.serial == "CXTEST0001" and identity.model == "Aqueduct 2.0"
    data = await api.update()
    assert data.modules[0]["power"] == 3600 and data.modules[1]["power"] == 3700
    assert data.modules[1]["errors"] == 65536 and data.system["current_1"] == 12000
    assert data.system["charging_mode"] == 1
    assert (100, 12) in unit.calls and (112, 12) in unit.calls
    assert not any(address in (62, 63) for address, _ in unit.calls)


@pytest.mark.asyncio
async def test_missing_optional_and_transient_errors():
    unit = FakeUnit()
    unit.failures[58] = IllegalDataAddressError()
    api = ChargeXApi(unit)
    assert (await api.update()).system["meter_timestamp"] is None
    unit.failures[56] = ModbusExceptionError(4)
    assert (await api.update()).system["eligible_modules"] is None
    del unit.failures[56]
    assert (await api.update()).system["eligible_modules"] == 1
    unit.values[57] = 404
    assert (await api.update()).system["eligible_modules"] is None
    assert unit.calls.count((58, 2)) == 1


@pytest.mark.asyncio
async def test_bad_count_and_identity_and_short_response():
    for count in (0, 16):
        with pytest.raises(InvalidResponse):
            await ChargeXApi(FakeUnit(registers(count))).update()
    unit = FakeUnit()
    unit.values[3] = 1
    with pytest.raises(InvalidDevice):
        await ChargeXApi(unit).identity()

    class Short(FakeUnit):
        async def read_holding_registers(self, address, count):
            return [0]

    with pytest.raises(InvalidResponse):
        await ChargeXApi(Short()).update()


@pytest.mark.asyncio
async def test_ha_entities_staleness_recovery_and_redaction(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    config = entry()
    unit = FakeUnit()
    coordinator = ChargeXCoordinator(hass, config, unit)
    config.runtime_data = coordinator
    await coordinator._async_setup()
    coordinator.data = await coordinator._async_update_data()
    coordinator.last_update_success = True
    coordinator.system_device_id = "system-device-id"
    sensors = {spec[0]: ChargeXSensor(coordinator, spec, 0) for spec in MODULE}
    assert (
        sensors["power"].native_value == 3600
        and sensors["current_1"].native_value == 12
    )
    assert (
        sensors["power"].available
        and sensors["power"].device_info["name"] == "Ladepunkt 1"
    )
    assert sensors["power"].device_info["via_device_id"] == "system-device-id"
    flags = {spec[0]: ChargeXBinarySensor(coordinator, spec, 0) for spec in FLAGS}
    assert flags["charging"].is_on and flags["vehicle_connected"].is_on
    assert not flags["battery_full"].is_on
    assert ChargeXBinarySensor(coordinator, FLAGS[4], 1).is_on
    assert (
        ChargeXSensor(coordinator, MODULE[-1], 1).native_value
        == "Zählerkommunikation gestört"
    )
    assert sensors["phase_mode"].native_value == "single_phase"
    coordinator.data.system["meter_timestamp"] = (
        int(datetime.now(UTC).timestamp()) - 1000
    )
    assert not sensors["power"].available and flags["charging"].available
    coordinator.last_update_success = False
    assert not flags["charging"].available
    coordinator.last_update_success = True
    coordinator.data.modules.pop()
    assert not ChargeXBinarySensor(coordinator, FLAGS[0], 1).available
    unit.failures[100] = OSError("dropped")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert unit.disconnected == 1
    del unit.failures[100]
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data.modules[0]["power"] == 3600
    data = await async_get_config_entry_diagnostics(hass, config)
    assert data["configuration"]["host"] == "**REDACTED**"
    assert data["identity"]["serial"] == "**REDACTED**"
    await asyncio.wait_for(hass.async_stop(), 10)


@pytest.mark.asyncio
async def test_real_tcp_read_only_and_reconnect():
    values = registers()
    functions = []
    connections = []

    async def handler(reader, writer):
        connections.append(writer)
        try:
            while True:
                header = await reader.readexactly(7)
                transaction, _protocol, length, unit = struct.unpack(">HHHB", header)
                request = await reader.readexactly(length - 1)
                functions.append(request[0])
                assert request[0] == 3
                address, count = struct.unpack(">HH", request[1:])
                body = bytes([3, count * 2]) + struct.pack(
                    ">" + ("H" * count), *[values[address + i] for i in range(count)]
                )
                writer.write(
                    struct.pack(">HHHB", transaction, 0, len(body) + 1, unit) + body
                )
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    connection = ModbusConnection(
        ModbusTcpParams(host="127.0.0.1", port=server.sockets[0].getsockname()[1])
    )
    try:
        api = ChargeXApi(connection.for_unit(10))
        assert (await api.identity()).serial == "CXTEST0001"
        assert len((await api.update()).modules) == 2
        await connection.disconnect()
        assert len((await api.update()).modules) == 2
        assert len(connections) == 2 and set(functions) == {3}
    finally:
        await connection.close()
        server.close()
        await server.wait_closed()


def test_no_mutation_calls():
    forbidden = {
        "write_register",
        "write_registers",
        "write_coil",
        "write_coils",
        "mask_write_register",
        "read_write_registers",
        "write_file_record",
        "diagnostics",
    }
    for path in (Path(__file__).parents[1] / "custom_components/chargex").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden, (path, node.lineno)


@pytest.mark.asyncio
async def test_unconfirmed_error_mask_does_not_report_all_errors(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    coordinator = ChargeXCoordinator(hass, entry(), FakeUnit())
    await coordinator._async_setup()
    coordinator.data = await coordinator._async_update_data()
    coordinator.data.modules[1]["errors"] = 0xFFFFFFFF
    coordinator.data.modules[1]["status"] |= 2
    assert not ChargeXBinarySensor(coordinator, FLAGS[-1], 1).available
    assert (
        ChargeXSensor(coordinator, MODULE[-1], 1).native_value
        == "Unbestätigter Fehlerwert (0xFFFFFFFF)"
    )
    assert ChargeXSensor(coordinator, MODULE[-2], 1).native_value == "three_phase"
    await asyncio.wait_for(hass.async_stop(), 10)


@pytest.mark.asyncio
async def test_full_ha_config_setup_options_duplicate_and_unload(tmp_path):
    import shutil

    from homeassistant import config_entries
    from homeassistant.helpers import device_registry, entity_registry
    from homeassistant.loader import async_setup as setup_loader
    from homeassistant.setup import async_setup_component

    values = registers()
    functions = []

    async def handler(reader, writer):
        try:
            while True:
                header = await reader.readexactly(7)
                transaction, _, length, unit = struct.unpack(">HHHB", header)
                request = await reader.readexactly(length - 1)
                functions.append(request[0])
                assert request[0] == 3
                address, count = struct.unpack(">HH", request[1:])
                body = bytes([3, count * 2]) + struct.pack(
                    ">" + ("H" * count), *[values[address + i] for i in range(count)]
                )
                writer.write(
                    struct.pack(">HHHB", transaction, 0, len(body) + 1, unit) + body
                )
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    shutil.copytree(
        Path(__file__).parents[1] / "custom_components", tmp_path / "custom_components"
    )
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    hass = HomeAssistant(str(tmp_path))
    setup_loader(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    from homeassistant.helpers import (
        area_registry,
        floor_registry,
        frame,
        label_registry,
    )

    frame.async_setup(hass)
    device_registry.async_setup(hass)
    await asyncio.gather(
        area_registry.async_load(hass),
        floor_registry.async_load(hass),
        label_registry.async_load(hass),
        device_registry.async_load(hass),
        entity_registry.async_load(hass),
    )
    await hass.config_entries.async_initialize()
    await hass.async_start()
    try:
        assert await async_setup_component(hass, "chargex", {})
        flow = await hass.config_entries.flow.async_init(
            "chargex", context={"source": "user"}
        )
        inputs = {
            "name": "Testsystem",
            "host": "127.0.0.1",
            "port": port,
            "unit_id": 10,
            "scan_interval": 15,
            "point_names": "Ladepunkt 1,Ladepunkt 2",
        }
        bad = await hass.config_entries.flow.async_configure(
            flow["flow_id"], inputs | {"point_names": "Only one"}
        )
        assert bad["errors"] == {"point_names": "wrong_point_names"}
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], inputs)
        assert result["type"] == "create_entry"
        await asyncio.wait_for(hass.async_block_till_done(), 15)
        config = result["result"]
        assert config.state is ConfigEntryState.LOADED
        registry = entity_registry.async_get(hass)
        entities = entity_registry.async_entries_for_config_entry(
            registry, config.entry_id
        )
        assert len(entities) == 53
        power = next(e for e in entities if e.unique_id == "CXTEST0001:module:0:power")
        assert power.entity_id == "sensor.chargex_ladepunkt_1_ladeleistung"
        assert hass.states.get(power.entity_id).state == "3600"
        assert all(
            e.entity_id.split(".", 1)[1].startswith("chargex_") for e in entities
        )
        devices = device_registry.async_entries_for_config_entry(
            device_registry.async_get(hass), config.entry_id
        )
        assert len(devices) == 3
        # Simulate an entity installed with 0.1.0: a reload must preserve its ID.
        registry.async_update_entity(
            power.entity_id, new_entity_id="sensor.ladepunkt_1_ladeleistung"
        )
        await asyncio.wait_for(hass.async_block_till_done(), 15)
        other = await hass.config_entries.flow.async_init(
            "chargex", context={"source": "user"}
        )
        duplicate = await hass.config_entries.flow.async_configure(
            other["flow_id"], inputs
        )
        assert (
            duplicate["type"] == "abort" and duplicate["reason"] == "already_configured"
        )
        options = await hass.config_entries.options.async_init(config.entry_id)
        changed = await hass.config_entries.options.async_configure(
            options["flow_id"],
            {
                "scan_interval": 30,
                "point_names": "Ladepunkt 1,Ladepunkt 2",
                "stale_seconds": 180,
                "system_error_address": 0,
            },
        )
        assert changed["type"] == "create_entry"
        await asyncio.wait_for(hass.async_block_till_done(), 15)
        assert config.runtime_data.update_interval.total_seconds() == 30
        assert config.state is ConfigEntryState.LOADED
        assert (
            registry.async_get_entity_id("sensor", "chargex", power.unique_id)
            == "sensor.ladepunkt_1_ladeleistung"
        )
        before = len(functions)
        preview = await hass.services.async_call(
            "chargex", "prefix_entity_ids", {}, blocking=True, return_response=True
        )
        assert preview["preview"] and preview["applied"] == []
        assert preview["changes"] == [
            {
                "old": "sensor.ladepunkt_1_ladeleistung",
                "new": "sensor.chargex_ladepunkt_1_ladeleistung",
            }
        ]
        assert registry.async_get("sensor.ladepunkt_1_ladeleistung") is not None
        hass.states.async_set("sensor.chargex_ladepunkt_1_ladeleistung", "external")
        collision = await hass.services.async_call(
            "chargex",
            "prefix_entity_ids",
            {"apply": True},
            blocking=True,
            return_response=True,
        )
        assert collision["applied"] == []
        assert collision["skipped"][0]["reason"] == "target_exists"
        assert registry.async_get("sensor.ladepunkt_1_ladeleistung") is not None
        assert (
            hass.states.get("sensor.chargex_ladepunkt_1_ladeleistung").state
            == "external"
        )
        hass.states.async_remove("sensor.chargex_ladepunkt_1_ladeleistung")
        applied = await hass.services.async_call(
            "chargex",
            "prefix_entity_ids",
            {"apply": True},
            blocking=True,
            return_response=True,
        )
        await asyncio.wait_for(hass.async_block_till_done(), 15)
        assert applied["applied"] == preview["changes"]
        assert len(functions) == before
        assert registry.async_get("sensor.ladepunkt_1_ladeleistung") is None
        assert hass.states.get(power.entity_id).state == "3600"
        again = await hass.services.async_call(
            "chargex",
            "prefix_entity_ids",
            {"apply": True},
            blocking=True,
            return_response=True,
        )
        assert again["changes"] == []
        assert set(functions) == {3}
        assert await hass.config_entries.async_unload(config.entry_id)
        await asyncio.wait_for(hass.async_block_till_done(), 15)
        assert hass.states.get(power.entity_id).state == "unavailable"
        assert config.state is ConfigEntryState.NOT_LOADED
    finally:
        await asyncio.wait_for(hass.async_stop(), 10)
        server.close()
        await server.wait_closed()
