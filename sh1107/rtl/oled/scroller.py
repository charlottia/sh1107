from typing import cast

from amaranth import Cat, Elaboratable, Module, Mux, Signal
from amaranth.lib.wiring import Component, In, Out

from ... import rom
from ...platform import Platform
from ..i2c import RW, I2CBus, Transfer
from .rom_bus import ROMBus

__all__ = ["Scroller"]


class Scroller(Component):
    _addr: int

    stb: Out(1)
    rst: Out(1)
    i2c_bus: Out(I2CBus)
    rom_bus: Out(ROMBus(rom.ROM_ABITS, 8))

    busy: In(1)
    adjusted: In(range(16))

    _offset: Signal
    _remain: Signal
    _written: Signal

    def __init__(self, *, addr: int):
        super().__init__()
        self._addr = addr

        self._offset = Signal(range(rom.ROM_LENGTH))
        self._remain = Signal(range(rom.ROM_LENGTH))
        self._written = Signal(range(rom.ROM_LENGTH))

    def elaborate(self, platform: Platform) -> Elaboratable:
        # XXX: This is an exact copy of ROMWriter with some bits added.
        m = Module()

        transfer = Transfer(self.i2c_bus.in_fifo_w_data)

        with m.FSM():
            with m.State("IDLE"):
                with m.If(self.stb):
                    m.d.sync += [
                        self.rom_bus.addr.eq(rom.OFFSET_SCROLL * 4),
                        self.busy.eq(1),
                        self._written.eq(0),
                    ]
                    m.next = "START: ADDRESSED OFFSET[0]"
                with m.If(self.rst):
                    m.d.sync += self.adjusted.eq(0)

            with m.State("START: ADDRESSED OFFSET[0]"):
                m.d.sync += self.rom_bus.addr.eq(self.rom_bus.addr + 1)
                m.next = "START: ADDRESSED OFFSET[1], OFFSET[0] AVAILABLE"

            with m.State("START: ADDRESSED OFFSET[1], OFFSET[0] AVAILABLE"):
                m.d.sync += [
                    self.rom_bus.addr.eq(self.rom_bus.addr + 1),
                    self._offset.eq(self.rom_bus.data),
                ]
                m.next = "START: ADDRESSED LEN[0], OFFSET[1] AVAILABLE"

            with m.State("START: ADDRESSED LEN[0], OFFSET[1] AVAILABLE"):
                m.d.sync += [
                    self.rom_bus.addr.eq(self.rom_bus.addr + 1),
                    self._offset.eq(self._offset | self.rom_bus.data.shift_left(8)),
                ]
                m.next = "START: ADDRESSED LEN[1], LEN[0] AVAILABLE"

            with m.State("START: ADDRESSED LEN[1], LEN[0] AVAILABLE"):
                m.d.sync += [
                    self._remain.eq(self.rom_bus.data),
                    self.rom_bus.addr.eq(self._offset),
                ]
                m.next = "START: ADDRESSED *OFFSET, LEN[1] AVAILABLE"

            with m.State("START: ADDRESSED *OFFSET, LEN[1] AVAILABLE"):
                m.d.sync += [
                    self._remain.eq(self._remain | self.rom_bus.data.shift_left(8)),
                    transfer.kind.eq(Transfer.Kind.START),
                    transfer.payload.start.addr.eq(self._addr),
                    transfer.payload.start.rw.eq(RW.W),
                    self.i2c_bus.in_fifo_w_en.eq(1),
                ]
                m.next = "ADDRESS PERIPHERAL: LATCHED W_EN"

            with m.State("ADDRESS PERIPHERAL: LATCHED W_EN"):
                m.d.sync += [
                    self.i2c_bus.in_fifo_w_en.eq(0),
                    self.i2c_bus.stb.eq(1),
                ]
                m.next = "LOOP HEAD: SEQ BREAK OR WAIT I2C"

            with m.State("LOOP HEAD: SEQ BREAK OR WAIT I2C"):
                m.d.sync += self.i2c_bus.stb.eq(0)
                with m.If(self._remain == 0):
                    m.d.sync += [
                        self.rom_bus.addr.eq(self._offset + 1),
                        self._offset.eq(self._offset + 1),
                    ]
                    m.next = "SEQ BREAK: ADDRESSED NEXTLEN[1], NEXTLEN[0] AVAILABLE"
                with m.Elif(self.i2c_bus.in_fifo_w_rdy):
                    m.d.sync += [
                        self._offset.eq(self._offset + 1),
                        self._remain.eq(self._remain - 1),
                        transfer.kind.eq(Transfer.Kind.DATA),
                        self.i2c_bus.in_fifo_w_en.eq(1),
                        self._written.eq(self._written + 1),
                    ]

                    with m.If(
                        self._written
                        == rom.SCROLL_OFFSETS["InitialHigherColumnAddress"]
                    ):
                        m.d.sync += transfer.payload.data.eq(
                            self.rom_bus.data + (self.adjusted >> 1)
                        )
                    for i in range(8):
                        with m.Elif(
                            self._written
                            == rom.SCROLL_OFFSETS[f"LowerColumnAddress{i}"]
                        ):
                            m.d.sync += transfer.payload.data.eq(
                                self.rom_bus.data + (self.adjusted[0] << 3)
                            )
                    with m.Elif(
                        self._written == rom.SCROLL_OFFSETS["DisplayStartLine"] + 1
                    ):
                        m.d.sync += transfer.payload.data.eq(
                            Mux(self.adjusted == 15, 0, 8 + self.adjusted * 8)
                        )
                    with m.Else():
                        m.d.sync += transfer.payload.data.eq(self.rom_bus.data)

                    # Prepare next read, whether it's data or NEXTLEN[0].
                    m.d.sync += self.rom_bus.addr.eq(self._offset + 1)
                    m.next = "SEND: LATCHED W_EN"

            with m.State("SEND: LATCHED W_EN"):
                m.d.sync += self.i2c_bus.in_fifo_w_en.eq(0)
                m.next = "SEND: WAIT FOR I2C"

            with m.State("SEND: WAIT FOR I2C"):
                with m.If(
                    self.i2c_bus.busy & self.i2c_bus.ack & self.i2c_bus.in_fifo_w_rdy
                ):
                    m.next = "LOOP HEAD: SEQ BREAK OR WAIT I2C"
                with m.Elif(~self.i2c_bus.busy):
                    # Failed.  Stop.
                    m.d.sync += self.busy.eq(0)
                    m.next = "IDLE"

            with m.State("SEQ BREAK: ADDRESSED NEXTLEN[1], NEXTLEN[0] AVAILABLE"):
                m.d.sync += [
                    self._remain.eq(self.rom_bus.data),
                    self.rom_bus.addr.eq(self._offset + 1),
                    self._offset.eq(self._offset + 1),
                ]
                m.next = "SEQ BREAK: ADDRESSED FOLLOWING, NEXTLEN[1] AVAILABLE"

            with m.State("SEQ BREAK: ADDRESSED FOLLOWING, NEXTLEN[1] AVAILABLE"):
                _remain = self._remain | cast(Cat, self.rom_bus.data.shift_left(8))
                with m.If(_remain == 0):
                    m.next = "FIN: WAIT I2C DONE"
                with m.Else():
                    m.d.sync += [
                        self._remain.eq(_remain),
                        transfer.kind.eq(Transfer.Kind.START),
                        transfer.payload.start.addr.eq(self._addr),
                        transfer.payload.start.rw.eq(RW.W),
                        self.i2c_bus.in_fifo_w_en.eq(1),
                    ]
                    m.next = "SEQ BREAK: LATCHED W_EN"

            with m.State("SEQ BREAK: LATCHED W_EN"):
                m.d.sync += self.i2c_bus.in_fifo_w_en.eq(0)
                m.next = "LOOP HEAD: SEQ BREAK OR WAIT I2C"

            with m.State("FIN: WAIT I2C DONE"):
                with m.If(
                    ~self.i2c_bus.busy & self.i2c_bus.ack & self.i2c_bus.in_fifo_w_rdy
                ):
                    m.d.sync += [
                        self.adjusted.eq(self.adjusted + 1),
                        self.busy.eq(0),
                    ]
                    m.next = "IDLE"
                with m.Elif(~self.i2c_bus.busy):
                    m.d.sync += self.busy.eq(0)
                    m.next = "IDLE"

        return m
