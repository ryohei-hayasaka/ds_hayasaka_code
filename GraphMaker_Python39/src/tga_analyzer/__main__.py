import sys


if sys.version_info < (3, 9):
    print(
        "GraphMaker Python 3.9互換版の実行にはPython 3.9以上が必要です。\n"
        f"現在のバージョン: {sys.version.split()[0]}"
    )
    raise SystemExit(1)


from .gui import main


if __name__ == "__main__":
    main()
