from typing import Callable, Literal, Optional, cast

from amaranth import Signal
from amaranth.sim import Delay

import sim
from .i2c import I2C

__all__ = [
    "start",
    "repeated_start",
    "send",
    "ack",
    "nack",
    "stop",
    "steady_stopped",
    "full_sequence",
]

# XXX: There's definitely some gentle drift happening here, but it needs really
# long runs to unearth.  Take care!


def _tick(i2c: I2C) -> float:
    return 0.1 / i2c.speed.value


def synchronise(i2c: I2C, start_value: int) -> sim.Generator:
    assert not (yield i2c.i_stb)
    assert (yield i2c.fifo.w_en)
    assert (yield i2c.fifo.w_data) == start_value
    assert not (yield i2c.fifo.r_rdy)
    yield Delay(sim.clock())

    # Data is enqueued, we're strobing I2C.  Lines still high.
    assert (yield i2c.i_stb)
    assert not (yield i2c.fifo.w_en)
    assert (yield i2c.fifo.r_rdy)
    assert (yield i2c.fifo.r_level) == 1

    assert (yield i2c.scl_o)
    assert (yield i2c.sda_o)
    yield Delay(sim.clock())


def start(i2c: I2C) -> sim.Generator:
    # Strobed.  I2C start condition.
    assert not (yield i2c.i_stb)
    assert (yield i2c.scl_o)
    assert not (yield i2c.sda_o)
    yield Delay(5 * _tick(i2c))

    # I2C clock starts.
    assert not (yield i2c.scl_o)
    assert not (yield i2c.sda_o)


def repeated_start(i2c: I2C) -> sim.Generator:
    assert not (yield i2c.scl_o)
    yield Delay(5 * _tick(i2c))

    assert (yield i2c.sda_o)
    yield Delay(5 * _tick(i2c))

    # I2C clock starts.
    assert not (yield i2c.scl_o)
    assert not (yield i2c.sda_o)


def send(
    i2c: I2C, byte: int, *, next: int | Literal["STOP"] | None = None
) -> sim.Generator:
    actual = 0
    for bit in range(8):
        yield Delay(sim.clock() * 2)
        if bit == 0:
            if isinstance(next, int):
                assert (yield i2c.fifo.r_rdy)
                assert (
                    yield i2c.fifo.w_data
                ) == next, f"checking next: expected {next:02x}, got {(yield i2c.fifo.w_data):02x}"
            elif next == "STOP":
                assert not (
                    yield i2c.fifo.r_rdy
                ), f"checking next: expected empty FIFO, contained ({(yield i2c.fifo.w_data):02x})"
        yield Delay(5 * _tick(i2c) - sim.clock() * 2)
        if bit == 0 and isinstance(next, int):
            assert not (yield i2c.fifo.w_en)
        assert (yield i2c.scl_o)
        actual = (actual << 1) | (yield i2c.sda_o)
        yield Delay(5 * _tick(i2c))

        assert not (yield i2c.scl_o), f"expected SCL low at end of bit {bit}"

    assert actual == byte, f"expected {byte:02x}, got {actual:02x}"


def ack(i2c: I2C, *, ack: bool = True) -> sim.Generator:
    # Master releases SDA; we ACK by driving SDA low.
    assert (yield i2c.sda_oe)
    yield Delay(_tick(i2c))
    if ack:
        yield cast(Signal, i2c.sda.i).eq(0)
    yield Delay(3 * _tick(i2c))
    assert not (yield i2c.sda_oe)
    yield Delay(_tick(i2c))

    yield Delay(4 * _tick(i2c))
    assert (yield i2c.sda_oe)
    if ack:
        yield cast(Signal, i2c.sda.i).eq(1)
    yield Delay(_tick(i2c))

    if ack:
        assert (yield i2c.o_ack)
    else:
        assert not (yield i2c.o_ack)


def nack(i2c: I2C) -> sim.Generator:
    yield from ack(i2c, ack=False)


def stop(i2c: I2C) -> sim.Generator:
    # While SCL is low, bring SDA low.
    last_sda = yield i2c.sda_o
    yield Delay(_tick(i2c))
    assert not (yield i2c.scl_o)
    assert (yield i2c.sda_o) == last_sda
    yield Delay(3 * _tick(i2c))
    assert not (yield i2c.scl_o)
    assert not (yield i2c.sda_o)
    yield Delay(_tick(i2c))

    # Then when SCL is high, bring SDA high.
    assert (yield i2c.scl_o)
    assert not (yield i2c.sda_o)
    yield Delay(_tick(i2c))
    assert not (yield i2c.sda_o)
    yield Delay(3 * _tick(i2c))
    assert (yield i2c.sda_o)
    yield Delay(_tick(i2c))


def steady_stopped(i2c: I2C) -> sim.Generator:
    for _ in range(3):
        yield Delay(sim.clock())
        assert (yield i2c.scl_o)
        assert (yield i2c.sda_o)

    assert not (yield i2c.fifo.r_rdy)
    assert not (yield i2c.o_busy)


def full_sequence(
    i2c: I2C,
    trigger: Callable[[], sim.Generator],
    sequences: list[int | list[int]],
    *,
    test_nacks: bool = True,
) -> sim.Generator:
    sequence: list[int] = []
    for item in sequences:
        if isinstance(item, int):
            sequence.append(item)
        else:
            sequence += item

    nacks: list[Optional[int]] = [None]
    if test_nacks:
        nacks += list(range(len(sequence)))

    for nack_after in nacks:
        yield from trigger()

        yield from synchronise(i2c, sequence[0])
        yield from start(i2c)

        for i, byte in enumerate(sequence):
            if (byte & 0x100) and i > 0:
                print("checking repeated")
                yield from repeated_start(i2c)

            check_byte = byte & 0xFF
            check_next = sequence[i + 1] if i < len(sequence) - 1 else "STOP"
            yield from send(i2c, check_byte, next=check_next)

            if check_next != "STOP":
                check_next = f"{check_next:02x}"
            print(f"got byte {check_byte:02x}, next {check_next}")

            if i == nack_after:
                yield from nack(i2c)
                break
            yield from ack(i2c)

        yield from stop(i2c)
        yield from steady_stopped(i2c)
