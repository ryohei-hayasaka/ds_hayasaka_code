import sys


if sys.version_info < (3, 9):
    print(
        "GraphMakerの実行にはPython 3.9以上が必要です。\n"
        f"現在のバージョン: {sys.version.split()[0]}"
    )
    raise SystemExit(1)


from .app import main


if __name__ == "__main__":
    main()
