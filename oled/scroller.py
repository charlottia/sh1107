from typing import Optional

from amaranth import Elaboratable, Module, Signal
from amaranth.build import Platform

from i2c import RW, I2CBus, Transfer
from .sh1107 import Cmd, ControlByte

__all__ = ["Scroller"]


class Scroller(Elaboratable):
    addr: int

    i_stb: Signal
    i_rst: Signal

    o_busy: Signal
    o_adjusted: Signal

    i2c_bus: I2CBus

    def __init__(self, *, addr: int):
        self.addr = addr

        self.i_stb = Signal()
        self.i_rst = Signal()

        self.o_busy = Signal()
        self.o_adjusted = Signal(range(16))

        self.i2c_bus = I2CBus()

    def elaborate(self, platform: Optional[Platform]) -> Module:
        m = Module()

        transfer = Transfer(self.i2c_bus.i_in_fifo_w_data)

        offset_cmd = Cmd.SetDisplayStartLine(0).to_bytes()
        assert len(offset_cmd) == 2
        assert offset_cmd[1] == 0x00

        with m.FSM():
            with m.State("IDLE"):
                with m.If(self.i_stb):
                    m.d.sync += [
                        self.o_busy.eq(1),
                        transfer.kind.eq(Transfer.Kind.START),
                        transfer.payload.start.addr.eq(self.addr),
                        transfer.payload.start.rw.eq(RW.W),
                        self.i2c_bus.i_in_fifo_w_en.eq(1),
                        self.o_adjusted.eq(self.o_adjusted + 1),
                    ]
                    m.next = "START: ADDR: STROBED W_EN"
                with m.If(self.i_rst):
                    m.d.sync += self.o_adjusted.eq(0)

            with m.State("START: ADDR: STROBED W_EN"):
                m.d.sync += [
                    self.i2c_bus.i_in_fifo_w_en.eq(0),
                    self.i2c_bus.i_stb.eq(1),
                ]
                m.next = "START: ADDR: STROBED I_STB"

            with m.State("START: ADDR: STROBED I_STB"):
                m.d.sync += self.i2c_bus.i_stb.eq(0)
                with m.If(self.i2c_bus.o_in_fifo_w_rdy):
                    m.d.sync += [
                        transfer.kind.eq(Transfer.Kind.DATA),
                        transfer.payload.data.eq(
                            ControlByte(False, "Command").to_byte()
                        ),
                        self.i2c_bus.i_in_fifo_w_en.eq(1),
                    ]
                    m.next = "START: CONTROL: STROBED W_EN"

            with m.State("START: CONTROL: STROBED W_EN"):
                m.d.sync += self.i2c_bus.i_in_fifo_w_en.eq(0)
                m.next = "START: CONTROL: UNSTROBED W_EN"

            with m.State("START: CONTROL: UNSTROBED W_EN"):
                with m.If(
                    self.i2c_bus.o_busy
                    & self.i2c_bus.o_ack
                    & self.i2c_bus.o_in_fifo_w_rdy
                ):
                    m.d.sync += [
                        transfer.payload.data.eq(offset_cmd[0]),
                        self.i2c_bus.i_in_fifo_w_en.eq(1),
                    ]
                    m.next = "START: OFFSET_CMD[0]: STROBED W_EN"
                with m.Elif(~self.i2c_bus.o_busy):
                    m.d.sync += self.o_busy.eq(0)
                    m.next = "IDLE"

            with m.State("START: OFFSET_CMD[0]: STROBED W_EN"):
                m.d.sync += self.i2c_bus.i_in_fifo_w_en.eq(0)
                m.next = "START: OFFSET_CMD[0]: UNSTROBED W_EN"

            with m.State("START: OFFSET_CMD[0]: UNSTROBED W_EN"):
                with m.If(
                    self.i2c_bus.o_busy
                    & self.i2c_bus.o_ack
                    & self.i2c_bus.o_in_fifo_w_rdy
                ):
                    m.d.sync += [
                        transfer.payload.data.eq(self.o_adjusted * 8),
                        self.i2c_bus.i_in_fifo_w_en.eq(1),
                    ]
                    m.next = "START: OFFSET_CMD[1]: STROBED W_EN"
                with m.Elif(~self.i2c_bus.o_busy):
                    m.d.sync += self.o_busy.eq(0)
                    m.next = "IDLE"

            with m.State("START: OFFSET_CMD[1]: STROBED W_EN"):
                m.d.sync += self.i2c_bus.i_in_fifo_w_en.eq(0)
                m.next = "START: OFFSET_CMD[1]: UNSTROBED W_EN"

            with m.State("START: OFFSET_CMD[1]: UNSTROBED W_EN"):
                with m.If(
                    ~self.i2c_bus.o_busy
                    & self.i2c_bus.o_ack
                    & self.i2c_bus.o_in_fifo_w_rdy
                ):
                    m.d.sync += self.o_busy.eq(0)
                    m.next = "IDLE"
                with m.Elif(~self.i2c_bus.o_busy):
                    m.d.sync += self.o_busy.eq(0)
                    m.next = "IDLE"

        return m
