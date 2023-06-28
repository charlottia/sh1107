import math
from typing import Final, Optional

from amaranth import C, Cat, ClockSignal, Instance, Memory, Module, Mux, Signal
from amaranth.build import Platform
from amaranth.lib.enum import IntEnum
from amaranth.lib.fifo import SyncFIFO
from amaranth_boards.icebreaker import ICEBreakerPlatform

from ... import rom
from ...base import Blackbox, Config, ConfigElaboratable
from ..common import Hz
from ..i2c import I2C, I2CBus
from ..spi import SPIFlashReader, SPIFlashReaderBus
from .clser import Clser
from .locator import Locator
from .rom_bus import ROMBus
from .rom_writer import ROMWriter
from .scroller import Scroller

__all__ = ["OLED"]


class OLED(ConfigElaboratable):
    ADDR: Final[int] = 0x3C

    # 1MHz is a bit unacceptable.  It seems to mostly work, except that
    # switching between command and data before doing a read isn't consistent.
    # There's a clear reason why this might be the case: the SH1107 datasheet
    # specifies 400kHz as the maximum SCL clock frequency, and further specifies
    # a bunch of timings that we don't meet at 1MHz — particularly
    # START/STOP/RESTART hold times, which are all listed as min 0.6μs.  At
    # 1MHz, we're only holding for 0.5μs.
    #
    # I tried adding some delays after switching to command mode (i.e. add some
    # extra commands!) before restarting the transaction in read, but it still
    # ended up giving me display RAM data back.  This doesn't happen at 400kHz.
    VALID_BUILD_SPEEDS: Final[list[int]] = [
        100_000,
        400_000,
    ]
    VALID_SPEEDS: Final[list[int]] = VALID_BUILD_SPEEDS + [
        2_000_000,  # for vsh
    ]
    DEFAULT_SPEED: Final[int] = 400_000
    DEFAULT_SPEED_VSH: Final[int] = 2_000_000

    class Command(IntEnum, shape=8):
        NOP = 0x00
        INIT = 0x01
        DISPLAY_ON = 0x02
        DISPLAY_OFF = 0x03
        CLS = 0x04
        LOCATE = 0x05
        PRINT = 0x06
        CURSOR_ON = 0x07
        CURSOR_OFF = 0x08
        ID = 0x09
        PRINT_BYTE = 0x0A
        SPI_TEST = 0x0B

    class Result(IntEnum, shape=2):
        SUCCESS = 0
        BUSY = 1
        FAILURE = 2

    i2c_bus: I2CBus
    own_i2c_bus: I2CBus
    i2c: I2C | Instance
    # For blackbox simulation only; not defined otherwise.
    i_i2c_bb_in_ack: Signal
    i_i2c_bb_in_out_fifo_data: Signal
    i_i2c_bb_in_out_fifo_stb: Signal

    spifr_bus: SPIFlashReaderBus
    spifr: SPIFlashReader | Instance

    rom_wr_en: Signal
    rom_wr_data: Signal
    rom_bus: ROMBus
    own_rom_bus: ROMBus
    rom_mem: Instance | Memory

    rom_writer: ROMWriter
    locator: Locator
    clser: Clser
    scroller: Scroller

    i_fifo: SyncFIFO
    o_result: Signal

    row: Signal
    col: Signal
    cursor: Signal

    chpr_data: Signal
    chpr_run: Signal

    def __init__(
        self,
        *,
        config: Config,
        speed: Hz,
    ):
        super().__init__(config)

        assert speed.value in self.VALID_SPEEDS

        self.i2c_bus = I2CBus()
        self.own_i2c_bus = I2CBus()
        if Blackbox.I2C not in self.config.blackboxes:
            self.i2c = I2C(speed=speed)
        else:
            self.i_i2c_bb_in_ack = Signal()
            self.i_i2c_bb_in_out_fifo_data = Signal(8)
            self.i_i2c_bb_in_out_fifo_stb = Signal()
            self.i2c = Instance(
                "i2c",
                i_clk=ClockSignal(),
                i_in_fifo_w_data=self.i2c_bus.i_in_fifo_w_data,
                i_in_fifo_w_en=self.i2c_bus.i_in_fifo_w_en,
                i_out_fifo_r_en=self.i2c_bus.i_out_fifo_r_en,
                i_stb=self.i2c_bus.i_stb,
                i_bb_in_ack=self.i_i2c_bb_in_ack,
                i_bb_in_out_fifo_data=self.i_i2c_bb_in_out_fifo_data,
                i_bb_in_out_fifo_stb=self.i_i2c_bb_in_out_fifo_stb,
                o_ack=self.i2c_bus.o_ack,
                o_busy=self.i2c_bus.o_busy,
                o_in_fifo_w_rdy=self.i2c_bus.o_in_fifo_w_rdy,
                o_out_fifo_r_rdy=self.i2c_bus.o_out_fifo_r_rdy,
                o_out_fifo_r_data=self.i2c_bus.o_out_fifo_r_data,
            )

        self.spifr_bus = SPIFlashReaderBus()
        if Blackbox.SPIFR not in self.config.blackboxes:
            self.spifr = SPIFlashReader(config=self.config)
        else:
            self.spifr = Instance(
                "spifr",
                i_clk=ClockSignal(),
                i_addr=self.spifr_bus.addr,
                i_len=self.spifr_bus.len,
                i_stb=self.spifr_bus.stb,
                o_busy=self.spifr_bus.busy,
                o_data=self.spifr_bus.data,
                o_valid=self.spifr_bus.valid,
            )

        self.rom_wr_en = Signal()
        self.rom_wr_data = Signal(8)
        abits = math.ceil(math.log2(rom.ROM_LENGTH))
        self.rom_bus = ROMBus(abits, 8)
        self.own_rom_bus = self.rom_bus.clone()

        self.rom_writer = ROMWriter(rom_bus=self.rom_bus, addr=OLED.ADDR)
        self.locator = Locator(addr=OLED.ADDR)
        self.clser = Clser(addr=OLED.ADDR)
        self.scroller = Scroller(rom_bus=self.rom_bus, addr=OLED.ADDR)

        self.i_fifo = SyncFIFO(width=8, depth=1)
        self.o_result = Signal(OLED.Result, reset=OLED.Result.BUSY)

        self.row = Signal(range(1, 17), reset=1)
        self.col = Signal(range(1, 17), reset=1)
        self.cursor = Signal()

        self.chpr_data = Signal(8)
        self.chpr_run = Signal()

    def elaborate(self, platform: Optional[Platform]) -> Module:
        m = Module()

        packed_size = math.ceil(rom.ROM_LENGTH / 2)

        addr = Signal(math.ceil(math.log2(packed_size)))
        rd_data = Signal(16)
        wr_en = Signal(2)

        m.d.comb += [
            addr.eq(self.rom_bus.addr >> 1),
            self.rom_bus.data.eq(rd_data.word_select(self.rom_bus.addr[0], 8)),
            wr_en.eq(Mux(self.rom_wr_en, 1 + (self.rom_bus.addr[0]), 0)),
        ]

        match platform:
            case ICEBreakerPlatform():
                self.rom_mem = Instance(
                    "$mem",
                    a_ram_style="huge",
                    p_MEMID="\\rom_mem",
                    p_SIZE=packed_size,
                    p_ABITS=len(addr),
                    p_WIDTH=16,
                    p_INIT=C(0, 0),
                    p_OFFSET=0,
                    p_RD_PORTS=1,
                    p_RD_CLK_ENABLE=C(1, 1),
                    p_RD_CLK_POLARITY=C(1, 1),
                    p_RD_TRANSPARENT=C(1, 1),
                    p_WR_PORTS=1,
                    p_WR_CLK_ENABLE=C(1, 1),
                    p_WR_CLK_POLARITY=C(1, 1),
                    i_RD_CLK=ClockSignal(),
                    i_RD_EN=1,
                    i_RD_ADDR=addr,
                    o_RD_DATA=rd_data,
                    i_WR_CLK=ClockSignal(),
                    i_WR_EN=Cat(wr_en[0].replicate(8), wr_en[1].replicate(8)),
                    i_WR_ADDR=addr,
                    i_WR_DATA=self.rom_wr_data.replicate(2),
                )
                m.submodules.rom_mem = self.rom_mem
            case _:
                # OrangeCrab, simulation, etc.
                # As is typical, zero-init ends up making the bitstream larger
                # than if we'd put actual data in it, so this is very much for
                # Fun(tm).
                self.rom_mem = Memory(width=16, depth=packed_size)
                m.submodules.rom_rd = rom_rd = self.rom_mem.read_port()
                m.submodules.rom_wr = rom_wr = self.rom_mem.write_port(granularity=8)
                m.d.comb += [
                    rom_rd.addr.eq(addr),
                    rd_data.eq(rom_rd.data),
                    rom_wr.addr.eq(addr),
                    rom_wr.data.eq(self.rom_wr_data.replicate(2)),
                    rom_wr.en.eq(self.rom_wr_en),
                ]

        if Blackbox.I2C not in self.config.blackboxes:
            m.d.comb += self.i2c.bus.connect(self.i2c_bus)

        if Blackbox.SPIFR not in self.config.blackboxes:
            m.d.comb += self.spifr.bus.connect(self.spifr_bus)

        m.submodules.i2c = self.i2c
        m.submodules.spifr = self.spifr
        m.submodules.rom_writer = self.rom_writer
        m.submodules.locator = self.locator
        m.submodules.clser = self.clser
        m.submodules.scroller = self.scroller

        m.submodules.i_fifo = self.i_fifo

        with m.If(self.rom_writer.o_busy):
            m.d.comb += [
                self.i2c_bus.connect(self.rom_writer.i2c_bus),
                self.rom_bus.connect(self.rom_writer.rom_bus),
            ]
        with m.Elif(self.locator.o_busy):
            m.d.comb += self.i2c_bus.connect(self.locator.i2c_bus)
        with m.Elif(self.clser.o_busy):
            m.d.comb += self.i2c_bus.connect(self.clser.i2c_bus)
        with m.Elif(self.scroller.o_busy):
            m.d.comb += [
                self.i2c_bus.connect(self.scroller.i2c_bus),
                self.rom_bus.connect(self.scroller.rom_bus),
            ]
        with m.Else():
            m.d.comb += [
                self.i2c_bus.connect(self.own_i2c_bus),
                self.rom_bus.connect(self.own_rom_bus),
            ]

        m.d.comb += self.locator.i_adjust.eq(self.scroller.o_adjusted)

        # TODO: actually flash cursor when on

        command = Signal(8)

        with m.FSM():
            with m.State("INIT: BEGIN"):
                m.d.sync += [
                    self.own_rom_bus.addr.eq(0),
                    self.spifr_bus.addr.eq(self.config.target.flash_rom_base),
                    self.spifr_bus.len.eq(rom.ROM_LENGTH),
                    self.spifr_bus.stb.eq(1),
                ]
                m.next = "INIT: STROBED SPIFR"

            with m.State("INIT: STROBED SPIFR"):
                m.d.sync += self.spifr_bus.stb.eq(0)
                m.next = "INIT: WAIT SPIFR"

            with m.State("INIT: WAIT SPIFR"):
                with m.If(self.spifr_bus.valid):
                    m.d.sync += [
                        self.rom_wr_data.eq(self.spifr_bus.data),
                        self.rom_wr_en.eq(0b1),
                    ]
                    m.next = "INIT: STROBED ROM_WR"
                with m.Elif(~self.spifr_bus.busy):
                    m.d.sync += self.o_result.eq(OLED.Result.SUCCESS)
                    m.next = "IDLE"

            with m.State("INIT: STROBED ROM_WR"):
                m.d.sync += [
                    self.rom_wr_en.eq(0),
                    self.own_rom_bus.addr.eq(
                        Mux(
                            self.own_rom_bus.addr == rom.ROM_LENGTH - 1,
                            0,
                            self.own_rom_bus.addr + 1,
                        )
                    ),
                ]
                m.next = "INIT: WAIT SPIFR"

            with m.State("IDLE"):
                with m.If(self.i_fifo.r_rdy & self.own_i2c_bus.o_in_fifo_w_rdy):
                    m.d.sync += [
                        command.eq(self.i_fifo.r_data),
                        self.i_fifo.r_en.eq(1),
                        self.o_result.eq(OLED.Result.BUSY),
                    ]
                    m.next = "START: STROBED I_FIFO R_EN"

            with m.State("START: STROBED I_FIFO R_EN"):
                m.d.sync += self.i_fifo.r_en.eq(0)
                with m.Switch(command):
                    with m.Case(OLED.Command.NOP):
                        m.d.sync += self.o_result.eq(OLED.Result.SUCCESS)
                        m.next = "IDLE"

                    with m.Case(OLED.Command.INIT):
                        m.d.sync += [
                            self.rom_writer.i_index.eq(rom.OFFSET_INIT),
                            self.rom_writer.i_stb.eq(1),
                            self.row.eq(1),
                            self.col.eq(1),
                            self.scroller.i_rst.eq(1),
                        ]
                        m.next = "INIT: STROBED ROM WRITER"

                    with m.Case(OLED.Command.DISPLAY_ON):
                        m.d.sync += [
                            self.rom_writer.i_index.eq(rom.OFFSET_DISPLAY_ON),
                            self.rom_writer.i_stb.eq(1),
                        ]
                        m.next = "ROM WRITE SINGLE: STROBED ROM WRITER"

                    with m.Case(OLED.Command.DISPLAY_OFF):
                        m.d.sync += [
                            self.rom_writer.i_index.eq(rom.OFFSET_DISPLAY_OFF),
                            self.rom_writer.i_stb.eq(1),
                        ]
                        m.next = "ROM WRITE SINGLE: STROBED ROM WRITER"

                    with m.Case(OLED.Command.CLS):
                        m.d.sync += [
                            self.clser.i_stb.eq(1),
                            self.row.eq(1),
                            self.col.eq(1),
                        ]
                        m.next = "CLSER: STROBED"

                    with m.Case(OLED.Command.LOCATE):
                        m.next = "LOCATE: ROW: WAIT"

                    with m.Case(OLED.Command.PRINT):
                        m.next = "PRINT: COUNT: WAIT"

                    with m.Case(OLED.Command.CURSOR_ON):
                        m.d.sync += [
                            self.cursor.eq(1),
                            self.o_result.eq(OLED.Result.SUCCESS),
                        ]
                        m.next = "IDLE"

                    with m.Case(OLED.Command.CURSOR_OFF):
                        m.d.sync += [
                            self.cursor.eq(0),
                            self.o_result.eq(OLED.Result.SUCCESS),
                        ]
                        m.next = "IDLE"

                    with m.Case(OLED.Command.ID):
                        m.next = "ID: START"

                    with m.Case(OLED.Command.PRINT_BYTE):
                        m.next = "PRINT_BYTE: START"

                    with m.Case(OLED.Command.SPI_TEST):
                        m.next = "SPI_TEST: START"

            self.locate_states(m)
            self.print_states(m)
            self.id_states(m)
            self.print_byte_states(m)
            self.spi_test_states(m)

            with m.State("CLSER: STROBED"):
                m.d.sync += self.clser.i_stb.eq(0)
                m.next = "CLSER: UNSTROBED"

            with m.State("CLSER: UNSTROBED"):
                with m.If(~self.clser.o_busy):
                    m.d.sync += [
                        self.locator.i_row.eq(self.row),
                        self.locator.i_col.eq(self.col),
                        self.locator.i_stb.eq(1),
                    ]
                    m.next = "CLSER: STROBED LOCATOR"

            with m.State("CLSER: STROBED LOCATOR"):
                m.d.sync += self.locator.i_stb.eq(0)
                m.next = "CLSER: UNSTROBED LOCATOR"

            with m.State("CLSER: UNSTROBED LOCATOR"):
                with m.If(~self.locator.o_busy):
                    m.d.sync += self.o_result.eq(OLED.Result.SUCCESS)
                    m.next = "IDLE"

            with m.State("INIT: STROBED ROM WRITER"):
                m.d.sync += [
                    self.rom_writer.i_stb.eq(0),
                    self.scroller.i_rst.eq(0),
                ]
                m.next = "ROM WRITE SINGLE: UNSTROBED ROM WRITER"

            with m.State("ROM WRITE SINGLE: STROBED ROM WRITER"):
                m.d.sync += self.rom_writer.i_stb.eq(0)
                m.next = "ROM WRITE SINGLE: UNSTROBED ROM WRITER"

            with m.State("ROM WRITE SINGLE: UNSTROBED ROM WRITER"):
                with m.If(~self.rom_writer.o_busy):
                    m.d.sync += self.o_result.eq(OLED.Result.SUCCESS)
                    m.next = "IDLE"

        self.chpr_fsm(m)

        return m

    def locate_states(self, m: Module):
        with m.State("LOCATE: ROW: WAIT"):
            with m.If(self.i_fifo.r_rdy):
                with m.If(self.i_fifo.r_data != 0):
                    m.d.sync += [
                        self.row.eq(self.i_fifo.r_data),
                        self.locator.i_row.eq(self.i_fifo.r_data),
                    ]
                with m.Else():
                    m.d.sync += self.locator.i_row.eq(0)
                m.d.sync += self.i_fifo.r_en.eq(1)
                m.next = "LOCATE: ROW: STROBED R_EN"

        with m.State("LOCATE: ROW: STROBED R_EN"):
            m.d.sync += self.i_fifo.r_en.eq(0)
            m.next = "LOCATE: COL: WAIT"

        with m.State("LOCATE: COL: WAIT"):
            with m.If(self.i_fifo.r_rdy):
                with m.If(self.i_fifo.r_data != 0):
                    m.d.sync += [
                        self.col.eq(self.i_fifo.r_data),
                        self.locator.i_col.eq(self.i_fifo.r_data),
                    ]
                with m.Else():
                    m.d.sync += self.locator.i_col.eq(0)
                m.d.sync += [
                    self.i_fifo.r_en.eq(1),
                    self.locator.i_stb.eq(1),
                ]
                m.next = "LOCATE: COL: STROBED R_EN"

        with m.State("LOCATE: COL: STROBED R_EN"):
            m.d.sync += [
                self.i_fifo.r_en.eq(0),
                self.locator.i_stb.eq(0),
            ]
            m.next = "LOCATE: UNSTROBED LOCATOR"

        with m.State("LOCATE: UNSTROBED LOCATOR"):
            with m.If(~self.locator.o_busy):
                m.d.sync += self.o_result.eq(OLED.Result.SUCCESS)
                m.next = "IDLE"

    def print_states(self, m: Module):
        remaining = Signal(8)

        with m.State("PRINT: COUNT: WAIT"):
            with m.If(self.i_fifo.r_rdy):
                m.d.sync += [
                    self.i_fifo.r_en.eq(1),
                    remaining.eq(self.i_fifo.r_data),
                ]
                m.next = "PRINT: COUNT: STROBED R_EN"

        with m.State("PRINT: COUNT: STROBED R_EN"):
            m.d.sync += self.i_fifo.r_en.eq(0)
            with m.If(remaining == 0):
                m.d.sync += self.o_result.eq(OLED.Result.SUCCESS)
                m.next = "IDLE"
            with m.Else():
                m.next = "PRINT: DATA: WAIT"

        with m.State("PRINT: DATA: WAIT"):
            with m.If(self.i_fifo.r_rdy):
                m.d.sync += [
                    self.i_fifo.r_en.eq(1),
                    self.chpr_data.eq(self.i_fifo.r_data),
                    self.chpr_run.eq(1),
                ]
                m.next = "PRINT: DATA: CHPR RUNNING"

        with m.State("PRINT: DATA: CHPR RUNNING"):
            m.d.sync += self.i_fifo.r_en.eq(0)
            with m.If(~self.chpr_run):
                with m.If(remaining == 1):
                    m.d.sync += self.o_result.eq(OLED.Result.SUCCESS)
                    m.next = "IDLE"
                with m.Else():
                    m.d.sync += remaining.eq(remaining - 1)
                    m.next = "PRINT: DATA: WAIT"

    def chpr_fsm(self, m: Module):
        with m.FSM():
            with m.State("IDLE"):
                with m.If(self.chpr_run):
                    with m.If(self.chpr_data == 13):
                        # CR
                        m.d.sync += [
                            self.col.eq(1),
                            self.locator.i_col.eq(1),
                            self.locator.i_row.eq(0),
                            self.locator.i_stb.eq(1),
                        ]
                        m.next = "CHPR: STROBED LOCATOR"
                    with m.Elif(self.chpr_data == 10):
                        # LF
                        with m.If(self.row == 16):
                            m.d.sync += [
                                self.col.eq(1),
                                self.scroller.i_stb.eq(1),
                            ]
                            m.next = "CHPR: STROBED SCROLLER"
                        with m.Else():
                            m.d.sync += [
                                self.col.eq(1),
                                self.row.eq(self.row + 1),
                                self.locator.i_row.eq(self.row + 1),
                                self.locator.i_col.eq(1),
                                self.locator.i_stb.eq(1),
                            ]
                            m.next = "CHPR: STROBED LOCATOR"
                    with m.Else():
                        m.d.sync += [
                            self.rom_writer.i_index.eq(
                                rom.OFFSET_CHAR + self.chpr_data
                            ),
                            self.rom_writer.i_stb.eq(1),
                        ]
                        m.next = "CHPR: STROBED ROM WRITER"

            with m.State("CHPR: STROBED ROM WRITER"):
                m.d.sync += self.rom_writer.i_stb.eq(0)
                with m.If(self.col == 16):
                    with m.If(self.row == 16):
                        m.d.sync += self.col.eq(1)
                        m.next = "CHPR: UNSTROBED ROM WRITER, NEEDS SCROLL"
                    with m.Else():
                        m.d.sync += [
                            self.col.eq(1),
                            self.row.eq(self.row + 1),
                        ]
                        m.next = "CHPR: UNSTROBED ROM WRITER"
                with m.Else():
                    m.d.sync += self.col.eq(self.col + 1)
                    m.next = "CHPR: UNSTROBED ROM WRITER"

            with m.State("CHPR: UNSTROBED ROM WRITER"):
                with m.If(~self.rom_writer.o_busy):
                    m.d.sync += [
                        self.locator.i_row.eq(self.row),
                        self.locator.i_col.eq(self.col),
                        self.locator.i_stb.eq(1),
                    ]
                    m.next = "CHPR: STROBED LOCATOR"

            with m.State("CHPR: UNSTROBED ROM WRITER, NEEDS SCROLL"):
                with m.If(~self.rom_writer.o_busy):
                    m.d.sync += self.scroller.i_stb.eq(1)
                    m.next = "CHPR: STROBED SCROLLER"

            with m.State("CHPR: STROBED SCROLLER"):
                m.d.sync += self.scroller.i_stb.eq(0)
                m.next = "CHPR: UNSTROBED SCROLLER"

            with m.State("CHPR: UNSTROBED SCROLLER"):
                with m.If(~self.scroller.o_busy):
                    m.d.sync += [
                        self.locator.i_row.eq(self.row),
                        self.locator.i_col.eq(self.col),
                        self.locator.i_stb.eq(1),
                    ]
                    m.next = "CHPR: STROBED LOCATOR"

            with m.State("CHPR: STROBED LOCATOR"):
                m.d.sync += self.locator.i_stb.eq(0)
                m.next = "CHPR: UNSTROBED LOCATOR"

            with m.State("CHPR: UNSTROBED LOCATOR"):
                with m.If(~self.locator.o_busy):
                    m.d.sync += self.chpr_run.eq(0)
                    m.next = "IDLE"

    def id_states(self, m: Module):
        # XXX(Ch): hack just to test read capability The hex printing is
        # duplicated in the print_byte states.

        id_recvd = Signal(8)

        with m.State("ID: START"):
            m.d.sync += [
                self.own_i2c_bus.i_in_fifo_w_data.eq(0x178),
                self.own_i2c_bus.i_in_fifo_w_en.eq(1),
            ]
            m.next = "ID: START WRITE: STROBED W_EN"

        with m.State("ID: START WRITE: STROBED W_EN"):
            m.d.sync += [
                self.own_i2c_bus.i_in_fifo_w_en.eq(0),
                self.own_i2c_bus.i_stb.eq(1),
            ]
            m.next = "ID: START WRITE: STROBED I_STB"

        with m.State("ID: START WRITE: STROBED I_STB"):
            m.d.sync += self.own_i2c_bus.i_stb.eq(0)
            m.next = "ID: START WRITE: UNSTROBED I_STB"

        with m.State("ID: START WRITE: UNSTROBED I_STB"):
            with m.If(
                self.own_i2c_bus.o_busy
                & self.own_i2c_bus.o_ack
                & self.own_i2c_bus.o_in_fifo_w_rdy
            ):
                m.d.sync += [
                    self.own_i2c_bus.i_in_fifo_w_data.eq(0x00),  # Command/NC
                    self.own_i2c_bus.i_in_fifo_w_en.eq(1),
                ]
                m.next = "ID: WRITE CMD: STROBED W_EN"
            with m.Elif(~self.own_i2c_bus.o_busy):
                m.d.sync += self.o_result.eq(OLED.Result.FAILURE)
                m.next = "IDLE"

        with m.State("ID: WRITE CMD: STROBED W_EN"):
            m.d.sync += self.own_i2c_bus.i_in_fifo_w_en.eq(0)
            m.next = "ID: WRITE CMD: UNSTROBED W_EN"

        with m.State("ID: WRITE CMD: UNSTROBED W_EN"):
            with m.If(
                self.own_i2c_bus.o_busy
                & self.own_i2c_bus.o_ack
                & self.own_i2c_bus.o_in_fifo_w_rdy
            ):
                m.d.sync += [
                    self.own_i2c_bus.i_in_fifo_w_data.eq(0x179),
                    self.own_i2c_bus.i_in_fifo_w_en.eq(1),
                ]
                m.next = "ID: START READ: STROBED W_EN"
            with m.Elif(~self.own_i2c_bus.o_busy):
                m.d.sync += self.o_result.eq(OLED.Result.FAILURE)
                m.next = "IDLE"

        with m.State("ID: START READ: STROBED W_EN"):
            m.d.sync += self.own_i2c_bus.i_in_fifo_w_en.eq(0)
            m.next = "ID: START READ: UNSTROBED W_EN"

        with m.State("ID: START READ: UNSTROBED W_EN"):
            with m.If(
                self.own_i2c_bus.o_busy
                & self.own_i2c_bus.o_ack
                & self.own_i2c_bus.o_in_fifo_w_rdy
            ):
                m.d.sync += [
                    self.own_i2c_bus.i_in_fifo_w_data.eq(0x00),
                    self.own_i2c_bus.i_in_fifo_w_en.eq(1),
                ]
                m.next = "ID: RECV: WAIT"
            with m.Elif(~self.own_i2c_bus.o_busy):
                m.d.sync += self.o_result.eq(OLED.Result.FAILURE)
                m.next = "IDLE"

        with m.State("ID: RECV: WAIT"):
            m.d.sync += self.own_i2c_bus.i_in_fifo_w_en.eq(0)
            with m.If(self.own_i2c_bus.o_out_fifo_r_rdy):
                m.d.sync += [
                    id_recvd.eq(self.own_i2c_bus.o_out_fifo_r_data),
                    self.own_i2c_bus.i_out_fifo_r_en.eq(1),
                ]
                m.next = "ID: RECV: STROBED R_EN"
            with m.Elif(~self.own_i2c_bus.o_busy):
                m.d.sync += self.o_result.eq(OLED.Result.FAILURE)
                m.next = "IDLE"

        with m.State("ID: RECV: STROBED R_EN"):
            m.d.sync += self.own_i2c_bus.i_out_fifo_r_en.eq(0)
            with m.If(~self.own_i2c_bus.o_busy):
                first_half = id_recvd[4:8]
                m.d.sync += [
                    self.chpr_data.eq(
                        Mux(
                            first_half > 9,
                            ord("A") + first_half - 10,
                            ord("0") + first_half,
                        )
                    ),
                    self.chpr_run.eq(1),
                ]
                m.next = "ID: FIRST HALF: CHPR RUNNING"

        with m.State("ID: FIRST HALF: CHPR RUNNING"):
            with m.If(~self.chpr_run):
                second_half = id_recvd[:4]
                m.d.sync += [
                    self.chpr_data.eq(
                        Mux(
                            second_half > 9,
                            ord("A") + second_half - 10,
                            ord("0") + second_half,
                        )
                    ),
                    self.chpr_run.eq(1),
                ]
                m.next = "ID: SECOND HALF: CHPR RUNNING"

        with m.State("ID: SECOND HALF: CHPR RUNNING"):
            with m.If(~self.chpr_run):
                m.d.sync += self.o_result.eq(OLED.Result.SUCCESS)
                m.next = "IDLE"

    def print_byte_states(self, m: Module):
        second_half = Signal(4)

        with m.State("PRINT_BYTE: START"):
            with m.If(self.i_fifo.r_rdy):
                first_half = self.i_fifo.r_data[4:8]
                m.d.sync += [
                    second_half.eq(self.i_fifo.r_data[:4]),
                    self.i_fifo.r_en.eq(1),
                    self.chpr_data.eq(
                        Mux(
                            first_half > 9,
                            ord("A") + first_half - 10,
                            ord("0") + first_half,
                        )
                    ),
                    self.chpr_run.eq(1),
                ]
                m.next = "PRINT_BYTE: STROBED R_EN, CHPR RUNNING"

        with m.State("PRINT_BYTE: STROBED R_EN, CHPR RUNNING"):
            m.d.sync += self.i_fifo.r_en.eq(0)
            with m.If(~self.chpr_run):
                m.d.sync += [
                    self.chpr_data.eq(
                        Mux(
                            second_half > 9,
                            ord("A") + second_half - 10,
                            ord("0") + second_half,
                        )
                    ),
                    self.chpr_run.eq(1),
                ]
                m.next = "PRINT_BYTE: SECOND HALF: CHPR RUNNING"

        with m.State("PRINT_BYTE: SECOND HALF: CHPR RUNNING"):
            with m.If(~self.chpr_run):
                m.d.sync += self.o_result.eq(OLED.Result.SUCCESS)
                m.next = "IDLE"

    def spi_test_states(self, m: Module):
        TO_READ = 0x20

        m.submodules.spi_test_fifo = fifo = SyncFIFO(width=8, depth=TO_READ)

        second_half = Signal(4)

        with m.State("SPI_TEST: START"):
            m.d.sync += [
                self.spifr_bus.addr.eq(self.config.target.flash_rom_base),
                self.spifr_bus.len.eq(0x100),
                self.spifr_bus.stb.eq(1),
            ]
            m.next = "SPI_TEST: WAIT"

        with m.State("SPI_TEST: WAIT"):
            m.d.sync += [
                self.spifr_bus.stb.eq(0),
                fifo.w_en.eq(0),
            ]
            with m.If(fifo.r_level == TO_READ):
                m.next = "SPI_TEST: WRITE LOOP"
            with m.Elif(self.spifr_bus.valid):
                m.d.sync += [
                    fifo.w_data.eq(self.spifr_bus.data),
                    fifo.w_en.eq(1),
                ]

        with m.State("SPI_TEST: WRITE LOOP"):
            with m.If(fifo.r_rdy):
                first_half = fifo.r_data[4:8]
                m.d.sync += [
                    second_half.eq(fifo.r_data[:4]),
                    fifo.r_en.eq(1),
                    self.chpr_data.eq(
                        Mux(
                            first_half > 9,
                            ord("A") + first_half - 10,
                            ord("0") + first_half,
                        )
                    ),
                    self.chpr_run.eq(1),
                ]
                m.next = "SPI_TEST: STROBED R_EN, CHPR RUNNING"
            with m.Else():
                m.next = "IDLE"

        with m.State("SPI_TEST: STROBED R_EN, CHPR RUNNING"):
            m.d.sync += fifo.r_en.eq(0)
            with m.If(~self.chpr_run):
                m.d.sync += [
                    self.chpr_data.eq(
                        Mux(
                            second_half > 9,
                            ord("A") + second_half - 10,
                            ord("0") + second_half,
                        )
                    ),
                    self.chpr_run.eq(1),
                ]
                m.next = "SPI_TEST: SECOND HALF: CHPR RUNNING"

        with m.State("SPI_TEST: SECOND HALF: CHPR RUNNING"):
            with m.If(~self.chpr_run):
                m.next = "SPI_TEST: WRITE LOOP"
