{
  description = "traffic-cam dev environment (PC only; the Pi is not managed by Nix)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python313;
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          python
          pkgs.uv
          pkgs.ruff
          pkgs.pyright
          pkgs.dotnet-sdk_10
          pkgs.just
          pkgs.mosquitto
          # For ingest's tests: the versions on the Pi. The Apache build has hypertables
          # but not continuous aggregates.
          (pkgs.postgresql_18.withPackages (p: [ p.timescaledb-apache ]))
          pkgs.rerun
          pkgs.mpv
          pkgs.ffmpeg
          pkgs.jq
          pkgs.rsync
          pkgs.openssh
        ];

        # Use the Nix interpreter; uv's downloaded Pythons are not wanted here.
        UV_PYTHON = "${python}/bin/python3.13";
        UV_PYTHON_DOWNLOADS = "never";

        # PyPI wheels with native code (numpy, pyarrow) expect these from the system.
        LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
          pkgs.stdenv.cc.cc.lib
          pkgs.zlib
        ];
      };
    };
}
