from typing import Tuple, List

from amaranth import Module, Signal, Value, ClockSignal, ResetSignal

# from amaranth.asserts import Assert, Cover, Assume, Initial
from amaranth.asserts import Cover, Assume

from i2c import I2C, Speed


def formal() -> Tuple[Module, List[Signal | Value]]:
    # XXX(Ari): Next time we work on this, please note some changes I've made
    # to main.sby, specifically around the "techmap" call.
    # See https://github.com/amaranth-lang/amaranth/issues/526.
    m = Module()
    m.submodules.dut = dut = I2C(speed=Speed(100_000))

    sync_clk = ClockSignal("sync")
    sync_rst = ResetSignal("sync")

    past_clk = Signal()
    m.d.sync += past_clk.eq(sync_clk)

    m.d.comb += Assume(sync_clk == ~past_clk)
    m.d.comb += Assume(~sync_rst)

    m.d.comb += Cover(~dut.scl_o)

    # past_inv = Signal()
    # m.d.sync += past_inv.eq(dut.ssa.inv)
    # m.d.comb += Cover(~Initial() & ~past_inv & dut.ssa.inv)

    # m.d.comb += Assert(dut.ssa.inv == dut.ssb.inv)

    return m, [
        sync_clk,
        sync_rst,
        dut.i_addr,
        dut.i_rw,
        dut.i_stb,
        dut.fifo.w_data,
        dut.fifo.w_en,
    ]
