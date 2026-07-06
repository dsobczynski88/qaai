"""Sample-size calculator for a single-model accuracy confidence interval.

Subcommands:
    ci        required N to estimate accuracy to +/- margin at a confidence level
    achieved  half-width (margin) achievable with a given N

Examples:
    uv run python scripts/sample_size.py ci --confidence 0.95 --margin 0.05 --p 0.85
    uv run python scripts/sample_size.py achieved --n 200 --confidence 0.95 --p 0.85
"""
import argparse

from qaai.eval.sample_size import achieved_margin, required_sample_size


def _cmd_ci(args: argparse.Namespace) -> None:
    r = required_sample_size(args.confidence, args.margin, args.p, args.population)
    pop = f", population={args.population}" if args.population else ""
    print(f"Target: accuracy +/-{args.margin} at {args.confidence:.0%} confidence "
          f"(expected p={args.p}{pop})")
    print(f"  z            = {r['z']:.4f}")
    print(f"  n (normal)   = {r['n_normal']}")
    print(f"  n (Wilson)   = {r['n_wilson']}")
    if args.method:
        chosen = r["n_normal"] if args.method == "normal" else r["n_wilson"]
        print(f"  -> use n     = {chosen}  ({args.method})")


def _cmd_achieved(args: argparse.Namespace) -> None:
    hw = achieved_margin(args.n, args.confidence, args.p)
    print(f"With n={args.n} at {args.confidence:.0%} confidence (p={args.p}):")
    print(f"  half-width (normal) = +/-{hw['normal']:.4f}")
    print(f"  half-width (Wilson) = +/-{hw['wilson']:.4f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("ci", help="required N for a target CI")
    c.add_argument("--confidence", type=float, default=0.95)
    c.add_argument("--margin", type=float, required=True, help="desired CI half-width, e.g. 0.05")
    c.add_argument("--p", type=float, default=0.5, help="expected accuracy (0.5 = worst case)")
    c.add_argument("--population", type=int, help="finite population size (optional FPC)")
    c.add_argument("--method", choices=("normal", "wilson"), help="highlight one method's N")
    c.set_defaults(func=_cmd_ci)

    a = sub.add_parser("achieved", help="margin achievable with a given N")
    a.add_argument("--n", type=int, required=True)
    a.add_argument("--confidence", type=float, default=0.95)
    a.add_argument("--p", type=float, default=0.5)
    a.set_defaults(func=_cmd_achieved)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
