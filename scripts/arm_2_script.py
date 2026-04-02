"""
Arm 2 Controller — independent brain for robotic arm 2.

Usage:
    python scripts/arm_2_script.py --angle 45
    python scripts/arm_2_script.py --sequence 30 90 150 45
    python scripts/arm_2_script.py --angle 45 --host http://localhost:5000
"""

import argparse
import time
import sys

from utils import send_command, print_result, DEFAULT_HOST

ARM_ID = 2


def main():
    """Parse arguments and execute arm commands."""
    parser = argparse.ArgumentParser(
        description=f'Control Arm {ARM_ID} via the INTER.act API'
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--angle', type=float,
                       help='Single target angle (0-180 degrees)')
    group.add_argument('--sequence', type=float, nargs='+',
                       help='Sequence of target angles (space-separated)')
    parser.add_argument('--host', default=DEFAULT_HOST,
                        help=f'Server URL (default: {DEFAULT_HOST})')
    parser.add_argument('--wait', action='store_true',
                        help='Wait and confirm after each command')

    args = parser.parse_args()

    if args.angle is not None:
        result = send_command(args.host, ARM_ID, args.angle)
        if result:
            print_result(ARM_ID, args.angle)
            if args.wait:
                print('[OK] Confirmed')
        else:
            sys.exit(1)

    elif args.sequence:
        total = len(args.sequence)
        for i, angle in enumerate(args.sequence, 1):
            result = send_command(args.host, ARM_ID, angle)
            if result:
                print_result(ARM_ID, angle, step=i, total=total)
                if args.wait:
                    print('[OK] Confirmed')
            else:
                print(f'Sequence aborted at step {i}/{total}', file=sys.stderr)
                sys.exit(1)
            if i < total:
                time.sleep(1.5)

        print(f'Sequence complete. {total} steps executed.')


if __name__ == '__main__':
    main()
