"""Validate two generated route sets with installed BIRD, without starting BGP.

Only invokes bird -p against temporary configs. Does not touch the live daemon.
"""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from ru_routes.generate import parse_prefixes, render_bird


def check(config: Path):
    subprocess.run(["bird", "-p", "-c", str(config)], check=True, timeout=15)


def main():
    with TemporaryDirectory(prefix="ru-bird-syntax-") as directory:
        root = Path(directory)
        routes = root / "routes.conf"
        main_config = root / "bird.conf"
        main_config.write_text(
            "router id 192.0.2.13;\n"
            "ipv4 table ru_restricted_table;\n"
            f'include "{routes}";\n',
            encoding="utf-8",
        )
        original = parse_prefixes("8.8.8.0/24\n1.1.1.0/24\n")
        updated = parse_prefixes("8.8.8.0/24\n1.1.1.0/24\n9.9.9.0/24\n")
        for label, networks in (("original", original), ("updated", updated), ("rollback", original)):
            routes.write_text(render_bird(networks), encoding="utf-8")
            check(main_config)
            print(f"OK: BIRD parsed {label} ({len(networks)} prefixes)")
    print("No BIRD daemon, BGP session or Linux route was modified")


if __name__ == "__main__":
    main()
