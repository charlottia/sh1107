{
  description = "Development shell for sh1107";

  inputs = {
    flake-utils.url = github:numtide/flake-utils;
    hdx = {
      url = github:charlottia/hdx?ref=stripped;
      inputs.flake-utils.follows = "flake-utils";
    };
    nixpkgs.follows = "hdx/nixpkgs";
    zig = {
      url = github:mitchellh/zig-overlay;
      inputs.nixpkgs.follows = "hdx/nixpkgs";
      inputs.flake-utils.follows = "flake-utils";
    };
  };

  outputs = inputs @ {
    self,
    nixpkgs,
    flake-utils,
    ...
  }: let
    overlays = [
      (final: prev: {
        hdx = inputs.hdx.packages.${prev.system};
        zig-overlay = inputs.zig.packages.${prev.system};
      })
    ];
  in
    flake-utils.lib.eachDefaultSystem (system: let
      pkgs = import nixpkgs {inherit overlays system;};
      hdx = pkgs.hdx.default;
      inherit (pkgs) lib;
      inherit (hdx) python;
      zig =
        if pkgs.stdenv.isDarwin
        then pkgs.zig-overlay.master
        else pkgs.zig;
    in rec {
      formatter = pkgs.alejandra;

      packages.default = python.pkgs.buildPythonPackage {
        name = "sh1107";

        format = "pyproject";
        src = ./.;

        nativeBuildInputs = builtins.attrValues {
          inherit
            (python.pkgs)
            setuptools
            black
            # isort
            
            # python-lsp-server
            
            ;

          inherit
            (pkgs.nodePackages)
            pyright
            ;

          inherit
            (pkgs)
            dfu-util
            ;

          inherit
            hdx
            zig
            ;
        };
        #// lib.optionalAttrs (pkgs.stdenv.isDarwin) {
        # XXX To use frameworks included below.
        # inherit (pkgs.darwin.apple_sdk_11_0) xcodebuild;
        #});

        buildInputs =
          builtins.attrValues {
            inherit
              (pkgs)
              SDL2
              iconv
              ;
            inherit zig;
          }
          # XXX I'm getting issues linking against system (?) QuickLook/QuickLookUI, and I just
          # cbf. Impure all frameworks against system for now.
          # ++ lib.optionals (pkgs.stdenv.isDarwin) (with pkgs.darwin.apple_sdk_11_0.frameworks; [
          #   # CoreHaptics XXX
          #   AppKit
          #   AudioToolbox
          #   Carbon
          #   Cocoa
          #   CoreAudio
          #   CoreGraphics
          #   CoreVideo
          #   ForceFeedback
          #   Foundation
          #   GameController
          #   IOKit
          #   Metal
          #   MetalKit
          #   OpenGL
          #   Quartz
          #   QuartzCore
          #   QuickLook
          # ])
          ;

        dontAddExtraLibs = true;

        preBuild = ''
          export ZIG_GLOBAL_CACHE_DIR="$TMPDIR/zig"
        '';

        doCheck = true;

        pythonImportsCheck = ["sh1107"];

        checkPhase = ''
          BOARDS=( icebreaker orangecrab )
          SPEEDS=( 100000 400000 )
          export CI=1

          set -euo pipefail

          echo "--- Unit tests."
          python -m sh1107 test

          for board in "''${BOARDS[@]}"; do
            for speed in "''${SPEEDS[@]}"; do
              echo "--- Building $board @ $speed."
              python -m sh1107 build "$board" -s "$speed"
            done
          done

          for opts in "-c" "-cfi"; do
            echo "--- Building Virtual SH1107 ($opts)."
            python -m sh1107 vsh "$opts"
            # On Darwin, nix build may appear to fail, but the binary output looks OK.
            # A true failure would exit non-zero?
          done

          echo "--- Formal verification."
          python -m sh1107 formal

          echo "--- All passed."
        '';
      };

      checks.default = packages.default;

      devShells.default = packages.default;
    });
}
