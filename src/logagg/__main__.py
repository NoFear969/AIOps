"""允许 `python -m logagg` 进入 poll 入口。"""

from logagg.poll import main

if __name__ == "__main__":
    raise SystemExit(main())
