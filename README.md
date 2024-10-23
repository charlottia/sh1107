# sh1107

This repository is a testbed for exploring [Amaranth] while learning digital
design. It consists of a basic driver for SH1107-type OLEDs over I²C such as the
[Pimoroni 1.12" 128x128 monochrome OLED][Pimoroni OLED], a read/write I²C
controller, plus a simple SPI flash reader. The driver supports commands akin to
old BASIC: `CLS`, `PRINT`, `LOCATE`. The classic IBM 8x8 font is used to this
end.

[Amaranth]: https://github.com/amaranth-lang/amaranth
[Pimoroni OLED]: https://shop.pimoroni.com/products/1-12-oled-breakout

Execute the package to see what it can do:

```console
$ py -m sh1107 -h
usage: sh1107 [-h] {test,formal,build,rom,vsh} ...

positional arguments:
  {test,formal,build,rom,vsh}
    test                run the unit tests and sim tests
    formal              formally verify the design
    build               build the design, and optionally program it
    rom                 build the ROM image, and optionally program it
    vsh                 run the Virtual SH1107

options:
  -h, --help            show this help message and exit
```

The current test deployment targets are:

* iCEBreaker ([Crowd Supply][iCEBreaker on Crowd Supply],
  [1BitSquared][iCEBreaker on 1BitSquared]).
  * Connect PMOD1 A1 to SDA, A2 to SCL.
* OrangeCrab ([1BitSquared][OrangeCrab on 1BitSquared]).
  * Connect the pins named SDA and SCL.
  * The code currently expects you have rev 0.2 with an 85F like I do. It's
    trivial to add support for rev 0.1 and/or the 25F.

[iCEBreaker on Crowd Supply]: https://www.crowdsupply.com/1bitsquared/icebreaker-fpga
[iCEBreaker on 1BitSquared]: https://1bitsquared.com/products/icebreaker
[OrangeCrab on 1BitSquared]: https://1bitsquared.com/products/orangecrab

## Requirements

If no version is specified, the most recent release or the version in your
package manager is probably fine, and if neither concept applies, just try the
latest commit.

For a detailed guide on installing specific versions of some of these tools,
please see [Installing an HDL toolchain from source][notes-0001].  On Nix,
[hdx] packages everything — `nix develop '.?submodules=1'`/`nix-shell` will
use it.

To run at all:

* [Python 3] (3.8+ works; I work on 3.12 beta)
* [Amaranth] ([`d218273`] or later)
* [Board definitions for Amaranth][amaranth-boards]

To run vsh:

* [Zig] (~[`50339f5`] or later)
* [SDL2]

To build and deploy:

* [nextpnr] configured with appropriate flows:
  * [Project IceStorm] for iCEBreaker
  * [Project Trellis] for OrangeCrab
    * [`dfu-util`] to upload the bitstream and ROM

To run formal tests:

* [Yosys] ([`d3ee4eb`] or later)
* [SymbiYosys]
* [Z3] (4.12+ is known to work; 4.8 is known not to)

[notes-0001]: https://lottia.net/notes/0001-hdl-toolchain-source.html
[hdx]: https://github.com/charlottia/hdx
[Python 3]: https://www.python.org
[`d218273`]: https://github.com/amaranth-lang/amaranth/commit/d218273b9b2c6e65b7d92eb0f280306ea9c07ea3
[amaranth-boards]: https://github.com/amaranth-lang/amaranth-boards
[Yosys]: https://github.com/yosyshq/yosys
[`d3ee4eb`]: https://github.com/YosysHQ/yosys/commit/d3ee4eba5b8d68c891f0beb831f19068e08765ed
[SymbiYosys]: https://github.com/YosysHQ/sby
[Z3]: https://github.com/Z3Prover/z3
[nextpnr]: https://github.com/YosysHQ/nextpnr
[Project IceStorm]: https://github.com/YosysHQ/icestorm
[Project Trellis]: https://github.com/YosysHQ/prjtrellis
[`dfu-util`]: https://dfu-util.sourceforge.net/
[Zig]: https://ziglang.org/
[SDL2]: https://libsdl.org/
[`50339f5`]: https://github.com/ziglang/zig/commit/50339f595aa6ec96760b1cd9f8d0e0bfc3f167fc


## TODOs

- try QSPI.
- OrangeCrab: try on-board DDR3 instead of EBR.

## vsh

Maybe the most interesting thing right now is the Virtual SH1107 for testing the
gateware. It emulates the internal state of the SH1107 device — what you see
rendered is what you should see on the display.

[<img alt="screenshot of the Virtual SH1107 testbench" src="doc/vsh.png"
height="300">](doc/vsh.png) [<img alt="photo of the OLED device being run on an
iCEBreaker" src="doc/helloworld.jpg" height="300">](doc/helloworld.jpg)

Initially this was implemented in Python and ran cooperatively with Amaranth's
own simulator, like the unit tests, but it was pretty slow. It's now written in
[Zig], and interacts with the simulated hardware running on its own thread by
compiling it to C++ through Yosys's [CXXRTL backend][CXXRTL].

[CXXRTL]: https://github.com/YosysHQ/yosys/tree/master/backends/cxxrtl

```console
$ py -m sh1107 vsh -h
usage: sh1107 vsh [-h] [-i] [-f] [-c] [-s {100000,400000,2000000}] [-t TOP]
                  [-v] [-O {none,rtl,zig,both}]

options:
  -h, --help            show this help message and exit
  -i, --whitebox-i2c    simulate the full I2C protocol; by default it is
                        replaced with a blackbox for speed
  -f, --whitebox-spifr  simulate the full SPI protocol for the flash reader;
                        by default it is replaced with a blackbox for speed
  -c, --compile         compile only; don't run
  -s {100000,400000,2000000}, --speed {100000,400000,2000000}
                        I2C bus speed to build at
  -t TOP, --top TOP     which top-level module to simulate (default:
                        oled.Top)
  -v, --vcd             output a VCD file
  -O {none,rtl,zig,both}, --optimize {none,rtl,zig,both}
                        build RTL or Zig with optimizations (default: both)
```

### I²C

By default, the I²C circuit is stubbed out with a
[blackbox](vsh/i2c_blackbox.cc) that acts close enough to the real controller
for the rest of the design, and the Virtual SH1107
[spies](vsh/src/I2CBBConnector.zig) on the inputs to the blackbox directly. This
is fast.

At the most fine-grained level (`vsh -i`), it responds to the gateware by doing
edge detection at I²C level, [spying](vsh/src/I2CConnector.zig) on the I²C
lines. This method is faster than the pure Python version I started with, but
still slow enough to take several seconds to clear the screen when not compiled
with optimizations.

### SPI flash

Sequences of SH1107 commands used by the driver are packed into a ROM image
which is separately programmed onto the on-board flash.  The driver reads the
contents into RAM on startup over SPI.

By default, the SPI flash reader component is stubbed out with a
[blackbox](vsh/spifr_blackbox.cc), which emulates the component's
[interface](vsh/spifr_blackbox.il), returning data bytes directly to the OLED
driver from the ROM embedded in the build.

This blackbox can be replaced with a [whitebox](vsh/spifr_whitebox.cc) (`vsh
-f`), which emulates at one level lower, emulating the [SPI
interface](vsh/spifr_whitebox.il) itself, returning data bitwise to the [flash
reader](sh1107/spi/spi_flash_reader.py).
