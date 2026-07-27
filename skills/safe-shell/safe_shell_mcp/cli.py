"""Console entry point for the safe-shell CLI fallback."""

import sys

import safe_shell


def main():
    return safe_shell.main(sys.argv[1:])
